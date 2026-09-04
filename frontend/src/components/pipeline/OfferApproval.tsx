import { useState } from "react";
import { motion } from "framer-motion";
import type { ConnectorResult, CouncilResult, ScoredOffer } from "@/lib/api";
import { api, ApiError } from "@/lib/api";

// Real candidate offers from a real connector search, the Decision
// Council's advisory recommendation highlighted, and — the piece that was
// missing — a genuine path from "I like this one" to a real cart write.
// update_cart is R1 (reversible write): it never auto-executes, it always
// needs this explicit approval, and what it writes is a real cart on the
// connector's own site, not a payment through OrderGuard. Checkout finishes
// on the connector's own site (checkout_url), never through OrderGuard's
// Razorpay account, which only ever settles for OrderGuard's own merchant.

interface Props {
  connectorId: string;
  result: ConnectorResult;
  council: CouncilResult | null;
  // Fired once, right when a real cart write actually succeeds — never
  // speculatively, never before the real approveCartAction response comes
  // back. Lets a page-level parent collect "which connectors have at
  // least one real approved item" across every card and every mission
  // turn in the session, so it can offer ONE consolidated checkout action
  // instead of the same per-card link repeated on every approved card.
  onApproved?: (info: { connectorId: string; checkoutUrl: string; itemsWritten: number }) => void;
}

type ApprovalState =
  | { phase: "idle" }
  | { phase: "picking-address"; offer: ScoredOffer; addresses: { id: string; address_line: string; category: string }[] }
  | { phase: "proposing"; offer: ScoredOffer }
  | { phase: "awaiting-approval"; offer: ScoredOffer; proposalId: string; summary: string; addressId: string }
  | { phase: "executing" }
  | { phase: "done"; checkoutUrl: string; itemsWritten: number; preservedExisting: number; cartReadSkippedReason: string | null }
  | { phase: "error"; message: string };

function offerKey(offer: ScoredOffer): string {
  return `${offer.offer.store}|${offer.offer.variant_id}`;
}

function formatMoney(paise: number, currency: string): string {
  return `${currency === "INR" ? "₹" : currency + " "}${(paise / 100).toFixed(2)}`;
}

const MIN_QTY = 1;
const MAX_QTY = 50; // matches ProposeCartActionRequest.quantity's own ge=1, le=50

