import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { Eyebrow } from "@/components/Eyebrow";
import { StaggerHeading } from "@/components/StaggerHeading";
import { CompareBars } from "@/components/CompareBars";

export function AttackLab() {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    api.evalResults().then(setData);
  }, []);

  if (!data?.generated_at) {
    return <Empty note={data?.note ?? "Loading…"} hint="Run `make eval` from the repo root, then reload." />;
  }

  const fifty = data.fixed_fifty;
  const lab = data.attack_lab;
  const agentLab = data.agent_attack_lab;
  const baselines = data.baselines ?? [];

  return (
    <div className="flex flex-col gap-8 max-w-4xl">
      <div>
        <Eyebrow>Hostile Attack Lab</Eyebrow>
        <StaggerHeading
          as="h1" text="What happens when the agent goes wrong."
          accent={["wrong."]}
          className="text-3xl md:text-4xl leading-[1.15] mt-2 mb-3"
        />
        <p className="text-sm text-muted-foreground mt-2 max-w-xl">
          Every number below comes from <code>results/latest.json</code>, written by <code>make eval</code>{" "}
          running the real gate code. Nothing here is a simulation of the guard; it is the guard.
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="False-match (fixed 50)" value={pct(fifty.false_match_rate)} good={fifty.false_match_rate === 0} />
        <Stat label="False-block (fixed 50)" value={pct(fifty.false_block_rate)} good={fifty.false_block_rate === 0} />
        <Stat label="False-match (hostile lab)" value={pct(lab.false_match_rate)} good={lab.false_match_rate === 0} />
        <Stat label="p95 gate latency" value={`${fifty.p95_ms.toFixed(2)}ms`} good />
      </div>

      <ScenarioList title={`Hostile scenarios (${lab.total})`} note="Beyond the fixed fifty — kept separate so the cited count never drifts." items={lab.scenarios.map((s: any) => ({ kind: s.kind, correct: s.correct, note: s.note }))} />

      {agentLab && (
        <ScenarioList
          title={`Agent-layer attacks (${agentLab.total})`}
          note="A different layer: these attack the orchestrator's own invariants (tool exposure, eligibility, SSRF, mission independence)."
          items={agentLab.scenarios.map((s: any) => ({ kind: s.kind, correct: s.correct, note: s.note }))}
        />
      )}

      <div>
        <h2 className="text-sm font-medium mb-1">Baselines</h2>
        <p className="text-xs text-muted-foreground mb-4">
          Same fixed-fifty scenarios, three configurations. Unsafe acceptance rate — how often a
          corrupted cart was wrongly allowed to pay.
        </p>
        <Card className="p-5 max-w-xl">
          <CompareBars
            data={baselines.map((b: any) => ({
              label: b.name,
              value: b.unsafe_acceptance_rate * 100,
              displayValue: `${Math.round(b.unsafe_acceptance_rate * 100)}% unsafe`,
              highlight: b.name === "orderguard",
            }))}
          />
        </Card>
      </div>
      <p className="text-xs text-muted-foreground">Generated {data.generated_at}</p>
    </div>
  );
}

function ScenarioList({ title, note, items }: { title: string; note: string; items: { kind: string; correct: boolean; note: string }[] }) {
  return (
    <div>
      <h2 className="text-sm font-medium mb-1">{title}</h2>
      <p className="text-xs text-muted-foreground mb-3">{note}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {items.map((s, i) => (
          <Card key={i} className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-medium">{label(s.kind)}</span>
              <Badge variant={s.correct ? "default" : "destructive"}>{s.correct ? "✓ handled safely" : "✗ unsafe"}</Badge>
            </div>
            <p className="text-xs text-muted-foreground">{s.note}</p>
          </Card>
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, good }: { label: string; value: string; good: boolean }) {
  return (
    <Card className="p-4">
      <div className={`text-2xl font-semibold ${good ? "text-primary" : "text-destructive"}`}>{value}</div>
      <div className="text-xs text-muted-foreground mt-1">{label}</div>
    </Card>
  );
}

function Empty({ note, hint }: { note: string; hint: string }) {
  return (
    <div className="text-sm text-muted-foreground">
      {note} {hint}
    </div>
  );
}

function pct(x: number) {
  return `${Math.round(x * 100)}%`;
}

function label(kind: string) {
  return kind.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}
