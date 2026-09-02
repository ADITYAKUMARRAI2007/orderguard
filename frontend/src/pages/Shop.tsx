import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { PipelineNode, type NodeStatus } from "@/components/pipeline/PipelineNode";
import { VeinConnector } from "@/components/pipeline/VeinConnector";
import { GateChecklist } from "@/components/GateChecklist";
import { ApiError, type Offer, type ScoredOffer } from "@/lib/api";
import {
  shop, type ShoppingSession, type ItemSearchResult, type PaymentOrder,
} from "@/lib/shop";

// The direct-search shopping flow, ported from the old server-rendered
// web/app.js into this app — see that file's own comments for the exact
// backend contract this mirrors (search -> select -> confirm -> gates ->
// signed Authorization -> Razorpay). FreshCart is the merchant this can run
// a real payment against (see ReceiptCard.tsx for why); a Shopify store
// works up through cart confirmation, same honest limit as everywhere else
// in this project — OrderGuard never collects money for a store it does not
// own.

declare global {
  interface Window {
    Razorpay: new (options: Record<string, unknown>) => { open: () => void; on: (event: string, cb: (r: any) => void) => void };
  }
}

interface ChatTurn {
  who: "user" | "system";
  text: string;
  error?: boolean;
}

const STEPS = ["UNDERSTAND", "CLARIFY", "RESEARCH", "REVIEW", "PAYMENT"] as const;
type StepName = (typeof STEPS)[number];

const PRESETS = ["2 litres of milk from freshcart, budget 200 rupees", "a loaf of bread from freshcart under 100 rupees"];

function money(paise: number | null | undefined, currency = "INR"): string {
  if (paise == null) return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency }).format(paise / 100);
}

