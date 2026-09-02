import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { ReceiptStatus, SessionReceipt } from "@/lib/api";

// Renders what app.py::session_receipt assembles from four already-real
// systems (checkout_guard's 13 gates, authorization.py's signed receipt,
// ledger.py's Razorpay state, audit.py's hash chain) — nothing here is
// computed client-side; every value is exactly what the server independently
// re-verified when this was fetched.

const STATUS_COPY: Record<ReceiptStatus, { label: string; variant: "default" | "destructive" | "secondary" | "outline" }> = {
  PAID: { label: "✓ PAID — money moved, verified independently", variant: "default" },
  BLOCKED: { label: "✕ BLOCKED — no authorization was issued, Razorpay was never called", variant: "destructive" },
  AWAITING_PAYMENT: { label: "GATES PASSED — awaiting payment", variant: "secondary" },
  NOT_CONFIRMED: { label: "NOT YET CONFIRMED", variant: "outline" },
};

function money(paise: number | null, currency: string): string {
  if (paise == null) return "—";
  return `${currency} ${(paise / 100).toFixed(2)}`;
}

export function ReceiptCard({ receipt }: { receipt: SessionReceipt }) {
  const status = STATUS_COPY[receipt.status];

  return (
    <Card className="p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="text-xs uppercase tracking-wider text-muted-foreground">Evidence Receipt</div>
          <div className="text-sm font-mono text-muted-foreground">{receipt.session_id}</div>
        </div>
        <Badge variant={status.variant}>{status.label}</Badge>
      </div>

      <p className="text-sm text-muted-foreground italic">"{receipt.request_text}"</p>

      {receipt.merchant && (
        <div className="text-sm">
          <span className="text-muted-foreground">Merchant: </span>
          <span className="font-medium">
            {receipt.merchant === "freshcart" ? "FreshCart" : receipt.merchant}
          </span>
          {receipt.merchant === "freshcart" && (
            <span className="text-muted-foreground">
              {" "}— OrderGuard's own reference merchant, not a real store. It exists so the real
              Razorpay payment below could run against a merchant OrderGuard actually owns —
              see the authorization/payment sections for what "real" means here.
            </span>
          )}
        </div>
      )}

      {receipt.items.length > 0 && (
        <div className="flex flex-col gap-1">
          {receipt.items.map((item, i) => (
            <div key={i} className="text-sm flex justify-between border-b border-border/40 pb-1">
              <span>{item.title || item.requested_as} × {item.quantity}</span>
              {item.unit_price_paise != null && (
                <span className="text-muted-foreground">
                  {money(item.unit_price_paise * item.quantity, receipt.payment?.currency ?? "INR")}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Gates */}
      <div>
        <div className="text-xs font-medium mb-2">
          {receipt.gates.evaluated
            ? `Deterministic gates — ${receipt.gates.passed.length}/${receipt.gates.passed.length + receipt.gates.failed.length} passed`
            : "Deterministic gates — not yet reached (cart not confirmed)"}
        </div>
        {receipt.gates.evaluated && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-1 text-xs">
            {receipt.gates.passed.map((g) => (
              <span key={g} className="text-primary">✓ {g.replace("G_", "")}</span>
            ))}
            {receipt.gates.failed.map((g) => (
              <span key={g} className="text-destructive" title={receipt.gates.reasons[g]}>
                ✕ {g.replace("G_", "")}
              </span>
            ))}
          </div>
        )}
        {receipt.gates.failed.length > 0 && (
          <div className="mt-2 flex flex-col gap-1">
            {receipt.gates.failed.map((g) => (
              <p key={g} className="text-xs text-destructive">
                <span className="font-mono">{g}</span>: {receipt.gates.reasons[g]}
              </p>
            ))}
          </div>
        )}
      </div>

      {/* Authorization */}
      {receipt.authorization && (
        <div className="text-xs bg-secondary/40 rounded-lg p-3 flex flex-col gap-1">
          <div className="font-medium mb-1">Signed Authorization (Ed25519 — AP2-inspired, not AP2-compliant)</div>
          <div>ID: <span className="font-mono">{receipt.authorization.authorization_id}</span></div>
          <div>
            Signature: {receipt.authorization.signature_valid ? (
              <span className="text-primary">✓ valid — recomputed live, not cached</span>
            ) : (
              <span className="text-destructive">✕ invalid</span>
            )}
          </div>
          <div>Amount: {money(receipt.authorization.amount_paise, receipt.authorization.currency)}</div>
          <div>Expires: {new Date(receipt.authorization.expires_at).toLocaleString()} {receipt.authorization.expired && <span className="text-destructive">(expired)</span>}</div>
          <div>Consumed: {receipt.authorization.consumed ? "yes — single-use, spent" : "not yet"}</div>
        </div>
      )}

      {/* Payment */}
      {receipt.payment && (
        <div className="text-xs bg-secondary/40 rounded-lg p-3 flex flex-col gap-1">
          <div className="font-medium mb-1">Razorpay (test mode)</div>
          <div>Ledger status: <span className="font-mono">{receipt.payment.status}</span></div>
          {receipt.payment.razorpay_order_id && <div>Order: <span className="font-mono">{receipt.payment.razorpay_order_id}</span></div>}
          {receipt.payment.razorpay_payment_id && <div>Payment: <span className="font-mono">{receipt.payment.razorpay_payment_id}</span></div>}
          {receipt.payment.captured_amount_paise != null && (
            <div>Captured: {money(receipt.payment.captured_amount_paise, receipt.payment.currency)}</div>
          )}
          {receipt.payment.last_rejection_reason && (
            <div className="text-destructive">Rejected: {receipt.payment.last_rejection_reason}</div>
          )}
        </div>
      )}

      {/* Audit */}
      <div className="text-xs text-muted-foreground">
        Audit chain: {receipt.audit.verified
          ? `✓ intact, ${receipt.audit.event_count} event(s), independently re-verified just now`
          : `✕ tamper detected at seq=${receipt.audit.broken_at_seq}`}
      </div>
    </Card>
  );
}
