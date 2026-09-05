import { useState } from "react";
import { motion } from "framer-motion";
import type { ConnectorResult, CouncilResult, ScoredOffer } from "@/lib/api";
import { api, ApiError } from "@/lib/api";

// Real candidate offers from a real connector search, the Decision
// Council's advisory recommendation highlighted, and a genuine path from
// "I want these" to a real cart write.
//
// Selection model, replacing the old per-item approve-and-write flow: rows
// are SELECTED (pure local state, no request, no prompt), and one explicit
// action at the bottom writes every selected item to the real cart in ONE
// update_cart call. Two real reasons, both found live:
//
//   1. Approving item-by-item meant one full read-modify-write-verify round
//      trip per item against the SAME cart, each one's pre-write read racing
//      whatever the previous item's write was still settling into on
//      Swiggy's side (2026-09-06). See swiggy_cart.py's own docstring.
//   2. A per-item confirm step asked the user to approve the same decision N
//      times to express one intent ("buy these five things").
//
// The R1 (reversible write) boundary is unchanged and still explicit: nothing
// is written until the one button below is pressed, that press is what
// approves the whole batch, and the ActionProposal records behind it are
// still staged server-side per item so the audit chain names exactly what was
// written. What changed is how many times a person has to say yes, not
// whether they have to. This still never processes a payment — checkout
// finishes on the connector's own site (checkout_url).

interface Props {
  connectorId: string;
  result: ConnectorResult;
  council: CouncilResult | null;
  // Fired once, right when a real cart write actually succeeds — never
  // speculatively, never before the real response comes back. Lets a
  // page-level parent collect "which connectors have at least one real
  // approved item" across every card and every mission turn in the session,
  // so it can offer ONE consolidated checkout action instead of the same
  // per-card link repeated on every approved card.
  onApproved?: (info: { connectorId: string; checkoutUrl: string; itemsWritten: number }) => void;
}

type Address = { id: string; address_line: string; category: string };

type WriteState =
  | { phase: "idle" }
  // Asked ONCE for the whole batch, never per item — and only when the
  // search itself did not already tell us which address it was scoped to.
  | { phase: "picking-address"; addresses: Address[] }
  | { phase: "executing" }
  | { phase: "done"; checkoutUrl: string; itemsWritten: number; preservedExisting: number; cartReadSkippedReason: string | null }
  | { phase: "error"; message: string; checkoutUrl?: string };

function offerKey(offer: ScoredOffer): string {
  return `${offer.offer.store}|${offer.offer.variant_id}`;
}

function formatMoney(paise: number, currency: string): string {
  return `${currency === "INR" ? "₹" : currency + " "}${(paise / 100).toFixed(2)}`;
}

const MIN_QTY = 1;
const MAX_QTY = 50; // matches ProposeCartActionRequest.quantity's own ge=1, le=50

