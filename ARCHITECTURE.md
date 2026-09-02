# Architecture

OrderGuard separates probabilistic planning from financial authority.

## Request paths

The legacy `/app` commerce flow remains the financial path:

1. capture intent and constraints;
2. search eligible merchant surfaces and require explicit selection;
3. create/read the external cart;
4. freeze the user confirmation;
5. immediately before payment, re-read the authoritative merchant cart;
6. run deterministic gates and Decision Council vetoes;
7. issue an Ed25519 signed Authorization;
8. atomically consume it once and correlate a Razorpay test order;
9. verify callback/webhook evidence and reconcile `PAYMENT_UNKNOWN`;
10. append evidence to the hash-chained audit log.

The agent path is additive. `AgentRuntime` has separate Agent SDK and
Anthropic Messages API implementations. Both translate a typed
`ConnectorInvocationSpec` into their native MCP configuration and return the
same `AgentTurnResult`, including actual tool results. Eligibility runs before
spec construction. Only R0 reads are currently exposed.

## Trust boundaries

- Claude: planner and connector reader; never financial authority.
- Connector control plane: owner/auth/runtime/region/health/risk eligibility.
- Strict normalizers: connector-specific schemas; malformed data fails closed.
- Decision Council: advisory; deterministic code validates the chosen ID.
- OrderGuard gates and Authorization: sole financial authorization boundary.
- Razorpay: server-side payment executor in test mode.
- Audit/Evidence: tamper-evident proof, not an immutability claim.

`LOCAL_SINGLE_USER` is supported. `MULTI_USER_HOSTED` requires real user
authentication and isolation beyond the current owner-scoped domain model.
