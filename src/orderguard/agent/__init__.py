"""Server-side agent orchestrator: picks a real, eligible connector, drives it
through one of two interchangeable runtimes, and hands whatever comes back to
this repo's existing, unmodified verification stack (Decision Council, the 22
gates, signed Authorization, the tamper-evident audit chain).

Nothing in this package is allowed to move money or write a cart on its own —
see ``tools.py``'s R3 exclusion and ``lifecycle.py``'s approval model.
"""
