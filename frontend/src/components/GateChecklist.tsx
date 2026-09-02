import { motion } from "framer-motion";

// A GitHub-Actions-style checks list for the real, named deterministic gates
// that just ran on THIS purchase — not a benchmark from an earlier run, not
// a count. Every name here is read straight from checkout_guard.py's own
// GateName enum via the API response; nothing is invented client-side.

const LABELS: Record<string, string> = {
  G_MERCHANT_PERMITTED: "Merchant is on the approved list",
  G_INTENT_VALID: "Request is a confirmed purchase",
  G_FIELDS_COMPLETE: "All required fields are present",
  G_CART_UNIQUE: "Cart is uniquely identified",
  G_ATTRIBUTES_MATCH: "Product attributes match",
  G_QUANTITIES_MATCH: "Quantities match what was approved",
  G_PRICES_MATCH: "Prices match what was quoted",
  G_ITEMS_AVAILABLE: "Every item is still available",
  G_CURRENCY_MATCH: "Currency matches",
  G_WITHIN_CAP: "Total is within the spending cap",
  G_CONFIRMATION_MATCHES: "Cart matches what you confirmed",
  G_AUTHORIZATION_FRESH: "Confirmation is still fresh (TOCTOU check)",
  G_IDEMPOTENCY_FREE: "This purchase has not already happened",
  G_PAYMENT_CAPTURED: "Razorpay confirms the payment was captured",
  G_NO_REFUND: "No refund exists against this payment",
  G_AMOUNT_MATCH: "Captured amount matches the confirmed total",
  G_CURRENCY_MATCH_POST: "Captured currency matches",
  G_SINGLE_CANDIDATE: "Exactly one matching order — never a guess",
  G_CORRELATION: "Payment belongs to the order we created",
  G_ORDER_REPAIRABLE: "A lost order response resolved cleanly",
  G_NOT_EXPIRED: "Purchase confirmed recently enough to trust",
  G_NO_PRIOR_EFFECT: "No prior capture for this purchase",
};

function readable(name: string): string {
  return LABELS[name] ?? name.replace(/^G_/, "").replace(/_/g, " ").toLowerCase();
}

interface Props {
  title: string;
  passed: string[];
  failed: string[];
}

export function GateChecklist({ title, passed, failed }: Props) {
  const rows = [
    ...failed.map((name) => ({ name, ok: false })),
    ...passed.map((name) => ({ name, ok: true })),
  ];
  const total = rows.length;

  return (
    <div className="border-2 border-border bg-card">
      <div className="flex items-center justify-between border-b-2 border-border px-3 py-2">
        <span className="label-micro text-muted-foreground">{title}</span>
        <span className={`label-micro ${failed.length ? "text-destructive" : "text-signal"}`}>
          {failed.length ? `${failed.length} FAILED` : `${passed.length}/${total} PASSED`}
        </span>
      </div>
      <div className="flex flex-col divide-y divide-border max-h-[280px] overflow-y-auto">
        {rows.map((row, i) => (
          <motion.div
            key={row.name}
            initial={{ opacity: 0, x: -4 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.04, duration: 0.15 }}
            className="flex items-center gap-2 px-3 py-1.5"
          >
            <span className={`shrink-0 font-mono text-[12px] ${row.ok ? "text-signal" : "text-destructive"}`}>
              {row.ok ? "✓" : "✕"}
            </span>
            <span className="text-[12px] flex-1 min-w-0 truncate">{readable(row.name)}</span>
            <span className="label-micro text-muted-foreground shrink-0">{row.name}</span>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
