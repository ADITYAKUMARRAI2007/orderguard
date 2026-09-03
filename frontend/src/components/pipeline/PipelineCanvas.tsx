import { useRef } from "react";
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import type { ConnectorResult, MissionStep } from "@/lib/api";
import { PipelineNode, type NodeStatus } from "./PipelineNode";
import { VeinConnector, type VeinStatus } from "./VeinConnector";
import { OfferApproval } from "./OfferApproval";

// The node graph, driven by real MissionStep results accumulated across the
// whole conversation (Mission.tsx owns and appends to that array — this
// component never clears or replaces it, so a step already drawn here stays
// drawn). Every node maps to an actual stage that ran inside
// agent/orchestrator.py for THAT step — capability routing, connector
// eligibility, budget/preference extraction, the tool call, normalization,
// the Decision Council, and the audit-chain write. A feature that did not
// run for a step (e.g. no budget stated, no commerce offers to rank)
// produces no node claiming it did — this list is assembled per-step from
// what the response actually contains, never a fixed template.

interface Props {
  steps: MissionStep[];
  runtime: string;
  running: boolean;
  pendingCategories?: string[];
}

interface NodeSpec {
  kind: string;
  value: string;
  status: NodeStatus;
  meta?: string;
  wide?: boolean;
}

function veinStatus(s: NodeStatus): VeinStatus {
  if (s === "ok") return "active";
  if (s === "blocked") return "blocked";
  if (s === "running" || s === "paused") return "pending";
  return "idle";
}

function money(paise: number): string {
  return `₹${(paise / 100).toFixed(paise % 100 === 0 ? 0 : 2)}`;
}

function resultOf(step: MissionStep, r: ConnectorResult | undefined): { value: string; meta: string; status: NodeStatus } {
  if (!r) {
    // A step can genuinely succeed with zero tool-call results — the model
    // made no call, or asked a clarifying question instead. Surfacing its
    // own text here is what makes that legible instead of indistinguishable
    // from "nothing happened."
    if (step.model_text) {
      return { value: "PAUSED — REPLY TO CONTINUE", meta: step.model_text.slice(0, 60), status: "paused" };
    }
    return { value: "—", meta: "", status: step.connector_id ? "idle" : "blocked" };
  }
  const p = r.payload;
  if (p.result_type === "commerce_candidates")
    return { value: `${p.offers.length} OFFER(S)`, meta: `MERCHANT: ${p.merchant}`, status: "ok" };
  if (p.result_type === "dev_task")
    return { value: `${p.items.length} ITEM(S)`, meta: `SOURCE: ${p.source}`, status: "ok" };
  if (p.result_type === "unsupported")
    return { value: "UNSUPPORTED", meta: p.reason.slice(0, 44), status: "blocked" };
  return { value: p.result_type.toUpperCase(), meta: "", status: "ok" };
}