export function Shop() {
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([
    { who: "system", text: "Tell me what to buy. I'll search real stores, show you the cart, and nothing is charged until you approve it." },
  ]);
  const [session, setSession] = useState<ShoppingSession | null>(null);
  const [offersByItem, setOffersByItem] = useState<Record<number, ItemSearchResult>>({});
  const [step, setStep] = useState<StepName>("UNDERSTAND");
  const [busy, setBusy] = useState(false);
  const [paymentOrder, setPaymentOrder] = useState<PaymentOrder | null>(null);
  const [paid, setPaid] = useState<{ paymentId: string; amountPaise: number } | null>(null);
  const [postGates, setPostGates] = useState<{ passed: string[]; failed: string[] } | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  function say(text: string, error = false) {
    setTurns((t) => [...t, { who: "system", text, error }]);
  }

  async function searchAllItems(s: ShoppingSession) {
    setStep("RESEARCH");
    const results: Record<number, ItemSearchResult> = {};
    for (let i = 0; i < (s.intent?.items.length ?? 0); i++) {
      try {
        results[i] = await shop.searchItem(s.session_id, i);
      } catch (err) {
        say(`Search stopped: ${err instanceof ApiError ? err.message : String(err)}`, true);
        setBusy(false);
        return;
      }
    }
    setOffersByItem(results);
    setStep("REVIEW");
    const total = Object.values(results).reduce((n, r) => n + r.offers.length, 0);
    say(
      total
        ? "Here are the real options. I won't pick between different products for you — choose one, and I'll verify the cart afterward."
        : "None of the stores I can buy from stock this. See below for what else I found."
    );
    setBusy(false);
  }

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setTurns((t) => [...t, { who: "user", text }]);
    setInput("");
    setBusy(true);
    try {
      const s = session && step === "CLARIFY"
        ? await shop.continue_(session.session_id, text)
        : await shop.start("local-user", text);
      setSession(s);
      if (!s.intent) {
        setStep("CLARIFY");
        say(s.clarifications.join(" ") || "Could you tell me a little more?");
        setBusy(false);
        return;
      }
      const items = s.intent.items.map((i) => `${i.quantity} × ${i.requested_product}`).join(", ");
      const shopName = s.intent.merchant ? ` from ${s.intent.merchant}` : "";
      say(`Understood: ${items}${shopName}, up to ${money(s.intent.maximum_total_paise, s.intent.currency)}. Checking real stores now.`);
      await searchAllItems(s);
    } catch (err) {
      say(`Stopped before changing anything: ${err instanceof ApiError ? err.message : String(err)}`, true);
      setBusy(false);
    }
  }

  async function pickOffer(itemIndex: number, offer: Offer) {
    if (!session || busy) return;
    setBusy(true);
    setStep("REVIEW");
    try {
      const s = await shop.selectOffer(session.session_id, itemIndex, offer);
      setSession(s);
      say(`Added ${offer.title}. Reading the cart back from the store, not trusting my own add request.`);
      const allSelected = Object.keys(s.selected_by_item ?? {}).length === s.intent?.items.length;
      if (allSelected) {
        const confirmation = await shop.confirm(s.session_id);
        setSession((prev) => (prev ? { ...prev, confirmation } : prev));
        setConfirmed(!!confirmation.intent);
        say(
          confirmation.intent
            ? "Cart verified against the store's own record. Nothing has been charged yet — review the total and continue when ready."
            : "The cart changed or could not be verified, so I stopped before payment."
        );
      }
    } catch (err) {
      say(`Stopped before confirming the cart: ${err instanceof ApiError ? err.message : String(err)}`, true);
    } finally {
      setBusy(false);
    }
  }

  async function startPayment() {
    if (!session) return;
    setStep("PAYMENT");
    setBusy(true);
    try {
      const order = await shop.createPaymentOrder(session.session_id);
      setPaymentOrder(order);
      say(`${order.gates_passed}/${order.gates_total} deterministic gates passed. Opening Razorpay checkout.`);
      if (order.status === "captured") {
        setPaid({ paymentId: "", amountPaise: order.amount_paise });
        setBusy(false);
        return;
      }
      const rzp = new window.Razorpay({
        key: order.key_id,
        order_id: order.razorpay_order_id,
        amount: order.amount_paise,
        currency: order.currency,
        name: "OrderGuard (test mode)",
        description: "Guarded purchase — verified server-side before it counts",
        handler: async (response: { razorpay_payment_id: string; razorpay_signature: string }) => {
          say("Verifying with Razorpay directly — not trusting this page's own success message…");
          try {
            const result = await shop.verifyPayment(session.session_id, response.razorpay_payment_id, response.razorpay_signature);
            setPostGates({ passed: result.gate_names_passed, failed: result.gate_names_failed });
            if (result.captured) {
              setPaid({ paymentId: result.payment_id, amountPaise: result.amount_paise });
              say(`Paid and independently verified. ${money(result.amount_paise)} captured.`);
            } else {
              say(`Not accepted: ${result.reason}`, true);
            }
          } catch (err) {
            say(`Verification failed: ${err instanceof ApiError ? err.message : String(err)}`, true);
          }
        },
        modal: { ondismiss: () => setBusy(false) },
      });
      rzp.on("payment.failed", (r: { error: { description: string } }) => say(`Payment failed: ${r.error.description}`, true));
      rzp.open();
    } catch (err) {
      say(`Stopped before payment: ${err instanceof ApiError ? err.message : String(err)}`, true);
      setBusy(false);
    }
  }

  const stepStatus = (name: StepName): NodeStatus => {
    const order = STEPS.indexOf(name);
    const current = STEPS.indexOf(step);
    if (order < current) return "ok";
    if (order === current) return busy ? "running" : "ok";
    return "idle";
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(320px,400px)_1fr] gap-6 items-start mt-8">
      <section className="border-2 border-border bg-card hard" aria-label="Shop console">
        <header className="border-b-2 border-border px-4 py-2.5 flex items-center gap-2">
          <span className="label-micro text-signal">SHOP · FRESHCART & CONNECTED STORES</span>
          <span className="ml-auto label-micro text-muted-foreground">{busy ? "BUSY" : "IDLE"}</span>
        </header>
        <div className="px-4 py-4 flex flex-col gap-3 max-h-[340px] overflow-y-auto">
          <AnimatePresence initial={false}>
            {turns.map((turn, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, x: turn.who === "user" ? 10 : -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ type: "spring", stiffness: 300, damping: 26 }}
                className="text-[13px] leading-relaxed"
              >
                <span className={`label-micro mr-2 ${turn.who === "user" ? "text-foreground" : turn.error ? "text-destructive" : "text-signal"}`}>
                  {turn.who === "user" ? "YOU ›" : turn.error ? "HALT ›" : "OG ›"}
                </span>
                <span className={turn.error ? "text-destructive" : "text-muted-foreground"}>{turn.text}</span>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
        <div className="border-t-2 border-border px-4 py-3">
          <form onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              rows={2}
              placeholder="2 litres of milk from freshcart, budget 200 rupees"
              className="w-full bg-background border-2 border-border px-3 py-2 text-[13px] resize-none focus:outline-none focus:border-signal transition-colors duration-150 placeholder:text-muted-foreground/50"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="w-full h-11 mt-2 bg-signal text-background font-bold text-xs tracking-widest border-2 border-signal cursor-pointer transition-all duration-150 hover:hard-signal disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {busy ? "WORKING…" : "SEND →"}
            </button>
          </form>
          <div className="flex flex-wrap gap-1.5 mt-3">
            {PRESETS.map((p) => (
              <button key={p} type="button" onClick={() => setInput(p)} className="label-micro border border-border px-2 py-1.5 text-muted-foreground hover:border-signal hover:text-signal transition-colors duration-150">
                {p}
              </button>
            ))}
          </div>
        </div>
        <footer className="border-t-2 border-border px-4 py-2.5">
          <p className="label-micro text-muted-foreground leading-relaxed">
            NOTHING IS CHARGED UNTIL YOU APPROVE THE CART AND COMPLETE RAZORPAY CHECKOUT
          </p>
        </footer>
      </section>

      <section className="grid-surface border-2 border-border bg-card/60 hard-lg min-h-[460px]">
        <header className="border-b-2 border-border px-4 py-2.5 bg-card">
          <span className="label-micro text-signal">CHECKOUT PIPELINE</span>
        </header>
        <div className="p-5 flex flex-col gap-6">
          <div className="flex items-center flex-wrap gap-y-2">
            {STEPS.map((name, i) => (
              <div key={name} className="flex items-center">
                {i > 0 && <VeinConnector status={stepStatus(name) === "ok" ? "active" : stepStatus(name) === "running" ? "pending" : "idle"} />}
                <PipelineNode kind={`0${i + 1}`} value={name} status={stepStatus(name)} index={i} />
              </div>
            ))}
          </div>

          {!session && (
            <p className="text-sm text-muted-foreground max-w-md">
              Send a request on the left. Every step here is a real call to app.py's session
              endpoints — the same backend the automated payment tests run against.
            </p>
          )}

          {session?.intent && step !== "PAYMENT" && !paid && (
            <div className="border-2 border-border bg-card p-4 flex flex-col gap-1.5 max-w-md">
              <div className="label-micro text-muted-foreground">PLAN</div>
              <div className="text-sm"><span className="text-muted-foreground">Store: </span>{session.intent.merchant || "any eligible store"}</div>
              <div className="text-sm">
                <span className="text-muted-foreground">Items: </span>
                {session.intent.items.map((it) => `${it.quantity} × ${it.requested_product}`).join(", ")}
              </div>
              <div className="text-sm"><span className="text-muted-foreground">Spending limit: </span>{money(session.intent.maximum_total_paise, session.intent.currency)}</div>
            </div>
          )}

          {step === "REVIEW" && !confirmed && (
            <div className="flex flex-col gap-2 max-w-xl">
              {Object.entries(offersByItem).map(([itemIndex, result]) => (
                <div key={itemIndex} className="flex flex-col gap-2">
                  {result.offers.length === 0 && (
                    <p className="text-sm text-destructive">{result.explanation}</p>
                  )}
                  {result.offers.slice(0, 5).map((scored: ScoredOffer) => {
                    const isRecommended = result.council?.recommended_id === `${scored.offer.store}|${scored.offer.variant_id}`;
                    return (
                      <div
                        key={`${scored.offer.store}|${scored.offer.variant_id}`}
                        className={`border-2 px-3 py-2 flex items-center gap-3 ${isRecommended ? "border-signal hard-signal" : "border-border"}`}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            {isRecommended && <span className="label-micro text-signal shrink-0">★ RECOMMENDED</span>}
                            <span className="text-[13px] font-bold truncate">{scored.offer.title}</span>
                          </div>
                          <div className="label-micro text-muted-foreground mt-0.5">
                            {scored.offer.store_label || scored.offer.store}
                            {scored.offer.store === "freshcart" && " — OrderGuard's reference merchant"}
                            {" · "}{scored.in_stock ? "in stock" : "unavailable"}
                          </div>
                        </div>
                        <div className="text-[13px] font-bold">{money(scored.line_total_minor, scored.offer.currency)}</div>
                        <button
                          type="button"
                          disabled={!scored.in_stock || busy}
                          onClick={() => pickOffer(Number(itemIndex), scored.offer)}
                          className="shrink-0 label-micro border-2 border-signal text-signal px-3 py-1.5 hover:bg-signal hover:text-background transition-colors duration-150 disabled:opacity-40"
                        >
                          CHOOSE →
                        </button>
                      </div>
                    );
                  })}
                  {result.web.length > 0 && (
                    <div className="flex flex-col gap-1.5 mt-1">
                      <div className="label-micro text-muted-foreground">
                        NOT PURCHASABLE HERE — OPEN ON THE WEB
                      </div>
                      {result.web.slice(0, 4).map((w) => (
                        <a
                          key={w.url} href={w.url} target="_blank" rel="noopener noreferrer"
                          className="text-[12px] border border-border px-2.5 py-1.5 hover:border-signal transition-colors duration-150 flex justify-between"
                        >
                          <span>{w.title}</span>
                          <span className="text-muted-foreground">{w.site_label || w.site}</span>
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {confirmed && step !== "PAYMENT" && !paid && (
            <div className="border-2 border-signal hard-signal bg-card p-4 max-w-md flex flex-col gap-2">
              <div className="label-micro text-signal">✓ CART VERIFIED — NOTHING CHARGED YET</div>
              <div className="text-sm">
                Verified total: <span className="font-bold">{money(session?.observed_cart?.total_paise, session?.intent?.currency)}</span>
              </div>
              <button
                type="button"
                onClick={startPayment}
                disabled={busy}
                className="label-micro border-2 border-signal bg-signal text-background px-3 py-2 mt-1 disabled:opacity-40"
              >
                CONTINUE TO PAYMENT APPROVAL →
              </button>
            </div>
          )}

          {step === "PAYMENT" && paymentOrder && !paid && (
            <div className="flex flex-col gap-3 max-w-md">
              <div className="border-2 border-warn bg-card p-4 flex flex-col gap-2">
                <div className="label-micro text-warn">RAZORPAY CHECKOUT OPEN</div>
                <div className="text-sm">Amount: <span className="font-bold">{money(paymentOrder.amount_paise, paymentOrder.currency)}</span></div>
                <div className="label-micro text-muted-foreground">Order: {paymentOrder.razorpay_order_id}</div>
              </div>
              <GateChecklist
                title="PRE-PAYMENT GATES — RAN ON THIS PURCHASE, JUST NOW"
                passed={paymentOrder.gate_names_passed}
                failed={paymentOrder.gate_names_failed}
              />
            </div>
          )}

          {paid && (
            <div className="flex flex-col gap-3 max-w-md">
              <div className="border-2 border-signal hard-signal bg-card p-4 flex flex-col gap-2">
                <div className="label-micro text-signal">✓ PAID AND INDEPENDENTLY VERIFIED</div>
                <div className="text-sm">{money(paid.amountPaise)} captured — never trusted from the browser, re-fetched from Razorpay's own record.</div>
                {session && (
                  <a
                    href={`/evidence?session=${session.session_id}`}
                    className="label-micro border-2 border-signal text-signal px-3 py-2 mt-1 text-center hover:bg-signal hover:text-background transition-colors duration-150"
                  >
                    VIEW EVIDENCE RECEIPT →
                  </a>
                )}
              </div>
              {postGates && (
                <GateChecklist
                  title="POST-PAYMENT GATES — RAN ON THIS PURCHASE, JUST NOW"
                  passed={postGates.passed}
                  failed={postGates.failed}
                />
              )}
            </div>
          )}

          {!paid && postGates && postGates.failed.length > 0 && (
            <div className="flex flex-col gap-3 max-w-md">
              <div className="border-2 border-destructive bg-card p-4">
                <div className="label-micro text-destructive">✕ PAYMENT REFUSED BEFORE CAPTURE</div>
              </div>
              <GateChecklist
                title="POST-PAYMENT GATES — WHY IT WAS REFUSED"
                passed={postGates.passed}
                failed={postGates.failed}
              />
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
