# What broke

The detailed chronological record is [FAILURE_LOG.md](FAILURE_LOG.md).

Latest checkpoint findings:

| Failure | Cause | Discovery | Fix / next action |
|---|---|---|---|
| Offline evaluation could crash inside Homebrew `uv` before project code ran | macOS dynamic network configuration was initialized by the runner even though generation needs no network | clean final reproducibility run | `eval` and `feature-matrix` Make targets now use `--offline --no-sync`; both regenerate from the installed locked environment |
| Subscription live run returned 401 | stale/revoked `CLAUDE_CODE_OAUTH_TOKEN` | real harmless Shopify Agent SDK run | runtime cleanup fixed; user must run `claude setup-token` |
| SDK generator cleanup warning after 401 | exception raised inside active async generator | same live run | break, close suspended stream, then raise; regression suite green |
| Swiggy connectors all disconnected | external OAuth session unavailable | `claude mcp list` health check | user must complete backend Swiggy OAuth |
| Swiggy normalizer guessed aliases | broad `items/products/restaurants` and price fallbacks | source audit | replaced with strict connector fixture; unknown shape raises |
| Shopify agent routing chose one store | early `break` and `stores[0]` | source audit and new regression test | unique per-store MCP specs retained through aggregation |
| Tool request counted without result | Subscription runtime ignored `ToolResultBlock` | source audit | correlate by tool-use ID; missing result fails closed |
| All six screens overflowed at 390px and 768px | six-item nav used desktop min-content sizing between breakpoints | real browser screenshots and measured scroll widths | shared responsive nav/header/table rules; all six now report zero horizontal overflow on mobile and tablet |