// Every genuinely-run feature for this step, in the order it actually
// executed. Nothing here is invented: PREFERENCE only appears for a
// commerce step that actually reached a search, COUNCIL only when there
// were commerce offers to rank, AUDIT always (every intent really does
// write agent_intent_parsed — see app.py).
function nodesFor(step: MissionStep): NodeSpec[] {
  const hasConnector = !!step.connector_id;
  const wasEligible = step.eligible_connector_ids.length > 0;
  const isCommerce = step.category.startsWith("COMMERCE");
  const connectorNode: NodeSpec = hasConnector
    ? { kind: "CONNECTOR", value: step.connector_id!, status: "ok", meta: "ELIGIBILITY PASSED" }
    : wasEligible
      // Eligible AND authenticated — the model just hasn't called it yet
      // this turn (it asked a clarifying question first). "NOT
      // AUTHENTICATED" here would be a real, checkable lie.
      ? { kind: "CONNECTOR", value: step.eligible_connector_ids.join(", "), status: "paused", meta: "ELIGIBLE — NOT CALLED YET" }
      : { kind: "CONNECTOR", value: "NONE ELIGIBLE", status: "blocked", meta: "NOT AUTHENTICATED" };
  const nodes: NodeSpec[] = [
    { kind: "CAPABILITY", value: step.category, status: "ok" },
    connectorNode,
  ];

  // Ground truth on what was actually searched, computed server-side from
  // real tool_calls — never from the model's own text. Real, live-found
  // gap (FAILURE_LOG.md F-041): with more than one eligible connector, a
  // turn's own reply is not reliable evidence of what it searched — it
  // once claimed a fully-connected connector was "disconnected" when it
  // had simply never called it. Only shown when eligibility offered more
  // than got attempted, so a normal single-connector turn stays uncluttered.
  const skipped = step.eligible_connector_ids.filter(
    (id) => !step.attempted_connector_ids.includes(id),
  );
  if (hasConnector && skipped.length > 0) {
    nodes.push({
      kind: "COVERAGE",
      value: `NOT SEARCHED: ${skipped.join(", ").toUpperCase()}`,
      status: "paused",
      meta: `ACTUALLY SEARCHED: ${step.attempted_connector_ids.join(", ").toUpperCase()} — DON'T TRUST THE REPLY TEXT ALONE`,
      wide: true,
    });
  }

  if (isCommerce && hasConnector) {
    nodes.push(
      step.budget_minor != null
        ? {
            kind: "PREFERENCE", value: `${money(step.budget_minor)} BUDGET`, status: "ok",
            meta: "COUNCIL CAN NOW PREFER AN OFFER",
          }
        : {
            kind: "PREFERENCE", value: "NONE STATED", status: "idle",
            meta: "COUNCIL WON'T PREFER ANY OFFER",
          },
    );
  }

  if (hasConnector) {
    // One TOOL CALL / RESULT pair per real result — a turn that searched
    // more than once (e.g. an image-derived list with several items) gets
    // a fully visible chain instead of everything past the first call
    // being silently dropped from the trace.
    const calls = step.results.length ? step.results : [undefined];
    for (const call of calls) {
      nodes.push({
        kind: "TOOL CALL", value: call?.operation ?? "—", status: call ? "ok" : "idle",
        meta: call ? `RISK ${call.risk_tier} · R3 EXCLUDED` : undefined,
      });
      const result = resultOf(step, call);
      nodes.push({ kind: "RESULT", value: result.value, status: result.status, meta: result.meta, wide: true });
    }
  }

  if (step.council) {
    const c = step.council;
    const picked = c.recommended_id
      ? c.eligible.find((e) => e.candidate_id === c.recommended_id)
      : null;
    nodes.push({
      kind: "COUNCIL",
      value: picked ? picked.title.slice(0, 26) : "NONE QUALIFIED",
      status: picked ? "ok" : "idle",
      meta: c.fallback_used ? `FALLBACK · ${c.rationale.slice(0, 50)}` : c.rationale.slice(0, 56),
      wide: true,
    });
  }

  nodes.push({
    kind: "AUDIT", value: "LOGGED", status: "ok",
    meta: `INTENT ${step.intent_id.slice(0, 8)} · HASH-CHAINED`,
  });

  return nodes;
}

/** Pointer-parallax depth. Kept small (≤5deg) — enough to feel physical,
 *  not enough to disorient, per the skill's `parallax-subtle` rule. */
