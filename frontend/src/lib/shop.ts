// The direct-search shopping flow — search/select/confirm/pay against a real
// merchant (FreshCart, or any Shopify store OrderGuard can reach). Distinct
// from lib/api.ts's agent/mission path: this one drives the same
// select_offer -> confirm -> gates -> Authorization -> Razorpay pipeline the
// old server-rendered web/ app used, now the ONLY frontend for it — see
// app.py's /api/sessions/* routes for the backend contract this mirrors.

import type { Offer, ScoredOffer, CouncilResult } from "./api";
import { ApiError } from "./api";

export interface IntentItem {
  requested_product: string;
  quantity: number;
  unit: string;
  required_attributes: Record<string, string>;
  preferred_attributes: Record<string, string>;
  allow_substitution: string;
}

export interface PurchaseIntent {
  intent_id: string;
  user_id: string;
  merchant: string;
  items: IntentItem[];
  maximum_total_paise: number;
  currency: string;
  missing_fields: string[];
  status: string;
  confirmed_cart_hash: string | null;
  confirmed_at: string | null;
}

export interface CartLine {
  sku: string;
  line_id: string;
  variant_id: string;
  title: string;
  quantity: number;
  unit_price_paise: number | null;
  line_total_paise: number | null;
}

export interface ObservedCart {
  merchant: string;
  cart_id: string;
  lines: CartLine[];
  currency: string;
  subtotal_paise: number | null;
  delivery_paise: number;
  total_paise: number | null;
  checkout_url: string;
}

export interface ConfirmationResult {
  intent: PurchaseIntent | null;
  comparison: {
    matches_merchant: boolean;
    matches_currency: boolean;
    matches_quantities: boolean;
    matches_prices: boolean;
    within_cap: boolean;
    cart_hash: string;
    failures: string[];
  };
}

export interface WebResult {
  title: string;
  url: string;
  site: string;
  site_label: string;
  image: string;
  claimed_price_paise: number | null;
}

export interface ShoppingSession {
  session_id: string;
  user_id: string;
  request_text: string;
  intent: PurchaseIntent | null;
  clarifications: string[];
  offers_by_item: Record<number, Record<string, Offer>>;
  selected_by_item: Record<number, Offer>;
  observed_cart: ObservedCart | null;
  confirmation: ConfirmationResult | null;
  authorization: { authorization_id: string } | null;
  pending_fields: string[];
  named_merchant: string;
  blocked_merchant: string;
}

export interface ItemSearchResult {
  query: string;
  quantity: number;
  budget_minor: number | null;
  offers: ScoredOffer[];
  stores_searched: string[];
  suggestions: string[];
  web: WebResult[];
  explanation: string;
  web_budget_note: string;
  council: CouncilResult | null;
}

export interface PaymentOrder {
  key_id: string;
  razorpay_order_id: string;
  amount_paise: number;
  currency: string;
  status: string;
  authorization: { authorization_id: string; amount_paise: number } | null;
  gates_passed: number;
  gates_total: number;
  gate_names_passed: string[];
  gate_names_failed: string[];
}

export interface PaymentVerifyResult {
  captured: boolean;
  payment_id: string;
  amount_paise: number;
  reason: string;
  already_captured: boolean;
  gates_passed: number;
  gates_total: number;
  gate_names_passed: string[];
  gate_names_failed: string[];
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let parsed: any = null;
  try {
    parsed = await res.json();
  } catch {
    /* empty response */
  }
  if (!res.ok) {
    const detail = parsed?.detail;
    const message = typeof detail === "string" ? detail : detail?.reasons?.join(" ") || res.statusText;
    throw new ApiError(res.status, message);
  }
  return parsed as T;
}

export const shop = {
  start: (userId: string, requestText: string) =>
    post<ShoppingSession>("/api/sessions", { user_id: userId, request_text: requestText }),
  continue_: (sessionId: string, message: string) =>
    post<ShoppingSession>(`/api/sessions/${sessionId}/messages`, { message }),
  searchItem: (sessionId: string, itemIndex: number) =>
    post<ItemSearchResult>(`/api/sessions/${sessionId}/items/${itemIndex}/search`),
  selectOffer: (sessionId: string, itemIndex: number, offer: Offer) =>
    post<ShoppingSession>(`/api/sessions/${sessionId}/items/${itemIndex}/select`, {
      offer_key: `${offer.store}|${offer.variant_id}`,
      explicit_user_selection: true,
    }),
  confirm: (sessionId: string) => post<ConfirmationResult>(`/api/sessions/${sessionId}/confirm`),
  createPaymentOrder: (sessionId: string) => post<PaymentOrder>(`/api/sessions/${sessionId}/payment/order`),
  verifyPayment: (sessionId: string, paymentId: string, signature: string) =>
    post<PaymentVerifyResult>(`/api/sessions/${sessionId}/payment/verify`, {
      razorpay_payment_id: paymentId, razorpay_signature: signature,
    }),
};
