// Thin, typed wrapper around OrderGuard's existing, already-tested FastAPI
// backend (src/orderguard/app.py). No backend changes for this frontend —
// same REST contract the server-rendered web/ UI already uses; see
// vite.config.ts's dev proxy for how /api and /mcp reach localhost:8000.

export type RiskTier = "R0" | "R1" | "R2" | "R3";

export interface ToolInfo {
  name: string;
  risk_tier: RiskTier;
}

export interface AgentConnector {
  id: string;
  label: string;
  category: string;
  backend_type: string;
  evidence: string;
  capability: string;
  auth: string;
  status: string;
  tools: ToolInfo[];
  routable: boolean;
  note: string;
}

export interface ClaudeCodeConnector {
  name: string;
  url: string;
  connected: boolean;
  status_text: string;
  cli_managed: boolean;
  usable_by_orderguard: boolean;
}

export interface RuntimeStatus {
  server_managed_api_key: boolean;
  byok_session_api_key: boolean;
  byok_masked: string | null;
  subscription_runtime: boolean;
  active_api_mode: string | null;
  active_agent_runtime: "api" | "subscription";
}

export interface Offer {
  store: string;
  store_label: string;
  product_id: string;
  variant_id: string;
  title: string;
  variant_title: string;
  price_minor: number;
  currency: string;
  available: boolean;
  url: string;
  image: string;
}

export interface ScoredOffer {
  offer: Offer;
  relevance: number;
  in_stock: boolean;
  priced: boolean;
  within_budget: boolean | null;
  line_total_minor: number;
}

export interface CouncilEligible {
  candidate_id: string;
  store_label: string;
  title: string;
  price_minor: number;
  line_total_minor: number;
  relevance: number;
}

export interface CouncilResult {
  recommended_id: string | null;
  rationale: string;
  fallback_used: boolean;
  alternatives_considered: number;
  alternatives_rejected: number;
  eligible: CouncilEligible[];
}

export type ConnectorResultPayload =
  | { result_type: "commerce_candidates"; merchant: string; offers: ScoredOffer[] }
  | { result_type: "dev_task"; source: string; items: any[] }
  | { result_type: "calendar"; events: any[] }
  | { result_type: "email"; messages: any[] }
  | { result_type: "task"; tasks: any[] }
  | { result_type: "file"; files: any[] }
  | { result_type: "unsupported"; reason: string };

export interface ConnectorResult {
  connector_id: string;
  capability: string;
  operation: string;
  risk_tier: RiskTier;
  execution_id: string;
  observed_at: string;
  provenance: string;
  payload: ConnectorResultPayload;
}

export interface MissionStep {
  category: string;
  connector_id: string | null;
  results: ConnectorResult[];
  council: CouncilResult | null;
  model_text: string;
  duration_ms: number;
  // Paise, or null when the user stated no budget this turn — real value
  // from agent/preferences.py's deterministic extraction, threaded through
  // orchestrator.py, never inferred client-side.
  budget_minor: number | null;
  // Connectors that WERE eligible for this category, whether or not the
  // runtime actually called one this turn — lets the UI tell "eligible but
  // hasn't been called yet (model asked a question first)" apart from
  // "truly no eligible connector," which connector_id alone can't do.
  eligible_connector_ids: string[];
  // Connectors the runtime ACTUALLY called this turn, built from its real
  // tool_calls — never from the model's own text. Real, live-found gap
  // (see FAILURE_LOG.md F-041): with more than one eligible connector, the
  // model's own narration is not reliable evidence of what it searched —
  // it once claimed a fully-connected connector was "disconnected" when it
  // had simply never called it. Compare against eligible_connector_ids to
  // show the user, truthfully, what was and wasn't searched this turn.
  attempted_connector_ids: string[];
  // Real, verified evidence (the Agent SDK's own init message, never a
  // model claim) that a connector's MCP handshake actually failed this
  // turn. Real, live-found gap (see FAILURE_LOG.md F-044's addendum): a
  // model's report that a connector's tools never loaded was dismissed as
  // a hallucination for multiple fix cycles because nothing surfaced this
  // signal before — it was telling the truth. Distinct from an entry in
  // eligible_connector_ids that's simply missing from
  // attempted_connector_ids (which just means "not searched," not "can't
  // be searched").
  failed_connector_ids: string[];
  // 1:1 with the mission's intents — correlates this step to the real
  // agent_intent_parsed audit event app.py actually wrote for it.
  intent_id: string;
}