function Tilt({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const rotateX = useSpring(useTransform(y, [-0.5, 0.5], [4, -4]), { stiffness: 140, damping: 18 });
  const rotateY = useSpring(useTransform(x, [-0.5, 0.5], [-5, 5]), { stiffness: 140, damping: 18 });

  return (
    <motion.div
      ref={ref}
      style={{ perspective: 1600 }}
      onPointerMove={(e) => {
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        const rect = ref.current?.getBoundingClientRect();
        if (!rect) return;
        x.set((e.clientX - rect.left) / rect.width - 0.5);
        y.set((e.clientY - rect.top) / rect.height - 0.5);
      }}
      onPointerLeave={() => {
        x.set(0);
        y.set(0);
      }}
    >
      <motion.div style={{ rotateX, rotateY, transformStyle: "preserve-3d" }}>{children}</motion.div>
    </motion.div>
  );
}

export function PipelineCanvas({ steps, runtime, running, pendingCategories = [] }: Props) {
  return (
    <Tilt>
      <section
        className="grid-surface border-2 border-border bg-card/60 hard-lg min-h-[460px] relative"
        aria-label="Mission pipeline"
      >
        <header className="border-b-2 border-border px-4 py-2.5 flex items-center gap-3 bg-card">
          <span className="label-micro text-signal">MISSION TRACE</span>
          {runtime && <span className="label-micro text-muted-foreground">RUNTIME: {runtime}</span>}
          {running && (
            <span className="label-micro text-warn flex items-center gap-1.5">
              <motion.span
                className="inline-block size-1.5 bg-warn"
                animate={{ opacity: [1, 0.2, 1] }}
                transition={{ duration: 0.8, repeat: Infinity }}
              />
              EXECUTING
            </span>
          )}
          <span className="ml-auto label-micro text-muted-foreground hidden sm:inline">
            {steps.length} INTENT{steps.length === 1 ? "" : "S"}
          </span>
        </header>

        <div className="p-5 flex flex-col gap-5">
          {!steps.length && !running && (
            <div className="min-h-[320px] grid place-items-center text-center px-6">
              <div>
                <div className="label-micro text-muted-foreground mb-3">AWAITING INSTRUCTION</div>
                <p className="text-sm text-muted-foreground max-w-md leading-relaxed">
                  Send a request. Every node drawn here is a real connector call through the agent
                  orchestrator — capability routed, eligibility checked, result normalized.
                </p>
              </div>
            </div>
          )}

          {/* Previously-completed or paused steps stay exactly as they
              rendered — keyed by intent_id (a fresh one per real backend
              call), never array index, so a step that hasn't changed never
              remounts and never replays its entrance animation. Only the
              slot that actually just resolved (paused -> answered) gets a
              new key and animates in, in place, rather than the whole trace
              resetting to node zero. */}
          {steps.map((step) => {
            const nodes = nodesFor(step);
            // Every commerce result in this step gets its own picker — not
            // just the first. A turn that searched more than once (multiple
            // items in one request, or an image-derived list) previously
            // made every offer past the first invisible.
            const commerceResults: ConnectorResult[] = step.connector_id
              ? step.results.filter((r) => r.payload.result_type === "commerce_candidates")
              : [];
            return (
              <div key={step.intent_id} className="flex flex-col gap-1.5">
                {step.duration_ms > 0 && (
                  <span className="label-micro text-muted-foreground">
                    {(step.duration_ms / 1000).toFixed(1)}s
                  </span>
                )}
                <motion.div
                  className="flex items-center flex-wrap gap-y-2"
                  initial="hidden"
                  animate="show"
                  variants={{ show: { transition: { staggerChildren: 0.07 } } }}
                >
                  {nodes.map((n, ni) => (
                    <div key={`${n.kind}-${ni}`} className="flex items-center">
                      {ni > 0 && <VeinConnector status={veinStatus(n.status)} />}
                      <PipelineNode
                        kind={n.kind} value={n.value} status={n.status} index={ni}
                        meta={n.meta} wide={n.wide}
                      />
                    </div>
                  ))}
                </motion.div>
                {commerceResults.map((call) => (
                  <OfferApproval
                    key={call.execution_id}
                    connectorId={step.connector_id!}
                    result={call}
                    council={step.council}
                  />
                ))}
              </div>
            );
          })}

          {/* A new request in flight — appended after whatever is already
              settled above, never replacing it. */}
          {running &&
            pendingCategories.map((category, i) => (
              <motion.div key={`pending-${i}`} className="flex items-center flex-wrap gap-y-2" initial="hidden" animate="show">
                <PipelineNode kind="CAPABILITY" value={category} status="ok" index={0} />
                <VeinConnector status="active" />
                <PipelineNode kind="CONNECTOR" value="RESOLVING…" status="running" index={1} />
                <VeinConnector status="idle" />
                <PipelineNode kind="TOOL CALL" value="…" status="idle" index={2} />
              </motion.div>
            ))}
        </div>

        <footer className="mt-auto border-t-2 border-border px-4 py-2.5 bg-card">
          <p className="label-micro text-muted-foreground leading-relaxed">
            <span className="text-signal">◉</span> NO NODE ABOVE CAN MOVE MONEY OR WRITE A CART — R3 TOOLS NEVER
            ENTER THE MODEL&apos;S TOOL LIST
          </p>
        </footer>
      </section>
    </Tilt>
  );
}
