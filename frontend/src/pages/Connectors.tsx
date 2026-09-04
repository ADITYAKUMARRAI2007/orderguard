import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { api, ApiError, type AgentConnector, type ClaudeCodeConnector, type RuntimeStatus } from "@/lib/api";
import { Eyebrow } from "@/components/Eyebrow";
import { StaggerHeading } from "@/components/StaggerHeading";

const MANUAL_TOKEN_CONNECTORS = new Set(["github"]);

function timeAgo(iso: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function Connectors() {
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [byokInput, setByokInput] = useState("");
  const [connectors, setConnectors] = useState<AgentConnector[]>([]);
  const [cliConnectors, setCliConnectors] = useState<ClaudeCodeConnector[]>([]);
  const [cliError, setCliError] = useState("");
  const [cliLoading, setCliLoading] = useState(true);
  const [tokenInputs, setTokenInputs] = useState<Record<string, string>>({});
  const [customLabel, setCustomLabel] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [customResult, setCustomResult] = useState("");

  async function refresh() {
    // Independent, not a serial await chain — one fetch's error can never
    // block or hide another's success.
    try {
      setRuntime(await api.runtimeStatus());
    } catch (err) {
      console.error("Failed to load runtime status", err);
    }
    try {
      setConnectors((await api.connectors()).connectors);
    } catch (err) {
      console.error("Failed to load registered connectors", err);
    }
    // claude-code-connectors calls the real `claude mcp list` CLI, which
    // runs a live health check against every connected server — a genuine
    // 3-8s wait, not a stale request. Without cliLoading, that wait looked
    // identical to "confirmed zero connectors," which read as broken during
    // testing when it was actually just still checking.
    setCliLoading(true);
    try {
      const cc = await api.claudeCodeConnectors();
      setCliConnectors(cc.connectors);
      setCliError(cc.error);
    } catch (err) {
      setCliError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setCliLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function setMode(mode: "api" | "subscription") {
    setRuntime(await api.setRuntimeMode(mode));
  }

  async function submitByok() {
    if (!byokInput.trim()) return;
    setRuntime(await api.setByokKey(byokInput.trim()));
    setByokInput("");
  }

  async function connectManual(id: string) {
    const token = tokenInputs[id]?.trim();
    if (!token) return;
    try {
      await api.connectWithToken(id, token);
      await refresh();
    } catch (err) {
      alert(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function connectOAuth(id: string) {
    try {
      const { authorize_url } = await api.connect(id);
      window.location.href = authorize_url;
    } catch (err) {
      alert(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function addCustom() {
    if (!customLabel.trim() || !customUrl.trim()) return;
    try {
      const row = await api.addCustomConnector(customLabel.trim(), customUrl.trim());
      setCustomResult(`Registered as id ${row.id}. Discovered tools are stored disabled until enabled explicitly.`);
      setCustomLabel("");
      setCustomUrl("");
    } catch (err) {
      setCustomResult(`Refused: ${err instanceof ApiError ? err.message : String(err)}`);
    }
  }

  return (
    <div className="flex flex-col gap-8 max-w-4xl">
      <div>
        <Eyebrow>Connectors</Eyebrow>
        <StaggerHeading
          as="h1" text="What the agent can actually reach."
          accent={["actually"]}
          className="text-3xl md:text-4xl leading-[1.15] mt-2 mb-3"
        />
        <p className="text-sm text-muted-foreground mt-2 max-w-2xl">
          Capability-first, not brand-based: every connector is classified by evidence tier and
          backend type before the orchestrator can route to it. No R3 (financial) tool ever appears
          in a connector's tool list — enforced in code, not just documented.
        </p>
      </div>

      <Card className="p-5">
        <h2 className="text-sm font-medium mb-3">Runtime</h2>
        {runtime && (
          <div className="grid grid-cols-2 gap-3 text-sm mb-4">
            <StatusRow label="Server-managed API key (.env)" ok={runtime.server_managed_api_key} />
            <StatusRow label="BYOK session API key" ok={runtime.byok_session_api_key} />
            <StatusRow label="Subscription runtime (Agent SDK)" ok={runtime.subscription_runtime} />
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Active runtime</span>
              <Badge>{runtime.active_agent_runtime}</Badge>
            </div>
          </div>
        )}
        <div className="flex gap-2 mb-3">
          <button onClick={() => setMode("api")} className="px-3 py-1.5 rounded-full text-xs font-medium bg-secondary hover:bg-secondary/70">
            Use API runtime
          </button>
          <button onClick={() => setMode("subscription")} className="px-3 py-1.5 rounded-full text-xs font-medium bg-secondary hover:bg-secondary/70">
            Use subscription runtime
          </button>
        </div>
        <div className="flex gap-2">
          <Input type="password" placeholder="Paste an Anthropic API key (BYOK)…" value={byokInput} onChange={(e) => setByokInput(e.target.value)} />
          <button onClick={submitByok} className="px-3 py-1.5 rounded-full text-xs font-medium bg-primary text-primary-foreground shrink-0">
            Use this key
          </button>
          <button onClick={async () => setRuntime(await api.forgetByokKey())} className="px-3 py-1.5 rounded-full text-xs font-medium bg-secondary shrink-0">
            Forget
          </button>
        </div>
      </Card>

      <div>
        <h2 className="text-sm font-medium mb-1">Connected via your Claude account</h2>
        <p className="text-xs text-muted-foreground mb-3">
          Detected live from <code>claude mcp list</code> on this machine — read-only status, never a
          credential.
        </p>
        {cliLoading && (
          <div className="text-sm text-muted-foreground mb-3">
            Checking `claude mcp list` — a live health check per server, usually a few seconds…
          </div>
        )}
        {!cliLoading && cliError && <div className="text-sm text-muted-foreground mb-3">{cliError}</div>}
        {!cliLoading && !cliError && cliConnectors.length === 0 && (
          <div className="text-sm text-muted-foreground mb-3">No connectors detected on this machine.</div>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          {cliConnectors.map((c, i) => (
            <motion.div key={c.name} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}>
              <Card className="p-4">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium text-sm">{c.name}</span>
                  <Badge variant={c.connected ? "default" : "secondary"}>{c.connected ? "✔ Connected" : c.status_text}</Badge>
                </div>
                <div className="text-xs text-muted-foreground truncate">{c.url}</div>
                {c.usable_by_orderguard && <Badge className="mt-2">usable here — subscription runtime</Badge>}
              </Card>
            </motion.div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-sm font-medium mb-3">Registered connectors</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {connectors.map((c, i) => (
            <motion.div key={c.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}>
              <Card className="p-4 flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm">{c.label}</span>
                  <Badge variant={c.status === "CONNECTED" ? "default" : "secondary"}>{c.status}</Badge>
                </div>
                {/* Real, verified MCP handshake status from the last actual
                    mission turn — never inferred from token presence alone.
                    See FAILURE_LOG.md F-044's fourth addendum: this page
                    kept showing "CONNECTED" above the whole time a real
                    connector was verifiably failing every turn, because
                    nothing here checked live connection health until now.
                    Shown only once we actually have real evidence — a
                    connector never attempted this deployment says nothing
                    here rather than implying a status we don't know. */}
                {c.mcp_verified_status && (
                  <div className="flex items-center gap-1.5">
                    <Badge variant={c.mcp_verified_status === "connected" ? "default" : "destructive"} className="text-[10px]">
                      MCP {c.mcp_verified_status === "connected" ? "VERIFIED LIVE" : "HANDSHAKE FAILED"}
                    </Badge>
                    {c.mcp_verified_checked_at && (
                      <span className="text-[10px] text-muted-foreground">
                        checked {timeAgo(c.mcp_verified_checked_at)}
                      </span>
                    )}
                  </div>
                )}
                <div className="text-xs text-muted-foreground">
                  {c.category} · {c.backend_type} · evidence: {c.evidence}
                </div>
                <p className="text-xs text-muted-foreground">{c.note}</p>
                <div className="flex flex-wrap gap-1">
                  {c.routable ? (
                    c.tools.map((t) => (
                      <Badge key={t.name} variant="outline" className="text-[10px]">
                        {t.name} · {t.risk_tier}
                      </Badge>
                    ))
                  ) : (
                    <Badge variant="outline" className="text-[10px]">
                      TOOLS NOT YET CLASSIFIED — OFFERS THE MODEL NOTHING
                    </Badge>
                  )}
                </div>
                {c.auth === "connector_account" && c.status !== "CONNECTED" && (
                  MANUAL_TOKEN_CONNECTORS.has(c.id) ? (
                    <div className="flex gap-2 mt-1">
                      <Input
                        type="password"
                        placeholder="Personal access token…"
                        value={tokenInputs[c.id] ?? ""}
                        onChange={(e) => setTokenInputs((s) => ({ ...s, [c.id]: e.target.value }))}
                      />
                      <button onClick={() => connectManual(c.id)} className="px-3 py-1.5 rounded-full text-xs font-medium bg-primary text-primary-foreground shrink-0">
                        Save
                      </button>
                    </div>
                  ) : (
                    <button onClick={() => connectOAuth(c.id)} className="self-start px-3 py-1.5 rounded-full text-xs font-medium bg-primary text-primary-foreground">
                      Connect
                    </button>
                  )
                )}
              </Card>
            </motion.div>
          ))}
        </div>
      </div>

      <Card className="p-5">
        <h2 className="text-sm font-medium mb-1">Add a custom MCP connector</h2>
        <p className="text-xs text-muted-foreground mb-3">
          HTTPS only. Private, loopback and link-local addresses are rejected before any connection.
          Discovered tools are stored disabled until explicitly enabled — never R3.
        </p>
        <div className="flex gap-2">
          <Input placeholder="Label" value={customLabel} onChange={(e) => setCustomLabel(e.target.value)} className="max-w-[160px]" />
          <Input placeholder="https://example.com/mcp" value={customUrl} onChange={(e) => setCustomUrl(e.target.value)} />
          <button onClick={addCustom} className="px-3 py-1.5 rounded-full text-xs font-medium bg-primary text-primary-foreground shrink-0">
            Register
          </button>
        </div>
        {customResult && <p className="text-xs text-muted-foreground mt-2">{customResult}</p>}
      </Card>
    </div>
  );
}

function StatusRow({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <Badge variant={ok ? "default" : "secondary"}>{ok ? "✓ configured" : "not configured"}</Badge>
    </div>
  );
}