export function OfferApproval({ connectorId, result, council, onApproved }: Props) {
  const [state, setState] = useState<ApprovalState>({ phase: "idle" });
  // Per-offer, not global — comparing two pack sizes side by side shouldn't
  // force the same quantity on both. Lazily defaults to 1 per offer key.
  const [quantities, setQuantities] = useState<Record<string, number>>({});

  if (result.payload.result_type !== "commerce_candidates") return null;
  const offers = result.payload.offers;
  if (!offers.length) return null;

  const recommendedKey = council?.recommended_id ?? null;

  function quantityFor(offer: ScoredOffer): number {
    return quantities[offerKey(offer)] ?? MIN_QTY;
  }

  function setQuantity(offer: ScoredOffer, next: number) {
    const clamped = Math.min(MAX_QTY, Math.max(MIN_QTY, next));
    setQuantities((q) => ({ ...q, [offerKey(offer)]: clamped }));
  }

  async function startApproval(offer: ScoredOffer) {
    setState({ phase: "proposing", offer });
    try {
      const addrs = await api.connectorAddresses(connectorId);
      if (addrs.addresses.length === 1) {
        await proposeAndApprove(offer, addrs.addresses[0].id);
      } else {
        setState({ phase: "picking-address", offer, addresses: addrs.addresses });
      }
    } catch (err) {
      setState({ phase: "error", message: err instanceof ApiError ? err.message : String(err) });
    }
  }

  async function proposeAndApprove(offer: ScoredOffer, addressId: string) {
    setState({ phase: "proposing", offer });
    try {
      const proposal = await api.proposeCartAction({
        connectorId,
        variantId: offer.offer.variant_id,
        quantity: quantityFor(offer),
        offerTitle: offer.offer.title,
        offerPriceMinor: offer.offer.price_minor,
      });
      setState({
        phase: "awaiting-approval", offer, proposalId: proposal.proposal_id,
        summary: proposal.summary, addressId,
      });
    } catch (err) {
      setState({ phase: "error", message: err instanceof ApiError ? err.message : String(err) });
    }
  }

  async function confirmApproval() {
    if (state.phase !== "awaiting-approval") return;
    setState({ phase: "executing" });
    try {
      const outcome = await api.approveCartAction(state.proposalId, state.addressId);
      setState({
        phase: "done", checkoutUrl: outcome.checkout_url,
        itemsWritten: outcome.items_written.length, preservedExisting: outcome.preserved_existing_items,
        cartReadSkippedReason: outcome.cart_read_skipped_reason,
      });
      onApproved?.({
        connectorId, checkoutUrl: outcome.checkout_url, itemsWritten: outcome.items_written.length,
      });
    } catch (err) {
      setState({ phase: "error", message: err instanceof ApiError ? err.message : String(err) });
    }
  }

  return (
    <div className="border-2 border-border bg-card/60 hard mt-4 p-4">
      <div className="label-micro text-signal mb-3">
        {offers.length} REAL OFFER{offers.length === 1 ? "" : "S"} FROM {result.payload.merchant.toUpperCase()}
      </div>

      {council && (
        <div className="label-micro text-muted-foreground mb-3 leading-relaxed">
          COUNCIL: {council.fallback_used ? "FALLBACK (LLM PICK REJECTED) — " : ""}{council.rationale}
        </div>
      )}

      <div className="flex flex-col gap-2 max-h-[360px] overflow-y-auto">
        {offers.map((o, i) => {
          const isRecommended = offerKey(o) === recommendedKey;
          const busy = state.phase !== "idle" && state.phase !== "error"
            && "offer" in state && offerKey(state.offer) === offerKey(o);
          return (
            <motion.div
              key={offerKey(o)}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.03 }}
              className={`border-2 px-3 py-2 flex items-center gap-3 ${
                isRecommended ? "border-signal hard-signal" : "border-border"
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  {isRecommended && <span className="label-micro text-signal shrink-0">★ RECOMMENDED</span>}
                  <span className="text-[13px] font-bold truncate">{o.offer.title}</span>
                </div>
                <div className="label-micro text-muted-foreground mt-0.5">
                  {o.offer.variant_title || "—"} · {formatMoney(o.offer.price_minor, o.offer.currency)}
                  {!o.in_stock && " · OUT OF STOCK"}
                </div>
              </div>
              <div className="shrink-0 flex items-center border-2 border-border">
                <button
                  type="button"
                  disabled={!o.in_stock || busy}
                  aria-label={`Decrease quantity for ${o.offer.title}`}
                  onClick={() => setQuantity(o, quantityFor(o) - 1)}
                  className="w-7 h-7 grid place-items-center text-[13px] hover:bg-border/40 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  −
                </button>
                <span className="w-7 text-center text-[13px] tabular-nums select-none">
                  {quantityFor(o)}
                </span>
                <button
                  type="button"
                  disabled={!o.in_stock || busy}
                  aria-label={`Increase quantity for ${o.offer.title}`}
                  onClick={() => setQuantity(o, quantityFor(o) + 1)}
                  className="w-7 h-7 grid place-items-center text-[13px] hover:bg-border/40 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  +
                </button>
              </div>
              <button
                type="button"
                disabled={!o.in_stock || busy}
                onClick={() => startApproval(o)}
                className="shrink-0 label-micro border-2 border-signal text-signal px-3 py-1.5 hover:bg-signal hover:text-background transition-colors duration-150 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {busy ? "…" : "APPROVE →"}
              </button>
            </motion.div>
          );
        })}
      </div>

      {state.phase === "picking-address" && (
        <div className="mt-3 border-t-2 border-border pt-3">
          <div className="label-micro text-muted-foreground mb-2">DELIVER TO — REAL SAVED ADDRESSES</div>
          <div className="flex flex-col gap-1.5">
            {state.addresses.map((a) => (
              <button
                key={a.id}
                type="button"
                onClick={() => proposeAndApprove(state.offer, a.id)}
                className="text-left text-[12px] border border-border px-2.5 py-1.5 hover:border-signal transition-colors duration-150"
              >
                <span className="label-micro text-muted-foreground mr-2">{a.category.toUpperCase()}</span>
                {a.address_line}
              </button>
            ))}
          </div>
        </div>
      )}

      {state.phase === "proposing" && (
        <div className="mt-3 label-micro text-warn animate-pulse">STAGING PROPOSAL…</div>
      )}

      {state.phase === "awaiting-approval" && (
        <div className="mt-3 border-t-2 border-warn pt-3">
          <div className="label-micro text-warn mb-2">R1 — REVERSIBLE WRITE — CONFIRM TO PROCEED</div>
          <p className="text-[13px] mb-3">{state.summary}</p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={confirmApproval}
              className="label-micro border-2 border-warn bg-warn text-background px-3 py-1.5"
            >
              CONFIRM — WRITE REAL CART
            </button>
            <button
              type="button"
              onClick={() => setState({ phase: "idle" })}
              className="label-micro border-2 border-border px-3 py-1.5 hover:border-destructive hover:text-destructive"
            >
              CANCEL
            </button>
          </div>
        </div>
      )}

      {state.phase === "executing" && (
        <div className="mt-3 label-micro text-warn animate-pulse">
          WRITING TO YOUR REAL CART — DIRECT MCP CALL, NOT THROUGH THE MODEL…
        </div>
      )}

      {state.phase === "done" && (
        <div className="mt-3 border-t-2 border-signal pt-3">
          <div className="label-micro text-signal mb-2">
            ✓ CART UPDATED — {state.itemsWritten} ITEM(S)
            {state.preservedExisting > 0 && ` (${state.preservedExisting} EXISTING ITEM(S) PRESERVED)`}
          </div>
          {state.cartReadSkippedReason && (
            <p className="text-[12px] text-warn mb-2">⚠ {state.cartReadSkippedReason}</p>
          )}
          <p className="text-[12px] text-muted-foreground mb-3">
            This only wrote a cart. OrderGuard never processes this payment — it settles only for its own
            merchant. Finish checkout on the connector's own site.
          </p>
          <a
            href={state.checkoutUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block label-micro border-2 border-signal bg-signal text-background px-3 py-1.5"
          >
            COMPLETE CHECKOUT ON THE MERCHANT'S SITE →
          </a>
        </div>
      )}

      {state.phase === "error" && (
        <div className="mt-3 border-t-2 border-destructive pt-3">
          <div className="label-micro text-destructive mb-1">HALTED BEFORE WRITING ANYTHING</div>
          <p className="text-[12px] text-muted-foreground">{state.message}</p>
        </div>
      )}
    </div>
  );
}
