import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { api, ApiError, type SessionReceipt } from "@/lib/api";
import { ReceiptCard } from "@/components/ReceiptCard";
import { Eyebrow } from "@/components/Eyebrow";
import { StaggerHeading } from "@/components/StaggerHeading";

export function Evidence() {
  const [searchParams] = useSearchParams();
  const [audit, setAudit] = useState<any>(null);
  const [sessionId, setSessionId] = useState(searchParams.get("session") ?? "");
  const [receipt, setReceipt] = useState<SessionReceipt | null>(null);
  const [lookupError, setLookupError] = useState("");

  useEffect(() => {
    api.auditVerify().then(setAudit);
  }, []);

  async function lookup(id?: string) {
    const target = (id ?? sessionId).trim();
    if (!target) return;
    setReceipt(null);
    setLookupError("");
    try {
      setReceipt(await api.sessionReceipt(target));
    } catch (err) {
      setLookupError(err instanceof ApiError ? err.message : String(err));
    }
  }

  // Deep link from the Shop page's "View Evidence Receipt" button — look it
  // up immediately rather than making the user paste it in again.
  useEffect(() => {
    const fromUrl = searchParams.get("session");
    if (fromUrl) lookup(fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-col gap-8 max-w-4xl">
      <div>
        <Eyebrow>Evidence</Eyebrow>
        <StaggerHeading
          as="h1" text="Why it was allowed — or why it wasn't."
          accent={["allowed"]}
          className="text-3xl md:text-4xl leading-[1.15] mt-2 mb-3"
        />
        <p className="text-sm text-muted-foreground mt-2 max-w-xl">
          The audit chain is independently re-verified on every load — every hash is recomputed from
          stored content, never trusted.
        </p>
      </div>

      <Card className="p-5">
        <h2 className="text-sm font-medium mb-2">Tamper-evident audit chain</h2>
        {audit && (
          <div className="flex items-center gap-2 mb-4">
            <Badge variant={audit.verified ? "default" : "destructive"}>{audit.verified ? "✓ chain intact" : "✗ tamper detected"}</Badge>
            <span className="text-xs text-muted-foreground">
              {audit.verified ? `${audit.event_count} event(s) recorded` : `broke at seq=${audit.broken_at_seq}`}
            </span>
          </div>
        )}
        {audit?.events && (
          <div className="flex flex-col gap-1.5 max-h-[320px] overflow-y-auto">
            {[...audit.events].reverse().map((e: any) => (
              <div key={e.seq} className="flex items-center justify-between text-xs py-1.5 border-b border-border/40 last:border-0">
                <span className="font-medium">{e.event_type}</span>
                <span className="text-muted-foreground">
                  seq {e.seq} · {e.created_at}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card className="p-5">
        <h2 className="text-sm font-medium mb-1">Look up a purchase receipt</h2>
        <p className="text-xs text-muted-foreground mb-3">
          Gates, signed authorization, Razorpay ledger state and the audit chain — assembled from a
          FreshCart checkout session, re-verified live, never read from a cached flag.
        </p>
        <div className="flex gap-2 mb-4">
          <Input placeholder="Paste a session id from the checkout flow…" value={sessionId} onChange={(e) => setSessionId(e.target.value)} />
          <button onClick={() => lookup()} className="px-3 py-1.5 rounded-full text-xs font-medium bg-primary text-primary-foreground shrink-0">
            Look up
          </button>
        </div>
        {lookupError && <p className="text-sm text-muted-foreground">{lookupError}</p>}
      </Card>

      {receipt && <ReceiptCard receipt={receipt} />}
    </div>
  );
}