export interface MissionRunResponse {
  message: string;
  runtime: string;
  steps: MissionStep[];
}

export interface AgentRunResponse {
  category: string;
  connector_id: string | null;
  runtime: string;
  results: ConnectorResult[];
  council: CouncilResult | null;
  model_text: string;
  duration_ms: number;
  budget_minor: number | null;
  eligible_connector_ids: string[];
  attempted_connector_ids: string[];
  failed_connector_ids: string[];
}

export type CheckStatus = "success" | "failure" | "pending";

export interface CiCheck {
  name: string;
  status: CheckStatus;
  summary: string;
  duration_s: number | null;
  detail: string[];
}

export interface CiChecksResponse {
  generated_at: string;
  commit: string;
  overall: CheckStatus;
  checks: CiCheck[];
}

export type ReceiptStatus = "NOT_CONFIRMED" | "AWAITING_PAYMENT" | "PAID" | "BLOCKED";

export interface ReceiptGates {
  evaluated: boolean;
  allow: boolean | null;
  passed: string[];
  failed: string[];
  reasons: Record<string, string>;
}

export interface ReceiptAuthorization {
  authorization_id: string;
  signature_valid: boolean;
  expired: boolean;
  amount_paise: number;
  currency: string;
  merchant: string;
  provenance: string;
  issued_at: string;
  expires_at: string;
  audit_tip: string | null;
  consumed: boolean;
  consumed_at: string | null;
}

export interface ReceiptPayment {
  status: string;
  razorpay_order_id: string;
  razorpay_payment_id: string;
  captured_amount_paise: number | null;
  currency: string;
  last_rejection_reason: string;
}

export interface SessionReceipt {
  session_id: string;
  request_text: string;
  status: ReceiptStatus;
  merchant: string;
  items: { requested_as: string; quantity: number; title: string; unit_price_paise: number | null }[];
  confirmation: { confirmed: boolean; confirmed_at: string | null; cart_hash: string | null };
  gates: ReceiptGates;
  authorization: ReceiptAuthorization | null;
  payment: ReceiptPayment | null;
  audit: { verified: boolean; event_count?: number; broken_at_seq?: number };
}

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Empty string in dev (Vite's server.proxy in vite.config.ts forwards /api
// and /mcp to localhost:8000, so a relative path is correct there). In a
// production build where the frontend and backend are deployed as separate
// services (see render.yaml), VITE_API_BASE_URL is set at build time to the
// backend's real URL — set once, at the build step, never hardcoded here.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* empty response */
  }
  if (!res.ok) {
    throw new ApiError(res.status, body?.detail || `Request failed (${res.status})`);
  }
  return body as T;
}