export function OfferApproval({ connectorId, result, council, onApproved }: Props) {
  const [state, setState] = useState<WriteState>({ phase: "idle" });
  // Pure local selection — picking an item sends nothing and asks nothing.
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  // Per-offer, not global — comparing two pack sizes side by side shouldn't
  // force the same quantity on both. Lazily defaults to 1 per offer key.
  const [quantities, setQuantities] = useState<Record<string, number>>({});

  if (result.payload.result_type !== "commerce_candidates") return null;
  const offers = result.payload.offers;
  if (!offers.length) return null;
  const searchAddressId = result.payload.address_id;

  const recommendedKey = council?.recommended_id ?? null;
  const busy = state.phase === "executing";
  const selectedOffers = offers.filter((o) => selectedKeys.has(offerKey(o)));
  const selectedTotalMinor = selectedOffers.reduce(
    (sum, o) => sum + o.offer.price_minor * quantityFor(o), 0,
  );
  const currency = offers[0]?.offer.currency ?? "INR";

  function quantityFor(offer: ScoredOffer): number {
    return quantities[offerKey(offer)] ?? MIN_QTY;
  }

  function setQuantity(offer: ScoredOffer, next: number) {
    const clamped = Math.min(MAX_QTY, Math.max(MIN_QTY, next));
    setQuantities((q) => ({ ...q, [offerKey(offer)]: clamped }));
  }

  function toggleSelected(offer: ScoredOffer) {
    const key = offerKey(offer);
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function failed(err: unknown) {
    setState({
      phase: "error",
      message: err instanceof ApiError ? err.message : String(err),
      checkoutUrl: err instanceof ApiError ? err.checkoutUrl : undefined,
    });
  }

  async function startWrite() {
    if (!selectedOffers.length) return;
    // These offers came from a search scoped to ONE real delivery address,
    // and the variant ids in them only exist in the store serving it.
    // Asking which address to deliver to, as if any saved address were
    // equally valid, is how F-036/F-048 happened: picking a different one
    // makes every id unsellable, and Swiggy's answer to that is to empty the
    // whole cart. When the search told us which address it used, that is the
    // address — there is nothing to ask.
    if (searchAddressId) {
      await writeSelected(searchAddressId);
      return;
    }
    setState({ phase: "executing" });
    try {
      const addrs = await api.connectorAddresses(connectorId);
      if (addrs.addresses.length === 1) {
        await writeSelected(addrs.addresses[0].id);
      } else {
        setState({ phase: "picking-address", addresses: addrs.addresses });
      }
    } catch (err) {
      failed(err);
    }
  }

  async function writeSelected(addressId: string) {
    const chosen = offers.filter((o) => selectedKeys.has(offerKey(o)));
    if (!chosen.length) return;
    setState({ phase: "executing" });
    try {
      // ONE request carrying the whole selected cart — no per-product
      // staging round trip, and one real update_cart behind it. The audit
      // chain still records each item individually, server-side, inside
      // this same request.
      const outcome = await api.writeCart({
        connectorId,
        addressId,
        items: chosen.map((o) => ({
          variantId: o.offer.variant_id,
          quantity: quantityFor(o),
          offerTitle: o.offer.title,
          offerPriceMinor: o.offer.price_minor,
        })),
      });
      setState({
        phase: "done", checkoutUrl: outcome.checkout_url,
        itemsWritten: outcome.items_written.length,
        preservedExisting: outcome.preserved_existing_items,
        cartReadSkippedReason: outcome.cart_read_skipped_reason,
      });
      onApproved?.({
        connectorId, checkoutUrl: outcome.checkout_url, itemsWritten: outcome.items_written.length,
      });
    } catch (err) {
      failed(err);
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
          const isSelected = selectedKeys.has(offerKey(o));
          return (
            <motion.div
              key={offerKey(o)}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.03 }}
              className={`border-2 px-3 py-2 flex items-center gap-3 ${
                isSelected
                  ? "border-signal bg-signal/10"
                  : isRecommended
                    ? "border-signal hard-signal"
                    : "border-border"
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
                aria-pressed={isSelected}
                onClick={() => toggleSelected(o)}
                className={`shrink-0 label-micro border-2 px-3 py-1.5 transition-colors duration-150 disabled:opacity-40 disabled:cursor-not-allowed ${
                  isSelected
                    ? "border-signal bg-signal text-background"
                    : "border-signal text-signal hover:bg-signal hover:text-background"
                }`}
              >
                {isSelected ? "✓ SELECTED" : "SELECT"}
              </button>
            </motion.div>
          );
        })}
      </div>

      {selectedOffers.length > 0 && (state.phase === "idle" || state.phase === "error") && (
        <div className="mt-3 border-t-2 border-signal pt-3 flex items-center justify-between gap-3 flex-wrap">
          <div className="label-micro text-muted-foreground">
            {selectedOffers.length} SELECTED · {formatMoney(selectedTotalMinor, currency)} — WRITTEN IN ONE CALL
          </div>
          <button
            type="button"
            onClick={startWrite}
            className="label-micro border-2 border-signal bg-signal text-background px-3 py-1.5"
          >
            ADD {selectedOffers.length} ITEM{selectedOffers.length === 1 ? "" : "S"} TO REAL CART →
          </button>
        </div>
      )}

      {state.phase === "picking-address" && (
        <div className="mt-3 border-t-2 border-border pt-3">
          <div className="label-micro text-muted-foreground mb-2">DELIVER TO — REAL SAVED ADDRESSES</div>
          <div className="flex flex-col gap-1.5">
            {state.addresses.map((a) => (
              <button
                key={a.id}
                type="button"
                onClick={() => writeSelected(a.id)}
                className="text-left text-[12px] border border-border px-2.5 py-1.5 hover:border-signal transition-colors duration-150"
              >
                <span className="label-micro text-muted-foreground mr-2">{a.category.toUpperCase()}</span>
                {a.address_line}
              </button>
            ))}
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
          <p className="text-[12px] text-muted-foreground mb-3">{state.message}</p>
          {state.checkoutUrl && (
            <a
              href={state.checkoutUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block label-micro border-2 border-destructive px-3 py-1.5 hover:bg-destructive hover:text-background"
            >
              OPEN THE REAL CART TO ADD IT YOURSELF →
            </a>
          )}
        </div>
      )}
    </div>
  );
}
