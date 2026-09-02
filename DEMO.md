# Demo

1. Run `make dev` and open `http://127.0.0.1:5173`.
2. Show Mission (natural-language agent flow), Shop (real checkout), Connectors,
   Attack Lab, Evidence, Features, and Eval.
3. For commerce, select a real candidate, confirm quantity 1, and show the
   fresh authoritative merchant re-read → 13 pre-payment gates → Execution
   Capability → Secret Executor → real Razorpay test order.
4. Change the cart after confirmation (or run a fixed-fifty attack case) and
   show the exact gate that blocks it, and that no capability was issued.
5. Verify the audit chain and signed Authorization from the Evidence screen.

Live connector status (Swiggy OAuth, GitHub PAT, Claude subscription token)
depends on credentials configured in `.env` for the machine running the
demo — see [Connectors](docs/CONNECTORS.md) for what each one needs.