export const api = {
  // Missions / agent orchestrator
  runMission: (
    message: string, sessionId?: string, continueCategory?: string | null,
    image?: { base64: string; mediaType: string } | null,
  ) =>
    request<MissionRunResponse>("/api/agent/missions/run", {
      method: "POST",
      body: JSON.stringify({
        message,
        ...(sessionId ? { session_id: sessionId } : {}),
        ...(continueCategory ? { continue_category: continueCategory } : {}),
        ...(image ? { image_base64: image.base64, image_media_type: image.mediaType } : {}),
      }),
    }),
  runAgent: (message: string, category: string) =>
    request<AgentRunResponse>("/api/agent/run", {
      method: "POST",
      body: JSON.stringify({ message, category }),
    }),

  // Connectors
  connectors: () => request<{ connectors: AgentConnector[]; detect_error: string }>("/api/agent/connectors"),
  claudeCodeConnectors: () =>
    request<{ error: string; connectors: ClaudeCodeConnector[] }>("/api/agent/claude-code-connectors"),
  connect: (id: string) => request<{ authorize_url: string }>(`/api/connectors/${id}/connect`, { method: "POST" }),
  connectWithToken: (id: string, token: string) =>
    request<{ status: string }>(`/api/connectors/${id}/token`, { method: "POST", body: JSON.stringify({ token }) }),
  disconnect: (id: string) => request<{ status: string }>(`/api/connectors/${id}/disconnect`, { method: "POST" }),
  addCustomConnector: (label: string, url: string) =>
    request<{ id: number; label: string; url: string }>("/api/connectors/custom", {
      method: "POST",
      body: JSON.stringify({ label, url }),
    }),
  discoverCustomTools: (id: number) =>
    request<{ discovered_tools: string[] }>(`/api/connectors/custom/${id}/tools/discover`, { method: "POST" }),
  enableCustomTool: (id: number, tool: string, riskTier: RiskTier) =>
    request<{ enabled: boolean }>(`/api/connectors/custom/${id}/tools/${tool}/enable`, {
      method: "POST",
      body: JSON.stringify({ risk_tier: riskTier }),
    }),

  // Real cart writes — a genuine R1 (reversible write) action, staged for
  // explicit approval, never auto-executed. See app.py's
  // propose_cart_action/approve_cart_action for the exact safety contract.
  connectorAddresses: (connectorId: string) =>
    request<{ addresses: { id: string; address_line: string; category: string }[] }>(
      `/api/agent/connectors/${connectorId}/addresses`
    ),
  proposeCartAction: (params: {
    connectorId: string; variantId: string; quantity: number; offerTitle: string; offerPriceMinor: number;
  }) =>
    request<{ proposal_id: string; risk_tier: RiskTier; summary: string; status: string }>(
      "/api/agent/cart-actions/propose",
      {
        method: "POST",
        body: JSON.stringify({
          connector_id: params.connectorId, variant_id: params.variantId, quantity: params.quantity,
          offer_title: params.offerTitle, offer_price_minor: params.offerPriceMinor,
        }),
      }
    ),
  approveCartAction: (proposalId: string, addressId: string) =>
    request<{
      status: string; items_written: { spin_id: string; quantity: number }[];
      preserved_existing_items: number; checkout_url: string;
      cart_read_skipped_reason: string | null;
    }>(`/api/agent/cart-actions/${proposalId}/approve`, {
      method: "POST",
      body: JSON.stringify({ address_id: addressId }),
    }),

  // Runtime settings
  runtimeStatus: () => request<RuntimeStatus>("/api/runtime/status"),
  setRuntimeMode: (mode: "api" | "subscription") =>
    request<RuntimeStatus>("/api/runtime/mode", { method: "POST", body: JSON.stringify({ mode }) }),
  setByokKey: (apiKey: string) =>
    request<RuntimeStatus>("/api/runtime/api-key", { method: "POST", body: JSON.stringify({ api_key: apiKey }) }),
  forgetByokKey: () => request<RuntimeStatus>("/api/runtime/api-key/forget", { method: "POST" }),

  // Eval / evidence / features
  evalResults: () => request<any>("/api/eval-results"),
  judgeResults: () => request<any>("/api/judge-results"),
  featureMatrix: () => request<any>("/api/feature-matrix"),
  auditVerify: () => request<any>("/api/audit/verify"),
  // One assembled artifact for a FreshCart session — gates, signed
  // authorization, Razorpay ledger state, audit chain — every field re-
  // verified live server-side, nothing cached. See app.py::session_receipt.
  sessionReceipt: (sessionId: string) =>
    request<SessionReceipt>(`/api/sessions/${encodeURIComponent(sessionId)}/receipt`),
  // Every real evidence artifact this repo writes, assembled into one
  // checks-run view — see app.py::ci_checks.
  ciChecks: () => request<CiChecksResponse>("/api/ci-checks"),
};

export { ApiError };
