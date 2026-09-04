# Failure Log

Written **as failures happen**. Never reconstructed from memory afterwards.
This file is part of the buildathon submission — the application asks
"what broke, and how you got out," and Razorpay says it is the answer they read first.

---

## Template

```
# F-000
Date:
Checkpoint:
Command:
Seed:

## What I expected
## What happened
## Root cause
## How I proved the root cause
## Fix
## Regression test added
## What I learned
## Could this happen in production?
## Remaining limitation
```

---

## F-001 — Shell variables did not survive between command blocks

**Date:** 2026-08-26
**Checkpoint:** CP-0 (caught in review, before execution)
**Command:** planned Step 6 — `sed "s/__ORDER_ID__/$OID/; s/__KEY__/$RZP_KEY_ID/"`

### What I expected
`$OID` from Step 5 and `$RZP_KEY_ID` from `.env` to be available in Step 6.

### What happened
Caught during plan review, not at runtime. Each command block runs in a fresh shell,
so `$OID` was gone and Step 6 never sourced `.env` at all — **both** variables would
have been empty. The checkout page would have rendered with blank `order_id` and `key`
and failed confusingly rather than loudly.

### Root cause
Assumed shell state persists across separate command invocations. It does not.

### Fix
Step 5 persists the order ID to `probe/order_id.txt`. Step 6 re-sources `.env`
and reloads the file with `tr -d '\n' < probe/order_id.txt`.

### Regression test
Not code — a documented rule: **every command block re-sources `.env` and reloads
state from disk.** Applies to every checkpoint.

### What I learned
Silent empty-string substitution is worse than a hard error. Prefer commands that
fail loudly on missing state.

### Could this happen in production?
Yes — the same class of bug appears in CI pipelines and shell-based deploys.

### Remaining limitation
`probe/` is gitignored, so the order ID is not reproducible from a fresh clone.
Acceptable: it is a throwaway probe artefact.

---

## F-002 — Unconditional `cp` overwrote the user's `.env`

**Date:** 2026-08-26
**Checkpoint:** CP-0
**Command:** `cp .env.example .env` (during the Gemini provider switch)

### What I expected
To refresh the `.env` template with provider-neutral field names.

### What happened
`.env` was overwritten unconditionally, destroying any credentials already
pasted into it. The earlier command in the same checkpoint was guarded
(`test -f .env || cp .env.example .env`) — the second one was not. Detected when
a field-status check showed all three credential fields empty after the user
reported having filled them in.

### Root cause
Two commands doing the same job, only one of them idempotent. The unguarded
variant silently destroyed user data instead of refusing.

### How I proved the root cause
`awk` field-status dump showed `RZP_KEY_ID`, `RZP_KEY_SECRET` and `LLM_API_KEY`
all empty, while `LLM_PROVIDER` and `LLM_MODEL` held the template defaults —
exactly the signature of a fresh copy from `.env.example`.

### Fix
`.env` is never written by a command again. Values are edited in place with
`Edit`, or the user edits the file directly. Any future template refresh must
merge, never replace.

### Regression test
Not code — a standing rule recorded here and in `SECURITY.md`:
**no command may write to `.env`.**

### What I learned
Destructive commands must be idempotent by default. `cp` over a credentials file
is a data-loss operation and should never appear unguarded. A guarded variant
existing three commands earlier made the unguarded one look safe by association.

### Could this happen in production?
Yes. This is the classic deploy-script bug that overwrites production config with
a template.

### Remaining limitation
If the Razorpay secret was pasted before the overwrite, it cannot be recovered —
Razorpay displays the secret only once. A new test key must be generated.

---

## F-003 — Gemini rejects Pydantic's `exclusiveMinimum`

**Date:** 2026-08-26 · **Checkpoint:** CP-0 · **Command:** A-7 probe

### What I expected
`Field(gt=0)` on a quantity to translate cleanly into Gemini's `responseSchema`.

### What happened
`ValidationError: properties.items.items.properties.quantity.exclusiveMinimum —
Extra inputs are not permitted`. Gemini's `types.Schema` does not accept
`exclusiveMinimum`.

### How I proved it
Probed three variants directly against `types.Schema.model_validate`:
`gt=0` (→ `exclusiveMinimum`) **REJECTED**; `ge=1` (→ `minimum`) **ACCEPTED**;
no constraint **ACCEPTED**.

### Fix
Use `ge=1` rather than `gt=0`. For integers these are semantically identical.

### Design consequence — larger than the bug
**The generation schema and the validation schema are not the same object.**
Providers support different JSON Schema subsets. The architecture must be:

    strict Pydantic model  =  validation contract (source of truth)
              ↓ derive, stripping unsupported keywords
    provider schema        =  generation hint only
              ↓ model returns output
    validate against the STRICT model, always

A provider quirk must never be allowed to weaken the validation contract.
A schema sanitiser is required at CP-1 or CP-3.

### Regression test
`tests/test_schema_compat.py` — assert the derived provider schema contains no
keyword the provider rejects, and that the strict model still rejects
`quantity=0` regardless.

### Could this happen in production?
Yes, and silently — a permissive generation schema plus weakened validation
would let invalid quantities through.

## F-004 — Gemini free key unusable; the model list lied

**Date:** 2026-08-26 · **Checkpoint:** CP-0

### What I expected
`gemini-2.5-flash`, listed by `GET /v1beta/models`, to be callable.

### What happened
- `GET /v1beta/models` returned **HTTP 200** listing 37 models including
  `gemini-2.5-flash`, `gemini-3.6-flash`, `gemini-3.7-flash`.
- `POST .../gemini-2.5-flash:generateContent` → **404**:
  *"no longer available to new users. Please update to models/gemini-3.6-flash."*
- `POST .../gemini-3.6-flash:generateContent` → **403**:
  *"Your project has been denied access."*
- Same 403 on `gemini-3.7-flash`, `gemini-3.5-flash`, `gemini-3.1-flash-lite`,
  `gemini-flash-latest`, `gemini-flash-lite-latest`, `gemini-3-flash-preview`.
- Same 404 on `gemini-2.5-flash-lite`.

Legacy models 404, current models 403. **No callable model exists for this key.**

### Root cause
Two separate problems compounding:
1. The models-list endpoint advertises models the key cannot invoke.
   **Listing is not entitlement.**
2. The key's Google project is denied access to current models — an
   account/project-level restriction, not a model-availability one.

### How I proved it
Direct REST calls, bypassing the SDK, with the minimal payload
`{"contents":[{"parts":[{"text":"Say OK"}]}]}` — removing schema handling as a
variable. Six models, two distinct error classes, zero successes.

### Secondary symptom explained
The google-genai SDK call hung for 2 minutes before timing out. That was the SDK
retrying the 404 with backoff. **The SDK hid the real error behind a timeout;
raw REST surfaced it in 0.67s.**

### Fix
Switch provider to Groq. `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL` are already
provider-neutral (D-015), so this is a config change, not a rewrite.

### What I learned
- **Never diagnose through an SDK when raw HTTP is available.** The SDK converted
  a clear 404 into an opaque 2-minute hang.
- **A model appearing in a list endpoint does not mean you can call it.**
- D-015's provider-neutral config paid for itself within hours of being written.

### Could this happen in production?
Yes — this is precisely the "provider silently changes its free catalogue"
scenario D-015 flagged. It happened the same day the decision was recorded.

### Remaining limitation
Why the project is denied access is unknown — possibly regional, account-age, or
unaccepted terms. Not worth diagnosing on a 10-day clock when a working
alternative takes 2 minutes.

---

## F-005 — urllib got 403 where curl succeeded (User-Agent block)

**Date:** 2026-08-26 · **Checkpoint:** CP-0 · **Command:** A-7 via `urllib.request`

### What I expected
`urllib.request.urlopen` to reach Groq the same way `curl` had, seconds earlier.

### What happened
`HTTPError: HTTP Error 403: Forbidden` — while the identical request via `curl`
returned HTTP 200 with valid content.

### Root cause
Groq's edge rejects Python's default `urllib` User-Agent. Not a key problem, not
a model problem, not a payload problem — a client-identity problem.

### How I proved it
Same URL, same headers, same body, same key, two clients: `curl` → 200,
`urllib` → 403. Switching to `httpx` → 200 immediately.

### Fix
Use `httpx` for all HTTP in this project. Already a dependency; sets a normal
User-Agent; also gives real timeout control.

### Regression test
Not code — a standing rule: **`httpx` only. No `urllib.request` anywhere.**

### What I learned
Third in a row where the *client* obscured the truth (F-004: SDK hid a 404 behind
a 2-minute hang; here urllib turned a working request into a 403). The debugging
protocol's "locate the boundary" step earned its place — the boundary was the
HTTP client each time, not the API.

### Could this happen in production?
Yes. UA-based filtering is common at CDN edges and fails closed with a
misleading status code.

### Remaining limitation
Not investigated whether a custom UA on `urllib` would also work. Irrelevant —
`httpx` is the standing choice.

---

## F-006 — Test account rejects international cards; UPI not enabled

**Date:** 2026-08-26 · **Checkpoint:** CP-0 · **Command:** manual checkout (A-1B)

### What I expected
`4111 1111 1111 1111` — the most widely used test card — to complete a test payment.

### What happened
Razorpay Checkout returned: *"Payment could not be completed. International cards
are not supported."* Separately, the payment-options list showed only Cards,
Netbanking, Wallet and Pay Later — **no UPI**, so the documented
`success@razorpay` UPI test handle was unusable too.

### Root cause
Two independent gaps in a fresh Indian test account:
1. `4111...` is an international Visa test number. Domestic-only accounts reject it
   regardless of test mode.
2. UPI is not enabled by default; it requires activation in Payment Methods.

### How I proved it
The rejection message names the cause explicitly, and the options list was visible
in the checkout UI.

### Fix
Use **Netbanking** for CP-0 verification — the test bank page is simulated, needs
no instrument details, and always offers an explicit Success button.
Domestic test cards (`5267 3181 8797 5449`, `4718 6091 0820 4366`) also work.

### Regression test
None — environmental, not code.

### What I learned
**"Test mode" does not mean "all instruments available."** A test account inherits
the same instrument restrictions as a live one. Test credentials found in generic
tutorials may not apply to a specific account's configuration.

### Consequence for the build
The demo merchant's checkout will offer **cards and netbanking, not UPI**, unless
UPI is activated. The video script must not promise a UPI flow that does not exist.
Recorded so the demo narration matches reality.

### Could this happen in production?
Yes — instrument availability varies by merchant configuration, and an integration
assuming UPI is present would break for merchants without it.

### Remaining limitation
UPI can be enabled in Dashboard → Settings → Payment Methods, but may require
account activation. Not pursued; netbanking is sufficient for verification.

---

## F-007 — I misunderstood what HTML escaping does

**Date:** 2026-08-26 · **Checkpoint:** CP-2 · **Command:** `pytest tests/test_xss.py`

### What I expected
That escaping a hostile product title would remove the dangerous text, so
`assert "onerror=alert(1)" not in html` would pass.

### What happened
The assertion failed. The text was still in the page.

### Root cause
**Escaping does not delete text. It neutralises markup.**
`<img src=x onerror=alert(1)>` becomes `&lt;img src=x onerror=alert(1)&gt;`.
The words `onerror=alert(1)` are still there as visible characters — but the
`<` is now `&lt;`, so the browser renders them instead of obeying them.

My test asserted the wrong property. The code was correct all along.

### How I proved it
Rendered the page with a deliberately hostile product and printed the result:

    <h3>&lt;script&gt;window.__pwned=1&lt;/script&gt;</h3>

No raw `<script>`; escaped form present. Working exactly as intended.

### Fix
Rewrote the assertions to check the *tag* is dead rather than the *words* gone:
- `"<script>window.__pwned" not in html`  ← no runnable tag
- `"&lt;script&gt;window.__pwned" in html` ← neutralised form present
- count real `<script>` tags: exactly one, ours

### Second, smaller bug in the same file
A test scanned `shop.js` for the word `innerHTML` after stripping comments, but
the stripper only removed comments at the *start* of a line. A trailing comment
`// textContent, not innerHTML` slipped through and failed the test.

Fixed by rewording the comment rather than loosening the regex. **A strict test
that occasionally needs a comment reworded is better than a clever regex that
might miss a real `innerHTML` call.**

### Regression test
`tests/test_xss.py` — 9 tests, including a real `<script>` and a real
`<img onerror=>` injected through the catalog.

### What I learned
I wrote a test based on what I *assumed* the defence did, rather than checking
what it actually did. The test failing was the system working correctly and
telling me my mental model was wrong.

Also: three of my four bugs today have been in *tests*, not in code. Tests
deserve the same scepticism as the thing they test.

### Could this happen in production?
Yes, and dangerously — a developer who believes escaping "removes" bad text
might strip escaping somewhere and assume the input was already clean.

### Remaining limitation
These tests cover titles and SKUs on the home page. Any new place that renders
shop text needs its own test.

---

## F-008 — Three UI bugs that no test would have caught

**Date:** 2026-08-26 · **Checkpoint:** CP-2

All three were found by *looking at the page*, not by running tests. Worth
recording together, because they share a cause.

### 8a. The block screen covered the shop on load

**Expected:** `<div class="blocker" hidden>` to be invisible.
**Happened:** a full-screen red PURCHASE BLOCKED panel on every page load.
**Root cause:** the HTML `hidden` attribute sets `display:none`, but my CSS rule
`.blocker{display:flex}` has higher specificity and overrides it.
**Fix:** `.blocker[hidden]{display:none !important}`.
**Regression test:** `test_block_screen_is_hidden_on_load` asserts the CSS rule exists.

### 8b. Every product card was invisible

**Expected:** cards to fade and slide in.
**Happened:** header and hero rendered; the entire product grid was blank.
**How I found it:** ran `getComputedStyle` on a card in the browser —
`opacity: 1` but `transform: matrix(0, 0, 0, 0, 0, 0)`. A zero-scale matrix.
**Root cause:** I animated the `transform` *shorthand* with `"none"` as the end
value. Motion cannot interpolate that and produced a degenerate matrix.
**Fix:** animate individual properties (`y`, `scale`, `x`) instead of a
`transform` string. This is the documented way and avoids string parsing.
**Also added a safety net:** 1.2s after load, anything still at opacity 0 or a
zero matrix is forced visible. **Animation is decoration; content must never
stay hidden because decoration failed.**

### 8c. The block screen threw when opened

**Expected:** `easing: spring({stiffness, damping})` to work.
**Happened:** `TypeError: Cannot read properties of undefined (reading '0')`.
**Root cause:** `motion@11` is the merged Motion/Framer Motion library. Springs
are now **options** (`{type:"spring", stiffness, damping}`), not easing
generators. The old Motion One `easing: spring()` form throws.
**Fix:** switched both spring animations to the option form.

### What I learned

**Tests verify logic; only looking verifies appearance.** All 76 tests passed
while the shop was showing a red error panel over an invisible product grid.

Two of the three came from **library API assumptions** — the same pattern as
F-004 (SDK hid a 404) and F-005 (urllib 403 vs curl 200). I keep assuming an
API's shape instead of checking it. The fix each time was the same: inspect the
actual runtime value.

### Could this happen in production?
Yes. A CSS specificity bug or a library version change can hide an entire page
while every test still passes.

### Remaining limitation
Visual checks are manual. There is no screenshot-diffing test, and adding one is
not worth the remaining time.

---

## F-009 — I claimed Swiggy had no MCP server. It does.

**Date:** 2026-08-26 · **Checkpoint:** CP-2 (research, not code)

### What I expected
A web search for "Zomato Swiggy Zepto MCP server" to surface an official server
if one existed.

### What happened
The search returned only Apify scrapers and unofficial reverse-engineered repos.
I concluded — and told the user — that *"Zomato, Swiggy, Zepto, Blinkit and
BigBasket have published nothing."*

**That was wrong.** The user pushed back with a specific URL. Fetching
`mcp.swiggy.com` directly showed an **official Swiggy MCP server**: 49 tools
across three servers (Food 18, Instamart 19, Dineout 12), including
`Order placement` and payment tools.

### Root cause
I treated absence from search results as evidence of absence. Search indexes
developer subdomains poorly, and `mcp.swiggy.com` is a subdomain, not a
GitHub repo.

### How I proved the correction
Direct `WebFetch` of the documented URL. Two pages: the overview confirmed
official status and the tool count; the Food reference confirmed the tool
categories and the access model.

### What it changes
Nothing structural — access is **invite-only whitelist onboarding with a partner
contract**, and there is **no sandbox or test mode**. So it is unusable for this
project on two counts: we cannot get access in 9 days, and every call would be a
real order with real money, which violates our own test-mode-only limitation.

### What it changes in framing (this part matters)
Swiggy shipping payment and order-placement tools to AI agents makes this
project's question concrete rather than speculative:

> Swiggy gave agents 49 tools including order placement and payment.
> What checks the agent's work before it spends?

Decision: mirror Swiggy's six tool categories (discover / cart / payment /
order / track / support) in our own demo-store MCP server, so the guard layer
sits in front of the same *shape* of surface that is actually shipping. Cite
Swiggy as the reason, never as an integration.

### What I learned
**Same failure as F-004.** There I trusted a models-list endpoint over a real
request; here I trusted search results over a real fetch. The rule stands and I
did not follow it: *when a specific URL is checkable, check it.*

Also: the user was right and I was confidently wrong. Worth recording, because
the correction came from being challenged rather than from my own checking.

### Could this happen in production?
Yes — concluding a capability does not exist because it was not indexed is how
teams rebuild things that already ship.

### Remaining limitation
Zomato's server is still unverified. Community write-ups describe one, but I have
found no first-party documentation URL. Treat as unknown, not as absent.

# F-010 — Twelve gates, and none of them checked the price
ID: F-010
Date: 2026-08-27
Checkpoint: CP-4 review
Command: pytest tests/test_cart_verifier.py -v
Seed: n/a

## Expected behaviour
The pre-payment gates were described as catching a cart that does not match
what the user approved. The user approves a product *at a price* — the number
on screen when they pick an offer.

## Actual behaviour
The quoted price was thrown away. CartExpectation held variant_id and quantity
only, and the sole money check was `cart_total <= maximum_total_paise`.
A merchant quoting Rs 12 during search and charging Rs 80 in the cart passed
all eleven gates, as long as six bananas stayed under a Rs 500 cap.

## Root cause
I read "within cap" as "the money is checked". It is not the same claim.
A cap is a ceiling: it permits every price beneath it, including one the user
was never shown. Identity checks (merchant, variant, quantity, currency) all
pass, because nothing about the item's identity changed — only its price.

## Proof
tests/test_cart_verifier.py::test_a_silent_price_rise_under_the_cap_is_blocked
Written first, and it passed against the old code, which is what proved the
hole: the comparison reported `matches == True` on an overcharged cart.

## Fix
ApprovedCartLine.unit_price_paise, required with no default. Exact equality
against the observed unit price. New gate G_PRICES_MATCH. See D-024.

## Regression test
Two: one at the comparison level, one at the gate level. They are separate on
purpose — a comparison that notices a mismatch is not the same thing as a
checkout that stops.

## Lesson
The gate list was frozen at CP-0 from reasoning, before any real merchant data
existed. Freezing a contract early is right; treating it as complete is not.
This hole only became visible when a real store quoted a real price into a real
cart. Design review found nothing here. The integration did.

## Production relevance
High, and it is the exact shape of a real agentic-commerce attack. An agent that
enforces only a spending cap can be walked up to that cap by any merchant it
shops at. The cap is the user's maximum, not their agreement.

## Remaining limitation
Exact equality means a legitimate price change between search and cart also
blocks. That is the correct default — it stops and asks — but it will produce
false blocks on stores with frequent repricing, and we have not measured how
often that happens.

# F-011 — The new price gate blocked the happy path on its first live run
ID: F-011
Date: 2026-08-28
Checkpoint: CP-4
Command: full guarded flow against slurrpfarm.com
Seed: n/a

## Expected behaviour
G_PRICES_MATCH (F-010's fix) should pass on a correct cart: quoted Rs 94.05,
charged Rs 94.05.

## Actual behaviour
  G_PRICES_MATCH FAILED - "was quoted at 9405 paise each but the cart
  charges [None]"
9 of 12 gates passed on a cart that was entirely correct.

## Root cause
I wrote the check against CartLine.unit_price_paise. That field is optional and
documented as optional for a good reason: a store may quote a line total that
does not divide evenly by the quantity, such as a discount spread over three
units, so the model keeps the line total authoritative rather than inventing a
rounded per-unit price. Every real Shopify cart line therefore has
unit_price_paise = None.

I added the check without reading the field's own comment explaining why it
could not be relied on.

## Proof
tests/test_cart_verifier.py::test_price_check_works_on_a_line_with_no_unit_price

## Fix
Compare LINE TOTALS. Approved unit price x approved quantity, against the
observed line total. Exact integer arithmetic, no division, and it uses the
field the model guarantees is populated.

## Regression test
Two: the live cart shape with no unit price, and a line total that does not
divide evenly (299 paise over 3 units).

## Lesson
A false block is not a safe failure. It looks like caution, but a gate that
fires on correct carts is a gate someone will switch off, and then it is not
protecting anything. The unsafe direction is not the only direction that
matters.

Second lesson: F-010 and F-011 are the same mistake one level apart. In F-010 I
assumed the cap checked the price. In F-011 I assumed a field held a value. Both
were assumptions about data I had already seen and had not re-read.

## Production relevance
High. The whole class of "safety checks that get disabled because they cry wolf"
starts exactly here.

## Remaining limitation
Exact equality still blocks a legitimate discount applied after selection. That
is deliberate — the cart no longer matches what was approved, so it stops and
asks — but it is a real usability cost on stores that reprice or apply
automatic offers, and we have not measured how often that happens.

# F-012 — Right conclusion, wrong evidence, on the third repeat of the same mistake
ID: F-012
Date: 2026-08-28
Checkpoint: CP-8 (connectors)
Command: curl -X POST https://mcp.zomato.com
Seed: n/a

## Expected behaviour
The connector directory should record what is true about Zomato, with evidence
another person can re-run.

## Actual behaviour
I wrote: "No public endpoint resolved at mcp.zomato.com."
The user showed me a screenshot of Zomato as a VERIFIED connector in Claude's
own directory, with an Add button.

The real endpoint is https://mcp-server.zomato.com/mcp. It answers 401, it
publishes both OAuth discovery documents, and Zomato documents it in their own
README with install instructions for Claude, VS Code and Postman.

## Root cause
I guessed a hostname (mcp.zomato.com), got no answer, and wrote the failed guess
down as a finding. A hostname I invented not resolving is evidence about my
guess, not about Zomato.

Two contributing errors:
  - I ran an MCP registry search that returned zero results and read that as
    "nothing exists". That tool does not index the Claude connector directory.
    Same shape as F-004: an empty result treated as an answer.
  - I never checked the manifest repo I had already cited. The install URL was
    in its README the whole time.

## Proof
    POST https://mcp-server.zomato.com/mcp                   -> 401
    GET  /.well-known/oauth-authorization-server             -> 200
    GET  /.well-known/oauth-protected-resource               -> 200
    gh api repos/Zomato/mcp-server-manifest/contents/README.md

## Fix
Zomato entry rewritten with the real endpoint, the 401, the OAuth discovery, and
their own words. New field in_assistant_directory, because "a person can use
this in Claude" and "our app may connect to it" are different questions and I
had collapsed them.

## Regression test
test_zomato_is_excluded_by_their_rules_not_by_our_ability
test_reachable_in_claude_is_not_the_same_as_usable_by_us
test_every_gated_connector_names_its_endpoint  - a gated entry must name a real
  https endpoint, so a failed guess can never again be recorded as a finding.

## Lesson
The status was RESTRICTED before and is RESTRICTED after, so nothing in the
product changed. That is exactly why it is worth logging: I got the right answer
for a reason that was false. Anyone reading my note would have concluded Zomato
had shipped nothing, which is the opposite of the truth.

Third time in this project (F-004, F-009, F-012). All three: absence of a
result treated as evidence of absence. The rule I keep failing to apply is to
check the primary source I have already cited before writing a conclusion.

## Production relevance
Zomato ships restaurant discovery, cart creation, ORDER PLACEMENT and QR-code
payment to AI agents, live, in India. Their own example prompts include "Order
my usual coffee" and "Reorder from my last order." That is memory-driven
autonomous spending, shipped. It is the strongest available argument for why
this project exists, and I nearly filed it as "does not exist".

## Remaining limitation
We still cannot connect. Their redirect-URI whitelist covers Claude, ChatGPT,
VS Code and Postman, not localhost, and their README says no third-party apps.
Both remain true; only the evidence was wrong.

# F-013 — Shopify accepts a price filter and ignores it
ID: F-013
Date: 2026-08-28
Checkpoint: CP-4 (location-aware search)
Command: probe against slurrpfarm.com with filters.price.max = 30000
Seed: n/a

## Expected behaviour
search_catalog's documented input schema includes
  filters.price = {min, max}  "in ISO 4217 minor units (e.g., 5000 = $50.00)"
Asking for products under Rs 300 should return products under Rs 300.

## Actual behaviour
10 results came back. TWO of them were above Rs 300 (Rs 311 twice). The filter
was accepted without error and had no effect on the result set.
Passing a Bangalore postal_code also changed nothing in the results.

## Root cause
Not our bug. The endpoint advertises a capability in its schema that this store
does not honour. Whether it is per-store, silently ignored, or only applies to
some catalogues, we do not know — and that uncertainty is the point.

## Proof
    filters={"price":{"max":30000}} -> 10 results, 2 above 30000

## Fix
We do not send filters.price at all, and the budget is computed in our own code
(search.py sets within_budget from line_total <= budget_minor). A regression
test asserts the outgoing request contains no "filters" key.

## Regression test
tests/test_discovery.py::test_the_store_is_never_asked_to_enforce_a_budget

## Lesson
A published schema describes what a server will ACCEPT, not what it will DO.
I nearly wired the user's spending limit straight into that parameter, which
would have shipped a budget filter that silently does not filter — the user
believing their limit was applied at search time when nothing enforced it.

This is the same family as F-010: a check that appears to exist and does not.
The difference is that here the false check would have been someone else's.

## Production relevance
Direct. Never delegate a user's spending limit to a merchant's API. The merchant
has no obligation to enforce it, no incentive to, and no way to be held to it.
Limits belong on our side of the wire.

## Remaining limitation
Location is still passed through as buyer context because stores that DO honour
it will give better results. It changed nothing on the store we tested, so no
claim is made that it improves relevance — and nothing in a safety check depends
on it.

# F-014 — Answering the question asked the question again
ID: F-014
Date: 2026-08-28
Checkpoint: CP-3
Command: make app, then "Find me a healthy breakfast on Zepto under Rs 400"

## Expected behaviour
"How many would you like?" answered with "2" should record a quantity of 2.

## Actual behaviour
The same question came back. Answering again asked BOTH questions at once:
  "How many healthy breakfast would you like? What is the most you would like
   to spend, including delivery?"
The user could not get out of the loop. Found by the user, in the running app.

## Root cause
continue_session appended the raw reply and recompiled the whole text:
    "...under Rs 400\nUser clarification: 2"
A lone "2" beside a request that already contains "400" is ambiguous, so the
model did not bind it to quantity, and _missing_fields asked again.

We knew which field we had asked about and threw that knowledge away.

## Proof
compile_intent on "...under Rs 400\nUser clarification: 2" -> still asks quantity
compile_intent on "...under Rs 400\nQuantity for item 1: 2" -> quantity 2, cap 40000

## Fix
The session keeps pending_fields. label_answer(field, answer) attaches the reply
to the question it answers, and parses the digits itself.

## Regression test
tests/test_intent_compiler.py::test_a_bare_answer_is_bound_to_the_question_it_answers

## Lesson
Code decided WHICH question to ask, then handed the answer back to the model as
unlabelled text and hoped. If deterministic code is good enough to choose the
question it is good enough to read the answer.

## Production relevance
High. A clarification loop with no exit is worse than no clarification.

## Remaining limitation
Free-text answers ("a couple") still go to the model. Only digits are parsed here.


# F-015 — It offered cheese for a pizza order, from a shop it cannot use
ID: F-015
Date: 2026-08-28
Checkpoint: CP-3
Command: make app, then "Order 2 pizza from La Pinoz"

## Expected behaviour
La Pinoz has no agent surface. Say so immediately.

## Actual behaviour
  1. asked for a budget
  2. searched five grocery stores
  3. offered a mozzarella block from Two Brothers at Rs 530
  4. only at SELECTION did it notice the shop was wrong
Same for "healthy breakfast on Zepto" and "chicken momos from Swiggy".
Every step worked. The result was nonsense. Found by the user.

## Root cause
Two mistakes.
  1. The named shop was never checked for reachability. The only merchant check
     was at selection time, comparing an offer's store to the intent — far too
     late to be useful.
  2. CompilationResult dropped the draft merchant when the request was
     incomplete, so even after adding the check it could not run until every
     other question had been answered. We asked a budget for a shop we were
     never going to be able to use.

## Proof
"Order 2 pizza from La Pinoz" -> "What is the most you would like to spend?"
then 24 grocery offers, none of them pizza.

## Fix
src/orderguard/merchants.py resolves a named shop BEFORE searching, into
SHOPPABLE / BLOCKED / NOT_REACHABLE / UNKNOWN, each with a reason in the user's
words. CompilationResult now carries draft_merchant so the check runs on an
incomplete request. Search is refused outright for a blocked shop.

## Regression test
tests/test_merchants.py, nine cases including La Pinoz, Swiggy, Zomato, Zepto

## Lesson
The interesting failure was not a crash. Every component did its job and the
product was still wrong, because nobody asked "can we even shop here?" first.
Component tests cannot catch that; only running the thing can.

Also: refusing early IS the feature. "I cannot shop La Pinoz, here is why, here
is what I can do" is a better answer than a confident list of the wrong products.

## Production relevance
Direct. An agent that silently substitutes a shop the user did not ask for is
the exact behaviour this project exists to prevent, and ours was doing it.

## Remaining limitation
A shop named as a plain word with no website cannot be checked beyond our known
list. We say so rather than guessing.


# F-016 — Asking for pizza returned mozzarella
ID: F-016
Date: 2026-08-28
Checkpoint: CP-3

## Expected behaviour
If no store sells what was asked for, say so.

## Actual behaviour
"pizza" returned three products, top of them a mozzarella block. Relevance was
computed and scored 0.0, then displayed anyway because rank() only sorted.

## Root cause
Relevance was used for ordering and never as a floor. A list sorted worst-last
is still a list of wrong answers when everything in it is wrong.

## Fix
Offers with relevance 0 are dropped when anything relevant survives. Search
reports irrelevant_dropped and nothing_matched.
  "pizza"         -> 0 shown, 3 dropped, nothing_matched=True
  "millet cereal" -> 5 shown, 19 dropped
Note the second line: even a good search was showing 24 results of which 19 were
unrelated.

## Regression test
tests/test_discovery.py::test_products_unrelated_to_the_request_are_dropped

## Lesson
Ranking is not filtering. Sorting the wrong answers to the bottom still presents
them as answers.

## Production relevance
An agent that offers cheese for pizza will eventually have one bought.


# F-017 — An outage was reported as the user's mistake
ID: F-017
Date: 2026-08-28
Checkpoint: CP-3

## Expected behaviour
When the language provider is unreachable, say so.

## Actual behaviour
  "I could not safely understand that order. What would you like to buy?"
shown for a perfectly clear request, because the provider had returned an error.
The user retyped it, which failed the same way.

## Root cause
compile_intent caught LLMUnavailable in the same except clause as
ValidationError, so a service outage and a genuinely unparseable request
produced identical text.

## Fix
Separate handlers. An outage now says the service could not be reached, states
that nothing was ordered, and asks the user to try again shortly.

## Regression test
tests/test_intent_compiler.py::test_a_provider_outage_does_not_blame_the_user

## Lesson
Failing safely and failing honestly are different requirements. Both refusals
were safe; one of them lied about whose fault it was, and sent the user round a
loop that could not succeed.

## Production relevance
"Nothing was ordered" is the sentence a user needs when a purchase flow errors.
Ours was withholding it.

# F-018 — Every shop on the internet was called "Google"
ID: F-018
Date: 2026-08-28
Checkpoint: CP-3 (web search)
Command: SEARCH_PROVIDER=serper, then search_web("roasted cashews 200g")

## Expected behaviour
Results labelled with the shop selling the product.

## Actual behaviour
Eighteen results across three queries, every single one labelled "Google".
Real prices, real products, and no idea where any of them came from.

## Root cause
serper's /shopping entries look like this:

    {"title": "Happilo Premium Cashews Roasted and Salted",
     "source": "Amazon.in",
     "link": "https://www.google.com/search?ibp=oshop&...",
     "price": "₹329"}

The merchant is in `source`. `link` is a Google Shopping URL. I derived the shop
from the link, so every result resolved to google.com.

I had only ever tested this against results I constructed by hand from a
WebSearch summary, where links were merchant URLs. The provider's actual
response shape was never checked. Same mistake as F-004 and F-013: I trusted
what a response ought to look like instead of printing one.

## Proof
    top-level keys: ['searchParameters', 'shopping', 'credits']
    first item source: "Amazon.in"   link: "https://www.google.com/search?..."

## Fix
`source` is now preferred over the link, because it is the only field that is
not a guess. SerperProvider also queries /search alongside /shopping and merges:
shopping supplies the structured price and the merchant name, organic supplies
links that go to the shop rather than to Google. Duplicates are collapsed by
title, keeping the shopping entry because it carries the price.

Now: Fitfire Consumer, Amazon, JioMart Grocery, Zepto, Cape Fresh, kindlife.in,
Pureheart, Reliance Digital, Gadgets Now, FirstCry India, Myntra, Nalanda
Enterprises.

## Regression test
tests/test_websearch.py::test_the_merchant_named_by_the_search_engine_wins_over_the_link

## Lesson
The user's complaint was "you hardcoded the sites". The code was not restricted
at all — but the OUTPUT looked identical to a hardcoded list, because everything
said "Google". A correct implementation that presents wrongly is indistinguishable
from a wrong one, and arguing about the code would not have fixed it. Running it
did.

## Production relevance
Price comparison without the merchant name is not price comparison.

## Remaining limitation
Shopping links still point at Google Shopping rather than the merchant's own
page. Organic results supply real merchant links, but they are not always the
same product as the priced shopping entry.

# F-019 — My fix for F-014 turned three items into a quantity of 111
ID: F-019
Date: 2026-08-28
Checkpoint: CP-3
Command: make app -> "Find me a healthy breakfast under 400"
         -> "1 bowl oats 1 dozen eggs and 1 kg apple"

## Expected behaviour
Three items: 1 oats, 12 eggs, 1 apple.

## Actual behaviour
    items: [('healthy breakfast', 111)]
    offers=0  dropped=25
One item, quantity ONE HUNDRED AND ELEVEN, and no results. Found by the user.

## Root cause
Mine, and it was one line. label_answer did:

    digits = re.sub(r"[^\d]", "", text)

which strips every non-digit and glues the rest together. "1 bowl oats 1 dozen
eggs and 1 kg apple" leaves "111".

The deeper error was assuming the answer to "how many?" is always a quantity.
Here the user was not answering the question — they were restating the order,
which is a perfectly normal thing to do and the code had no way to express it.

## Proof
    label_answer("items[0].quantity", "1 bowl oats 1 dozen eggs and 1 kg apple")
      -> "Quantity for item 1: 111"

## Fix
Find each number separately with a findall. Exactly one number means it answers
the question. More than one means it does not, so the reply is passed through as
a restatement and the whole request is read again.

    items: [('oats', 1), ('eggs', 12), ('apple', 1)]

## Regression test
tests/test_intent_compiler.py::test_several_numbers_in_one_answer_never_become_one_quantity

## Lesson
A quantity of 111 is exactly the failure this project exists to catch, and I
wrote it into the intent compiler while fixing a different bug. The gates would
have caught it at the cart — 111 packs is far over any cap — but the intent
itself was already wrong, and everything downstream is checked against the
intent. A bad intent is not something the gates can see past.

Fixing a bug is when to be most careful, not least.

## Production relevance
Direct. The whole product rests on the intent being what the user actually said.

## Remaining limitation
Worded quantities ("a couple", "half a dozen") still go to the model.


# F-020 — "healthy breakfast" returned nothing at all
ID: F-020
Date: 2026-08-28
Checkpoint: CP-3

## Expected behaviour
Something useful for a category the shops clearly stock.

## Actual behaviour
    'healthy breakfast'  offers=0  dropped=25
A blank panel reading "No usable options were returned."

## Root cause
The relevance floor added in F-016 works on word overlap. "healthy breakfast" is
a CATEGORY; no product is titled that, so every one of the 25 results scored 0
and all were dropped. Before F-016 the same search showed 25 wrong products;
after it, nothing. Neither is an answer.

## Fix
When nothing matches, the search now returns what those shops DO sell, so the
app can name real products and ask. Blueberry Millet Pancake Mix, Multi-seed
Millet cookies, Berry Crunch Ragi Stars.

## Regression test
tests/test_discovery.py::test_when_nothing_matches_it_names_what_the_shops_do_sell

## Lesson
F-016 replaced wrong answers with no answer and I called it fixed. A refusal is
only safe when it leaves the user somewhere to go. "I found nothing" is a dead
end; "no exact match, these shops sell these things" is a question.

## Remaining limitation
Matching is still lexical. "Healthy breakfast" and "millet porridge" are related
only in a person's head, and nothing here knows that.

# F-021 — "No usable options" for anything a normal person would buy
ID: F-021
Date: 2026-08-28
Checkpoint: CP-3
Command: make app -> "Order 2 momos under 500", "find 1 kg onion"

## Expected behaviour
Something useful for an ordinary shopping request.

## Actual behaviour
A blank panel: "No usable options were returned. I have not changed a cart."
Repeatedly, for momos, onions, eggs, chicken. Found by the user, who asked the
right question: "it several times just give no results but how".

## Root cause
Not a bug in any function. A mismatch between what the app OFFERS and what it
can REACH.

The five shops we can transact with are speciality D2C brands:
    Slurrp Farm    kids' millet food
    Nourish You    quinoa and superfoods
    Two Brothers   organic ghee and atta
    Blue Tokai     coffee
    Sleepy Owl     coffee

    'momos'   -> 0 results
    'onion'   -> 0 results
    'eggs'    -> 0 results
    'chicken' -> 0 results

None of them sell any of it, and they never will. But the app opens with "Tell
me what you need", which promises a supermarket. Every ordinary request was
always going to fail, and the failure said nothing about why.

Every previous fix in this area made the message better while leaving the
mismatch untouched. F-016 stopped showing wrong products; F-020 offered
substitutes from the same five shops. Neither could help someone who wants an
onion.

## Fix
When no shop we can buy from stocks the item, the search now falls back to the
open web and returns links, together with a sentence naming the shops it tried.

    "None of the shops I can buy from sell momos. I searched Slurrp Farm,
     Nourish You, Two Brothers, Blue Tokai, Sleepy Owl. Here is what the web
     shows — you can open these yourself; I cannot add them to a cart."

        BigBasket            Rs 278.60   Wow! Momo Veg Premium Momos
        Zepto                Rs 200.00   Prasuma Mixed Vegetable Momos
        Blinkit              Rs 160.00   Hello Tempayy High Protein Veg Momos
        Meatigo by Prasuma   Rs 250.00   Prasuma Chicken Momos

Web rows get an "Open" link, never a "Choose" button. Nothing there can enter a
cart, which is the same rule as everywhere else.

## Regression test
tests/test_app.py::test_an_item_no_shop_stocks_falls_back_to_the_web

## Lesson
I fixed the wording of this failure three times without asking why the failure
kept happening. F-016, F-020 and this are the same complaint from the user, and
only this one looked past the message to the catalogue behind it.

The honest framing is also the better product: naming the five shops tells the
user exactly what kind of thing this can buy, which no amount of polish on "no
results" ever would.

## Production relevance
An assistant that cannot say why it failed cannot be trusted when it succeeds.

## Remaining limitation
Web results are links. We still cannot buy an onion, and the app now says so
plainly instead of implying otherwise with an empty panel.

# F-022 — Web search threw away 34 of 40 results, and never saw the budget
ID: F-022
Date: 2026-08-28
Checkpoint: CP-3

## Expected behaviour
"Under Rs 500" should mean the results shown are under Rs 500, and anything
dearer should be shown as dearer rather than silently mixed in.

## Actual behaviour
Two separate faults, both invisible until the numbers were printed.

  1. serper's /shopping endpoint returns about FORTY results. We sliced the
     first six in Google's own relevance order and discarded the other 34
     before doing anything with them.
  2. `search_web` had no budget parameter at all. The user's stated cap lived on
     the intent and was never passed. Nothing on screen said which results they
     could afford.

Together: six arbitrary results, unranked, unlabelled. The user's complaint was
that web search "is not working effectively", which was exactly right.

## Proof
    num=3 -> 40 results      num=10 -> 40 results
    num=6 -> 40 results      num=20 -> 40 results
and search_web's signature had no budget argument.

## Fix
Every result the endpoint returns is now kept and ranked, not the first few.
`search_web` takes quantity and budget_paise. Each result carries
line_total_paise (price x quantity) and within_budget. Affordable first, then
cheapest, then the ones with no price at all.

Over-budget results are SHOWN and marked, never hidden: someone asking for
onions under Rs 100 still wants to know the 10 kg sack is Rs 460.

A result with no price is `within_budget = None`, not True. Not knowing is not
the same as it fitting.

    1 x laptop, budget Rs 1,000
       -  ?     Myg          Laptop Prices in India
       -  ?     Croma        Buy Latest Laptop Models
    Rs 10,000 OVER  Vijaysales
    Rs 20,000 OVER  Amazon
    -> Nothing found is within Rs 1,000.00. The cheapest is Rs 10,000.00.

## A second bug found while fixing the first
The first version reported "6 of these are within Rs 500. 6 cost more and are
marked" beside six affordable rows. The count was taken over everything fetched
rather than over what survived the limit, so it named results that were not on
screen. A number the user cannot see is worse than no number.
Test: test_the_over_budget_count_matches_what_is_on_screen

## Regression tests
tests/test_websearch.py, six cases covering ranking, quantity, over-budget
marking, unpriced results, the count, and no budget stated.

## Lesson
I built this feature, tested it, and shipped it without ever asking how many
results the endpoint actually returns. Six looked like a reasonable page size;
it was a 15% sample of the data, taken before the only filter that mattered.

Slicing before ranking is the general form of this mistake, and it is quiet:
the output looks plausible either way.

## Production relevance
A shopping assistant that ignores the budget the user just stated has ignored
the only instruction they gave about money.

## Remaining limitation
Prices come from search snippets and are whatever Google reports — including
wholesale per-piece rates that look implausibly low next to retail packs. We
show what the source says and do not second-guess it, because inventing a
plausibility rule would be guesswork with the user's money.

# F-023 — Only the first request ever searched anything
ID: F-023
Date: 2026-08-28
Checkpoint: CP-3
Command: make app -> ask for a laptop, then an iPhone, then an iPad

## Expected behaviour
Every request searches.

## Actual behaviour
The FIRST request worked. Every one after it returned
    "I could not find that in the shops I can buy from."
with no web results, for iphone, new phone and ipad — while the identical
request through the API returned six web results each time. Found by the user,
who noticed the first one worked and the rest did not.

## Root cause
One line of browser state.

    const state = { ..., currentItem: 0, ... };

`currentItem` tracks which item of the request is being searched, and it was
initialised at PAGE LOAD and never again. After one single-item order it was
left at 1. The next request has one item, so:

    if (state.currentItem >= intent.items.length) { renderOffers(); return; }
       1 >= 1  ->  render results without searching at all

No request was made. The results panel was empty, so the UI fell through to its
own default string — which blamed the shops for a bug in the client.

The backend was correct the whole time. Every check I ran was through the API,
where the bug cannot appear.

## Proof
    js> state.currentItem
    1                      <- after one completed request

## Fix
resetSearchProgress() at the start of startRequest and continueRequest. The
default message also now distinguishes "the shops had nothing" from "no search
ran", because the second is our fault and must not be reported as the first.

## Verified
Laptop then iPhone in the same session, both returning live results with budget
labels.

## Lesson
I verified this feature five times through TestClient and never once through the
browser, so a whole class of bug — state that survives between requests — was
invisible to every check I made. The user found it in under a minute by doing
the obvious thing: asking for a second item.

Testing the API is not testing the app.

## Production relevance
Silent skipped work is the worst failure shape: no error, no log, a plausible
empty screen.

## Remaining limitation
Client state is still ad-hoc globals rather than something derived from the
session. Another variable could rot the same way.

# F-024 — Coffee-scented face wash outranked actual coffee
ID: F-024
Date: 2026-08-28
Checkpoint: CP-3

## Expected behaviour
"coffee beans" returns coffee.

## Actual behaviour
    Coffee Face Wash - 100 ml       Rs 349   mCaffeine
    Coffee Body Polishing Oil       Rs 445   mCaffeine
    Coffee Body Scrub - 100 g       Rs 449   mCaffeine
Blue Tokai and Sleepy Owl, both actual roasters, contributed nothing.

## Root cause
Relevance was scored on the product TITLE alone. Blue Tokai names its coffee by
estate — "Attikan Estate", "Silver Oak Café Blend" — so not one word of the
request appeared in the title and every result scored zero and was dropped by
the F-016 filter. mCaffeine sells coffee-SCENTED skincare, whose titles do say
"Coffee", so they scored 0.5 and survived.

The shop selling the actual thing was eliminated; the shop selling a smell of it
was promoted.

## Proof
    Blue Tokai raw results: Attikan Estate, Attikan Estate, Silver Oak Café Blend

## Fix
Relevance now considers the SHOP as well as the product. Each store declares
what it sells in plain words; if the request overlaps that, its products get a
floor of 0.6 of the shop match. A title match still scores higher, because the
shop only tells you the aisle while the title tells you the product.

    'coffee beans' -> 15 offers, 0 dropped
        Attikan Estate        Rs 700   Blue Tokai
        Silver Oak Cafe Blend Rs 750   Blue Tokai

"pizza" still correctly returns nothing: no shop declares pizza, so no shop
match rescues anything, and the web fallback takes over.

## Regression test
tests/test_discovery.py::test_a_shop_that_sells_the_thing_counts_even_when_the_title_does_not_say_so

## Lesson
Lexical matching on titles assumes sellers name products after their category.
Premium sellers do the opposite — the whole point of "Attikan Estate" is that it
is not "coffee". The naming style that signals quality is exactly the style that
defeats a keyword filter.

Knowing WHY a shop was searched is information the ranker was throwing away.

## Production relevance
An agent that cannot find a product because the seller named it well is not
useful to the sellers most worth buying from.

## Remaining limitation
Store subject matter is hand-written. A new store needs its words filled in, and
a test now fails if they are left blank.

# F-025 — A shop that smells of coffee hijacked every search for coffee
ID: F-025
Date: 2026-08-28
Checkpoint: CP-3
Command: make app -> "order 1 cup coffee", budget Rs 200

## Expected behaviour
Coffee.

## Actual behaviour
    Coffee Face Wash - 100 ml                  mCaffeine
    Coffee Body Scrub - 100 g                  mCaffeine
    Coffee Hydrogel Under Eye Patches          mCaffeine
    Caramel Eclairs Coffee Body Scrub          mCaffeine
    Coffee Face Mask - 100 gm                  mCaffeine
Five results, all skincare. Found by the user, one commit after I claimed the
same class of bug was fixed.

## Root cause
Mine, in data I had written by hand an hour earlier. The store registry says
what each shop sells, and I had written:

    Store("mcaffeine.com", "mCaffeine", "beauty",
          "coffee scrub body wash face serum")

mCaffeine does not sell coffee. It sells skincare that smells of coffee. That
one word did two things: it routed the query to a beauty brand, and it gave
every mCaffeine product a shop-match score for "coffee".

Worse, the F-024 fix made it stronger. Products whose titles say "Coffee Face
Wash" scored on the title AND the shop, so the fix that was meant to rescue
Blue Tokai's "Attikan Estate" also promoted skincare above it.

## Proof
    for_query("order 1 cup coffee") -> [Blue Tokai, Sleepy Owl, mCaffeine]
    mCaffeine sells: "coffee scrub body wash face serum"

## Fix
`sells` must answer "you can buy ___ here". A scent, an ingredient or a
marketing word does not qualify. mCaffeine now declares
"scrub bodywash facewash serum lotion sunscreen".

    'order 1 cup coffee' -> Sleepy Owl Instant Coffee Rs 599
                            Blue Tokai Attikan Estate  Rs 700
    'coffee scrub'       -> mCaffeine, correctly
    'cold brew'          -> Blue Tokai and Sleepy Owl

## Regression tests
test_a_shop_may_not_claim_a_category_it_only_smells_of
test_no_beauty_shop_claims_a_food_or_drink_category — sweeps every beauty,
health and lifestyle shop for edible words, so the next store added cannot
repeat it.

## Lesson
F-024 and F-025 are the same failure from opposite directions. In F-024 a shop
that sold the thing was missed because its titles did not say so. In F-025 a
shop that did not sell the thing won because its titles did.

The first fix trusted my hand-written store descriptions, and I had put a lie in
one of them. Adding a data-driven ranking signal means the data is now load
bearing, and hand-written data is exactly where a plausible lie survives review:
"coffee scrub body wash" reads perfectly true, because their products ARE called
that.

## Production relevance
Ingredient-as-brand is everywhere in D2C — coffee scrubs, oat cleansers, milk
soaps, chocolate protein. A catalogue router that cannot tell an ingredient from
a product will send every food search into cosmetics.

## Remaining limitation
The guard test knows a fixed list of edible words. A shop selling "rose water"
face mist would still hijack a search for rose water, and nothing here would
notice.

# F-026 — A shoe shop's chukka boot outranked "no running shoes here"
ID: F-026
Date: 2026-08-28
Checkpoint: CP-3

## Expected behaviour
"running shoes" and "water bottle" should return real matches or say plainly
that these 24 D2C shops do not sell them.

## Actual behaviour
    'running shoes' -> TED CHUKKA SHOES, Rs 15,500, Nappa Dori (a bag brand)
    'water bottle'  -> Rice Dewy Bright Face Wash With Rice Water, Mamaearth
Both look like real results. Neither is what was asked for. Found by stress
testing ten realistic conversational queries the user asked me to check.

## Root cause
No store declares "shoes" or "bottle" in what it sells, so for_query fell back
to searching all twenty-four — a blind hunch, not a routed choice. The relevance
floor from F-016 only required ONE shared word to survive. "shoes" alone matched
a chukka boot; "water" alone matched rose water. A coincidental single-word hit
across an unrelated 24-shop catalogue looked exactly like a genuine find.

## Proof
    for_query("running shoes") -> ALL (no store's `sells` overlaps "running"
    or "shoes")
    Nappa Dori title "TED CHUKKA SHOES" ∩ {"running","shoes"} = {"shoes"}
    relevance = 0.5 under the old rule -> kept. Should not have been.

## Fix
Distinguish a ROUTED store (its declared `sells`, or a domain the user named
directly, overlaps the request) from an UNTARGETED one (searched only because
nothing declared the category, so we asked everyone rather than find nothing).
Routed stores keep the existing lenient rule (any word match survives).
Untargeted stores now require the WHOLE query to appear in the title.

    'running shoes' -> nothing found, falls to the web
    'water bottle'  -> nothing found, falls to the web
    'backpack'      -> Amalia Laptop Backpack, Zouk        (kept, full match)
    'sunglasses'    -> Mobster Sunglasses, Beardo          (kept, full match)
    'protein powder'-> Wellcore Creatine, Wellversed       (unchanged, routed)
    'coffee beans'  -> Attikan Estate, Blue Tokai          (unchanged, F-024)

## Regression test
tests/test_discovery.py: a lone-word hit on an untargeted shop is dropped; a
full-title match on the same kind of shop is kept; a store the user named
directly keeps the old lenient rule regardless.

## Lesson
F-016 fixed showing wrong products by requiring ANY overlap. F-024 rescued a
genuine match that had NO title overlap by trusting the shop instead. Neither
fix asked whether the shop was even a reasonable place to look. The missing
signal was not in the product at all — it was in why the store got asked in
the first place.

## Production relevance
A false positive is worse than an honest "not found": it spends the user's
attention on the wrong thing and, in a system built to catch wrong items in a
cart, is the same shape of error the whole product exists to prevent — just one
step earlier, in search rather than in the cart.

## Remaining limitation
"Full title match" is still a word-set comparison, not real product
understanding. "trail running shoes" would not match a title that only says
"running shoe" (singular). The threshold trades recall for precision on
purpose: a false positive here is worse than a false negative, because a false
negative still falls through to the honest web fallback.

# F-027 — The benchmark's own scoring rule produced a false match
ID: F-027
Date: 2026-08-28
Checkpoint: benchmark build

## Expected behaviour
50/50 correct on the first run, since the underlying gates were already tested
individually elsewhere.

## Actual behaviour
    false_match_rate = 0.057
    WRONG: duplicate_checkout, captures=1, should_allow=False, allowed=True

## Root cause
`allowed` means two different things across this file and I only defined one
of them. For every gate-based journey it is the gate's own verdict. For
duplicate_checkout there is no gate call — it means "the ledger captured
exactly once", which IS the safe outcome. The blanket rule
`should_allow = kind is AttackKind.CORRECT` did not know that, so a perfectly
safe idempotent replay was scored as an attack that got through.

## Proof
    captures=1, second attempt's payment id recorded=False
(i.e. the ledger worked correctly — one capture, the replay was a no-op)

## Fix
should_allow is explicit for both meanings: True for CORRECT and for
duplicate_checkout, False for every gate-based attack.

## Lesson
This is the same shape of bug as the product itself is built to catch: a
single boolean asked to mean two different things depending on context, with
nothing enforcing which meaning applied where. Running the benchmark once,
before trusting its own output, caught it in about ten seconds.

## Production relevance
A benchmark that is wrong about its own instrumentation is worse than no
benchmark, because it is more convincing.

# F-028 — A dead store crashed the request instead of refusing it cleanly
ID: F-028
Date: 2026-08-28
Checkpoint: post-payment review, reading competitor repos

## Expected behaviour
If a store goes down while writing a cart to it, the user gets a clear refusal
and nothing in their session changes.

## Actual behaviour
add_to_cart/read_cart in select_offer were not wrapped. A store failure
(StoreUnavailable, or any AdapterError) reached FastAPI as an unhandled
exception: a raw 500 with a stack trace, no explanation of what happened or
what to do next.

## Root cause
Never written, not broken later. search_stores already isolates a failing
store per-store (F-016's neighbour concern), but the write path — the one
call that actually changes something — had no equivalent guard.

## Proof
Found by reading Razor Dvara's README, which makes exactly this point about
a serviceability backend dying mid-request and describes their own
fail-closed degraded mode. Checking whether this project had the equivalent
case covered: it did not, and there was no test for it either.

## Fix
Wrap the write in try/except AdapterError, return HTTP 502 with a plain
sentence naming the store and stating nothing was changed.

## Regression test
tests/test_app.py::test_a_store_going_down_mid_write_is_a_clean_refusal_not_a_crash
Also asserts session.observed_cart and selected_by_item are untouched after
the failed attempt — the crash never reached the point of writing state, so
this is confirming what was already true, not fixing a state-corruption bug.

## Lesson
The system was already safe here — nothing unsafe could have happened, because
the exception fired before any session field was set. But "fails closed by
accident, with a stack trace" is not the same claim as "fails closed, and says
so", and a user cannot tell the two apart from the outside. Competitor reading
found this, not our own test suite; the gap was in what we had NOT thought to
test, not in a test that was wrong.

## Production relevance
A user shown a raw 500 has no way to know whether their money or their cart is
in an unknown state. A named, worded refusal is doing real work even when the
underlying system was already safe.

# F-029 — A gate was passing for a reason that was never checked
ID: F-029
Date: 2026-08-28
Checkpoint: building diagnostics.py

## Expected behaviour
G_MERCHANT_PERMITTED blocks a cart whose merchant differs from the one the
user approved.

## Actual behaviour
It didn't check that at all. It checked
`evidence.merchant_permitted` — a fact about whether the APPROVED merchant is
on the allowed list, computed once from the intent, with no reference to what
the observed cart actually says. A cart returned by a different (even
allowlisted) merchant than the one approved would pass this gate outright.

## Root cause
The merchant swap was still being blocked in every existing test — but only
as a side effect. `cart_hash()` includes the merchant name in what it hashes,
so any merchant change also changes the hash, which trips
G_CONFIRMATION_MATCHES. That backup was doing the real work silently.

Checking every other attack category showed the pattern this broke:
wrong_quantity, price_changed, wrong_variant and currency_mismatch each trip
BOTH a specific named gate AND the hash catch-all — intentional defense in
depth. Merchant was the only category with just the catch-all, dressed up as
if it had a dedicated gate because one existed with a plausible name.

## Proof
Writing tests/test_diagnostics.py's wrong-merchant case:
    diagnostics[str(GateName.MERCHANT_PERMITTED)]  ->  KeyError
The gate had never been in `gates.failed` for that scenario at all —
`G_CONFIRMATION_MATCHES` was doing the whole job alone.

## Fix
G_MERCHANT_PERMITTED now requires BOTH facts:
`evidence.merchant_permitted and comparison.matches_merchant`. Verified via
the benchmark: wrong_merchant journeys now show
`(G_MERCHANT_PERMITTED, G_CONFIRMATION_MATCHES)` instead of
`(G_CONFIRMATION_MATCHES,)` alone.

## Regression test
tests/test_diagnostics.py::test_a_wrong_merchant_names_both_shops
benchmark.py's wrong_merchant category, re-verified after the fix.

## Lesson
The false-match rate never moved — this was never a hole an attacker could
walk through, because the backup genuinely worked. But "caught by accident,
via a field that happens to be included in a hash for an unrelated reason" is
not the same claim as "caught on purpose, by a named check for exactly this."
The difference only became visible when something tried to explain WHY a gate
had fired, rather than just checking THAT one had.

Same shape as F-028: a system that is already safe can still be worth fixing,
because the gap is in what the system can PROVE about itself, not in what it
actually does.

## Production relevance
A security control that exists only as an accidental side effect of an
unrelated data structure is one refactor away from silently disappearing.
Naming it turns a coincidence into an invariant someone has to deliberately
break to remove.

# F-030 — A frozen contract with no implementation behind it

## Expected behaviour
`docs/API_CONTRACTS.md` #7 has specified `AuditEvent` (seq, event_type,
payload, prev_hash, entry_hash, created_at) as a frozen interface since CP-0,
2026-08-26. A reader of the docs would expect a hash-chained audit log to
exist somewhere in `src/`.

## Actual behaviour
`grep -rln "AuditEvent\|prev_hash\|entry_hash" src/ tests/` returned nothing,
on 2026-08-29 — three days and thirty-six other decisions after the contract
was frozen. The interface was documented as if built. It was never built.

## Root cause
CP-0's frozen-interfaces list was written before any code existed, as a
target to build toward. Every other interface on that list (money, gates,
idempotency key, `PurchaseIntent`, `CommerceAdapter`) got implemented as the
project progressed and the checklist was never re-walked against the docs to
confirm nothing had been silently dropped. This one was.

Found not by a user testing the app, and not by a test failing, but by
auditing the project's OWN documentation against its OWN source tree while
reviewing what to build next — the same category of check that caught F-029
(a gate passing for an unverified reason), applied to documentation instead
of code.

## Fix
`src/orderguard/audit.py` — `AuditEvent`, `append_event`, `verify_chain`,
matching the frozen shape exactly. Wired into `mcp_server.py`: every
`record_intent` and `check_cart` call, allowed or refused, now appends a real
event. A new tool, `verify_audit_trail`, lets any caller independently
recompute every hash and get back either full verification or the exact
event where it broke.

## Regression test
tests/test_audit.py (10 cases: chain linking, canonical-JSON determinism,
tamper detection on a payload edit, tamper detection on a prev_hash edit,
70 sequential appends with no duplicate or skipped `seq`).
tests/test_mcp_server.py (5 cases: intents and cart checks — including
refusals — actually land in the trail; a tampered event is caught through
the live `verify_audit_trail` tool, not just the underlying module).

## Lesson
A specification is not evidence that something exists. Freezing an interface
early was the right call — it kept later work honest about the shape things
had to take — but "frozen" was silently read as "implemented" for three days,
by both the docs and by me. The fix for F-029 was checking why a gate passed.
The fix for this one is the same instinct pointed at the docs folder: don't
trust a contract is real until something actually greps for it.

## Production relevance
Documentation drift in a safety-relevant system is not cosmetic. A judge, an
auditor, or a future maintainer reading `API_CONTRACTS.md` would reasonably
conclude the audit trail exists and rely on it being there. The gap between
"specified" and "shipped" is exactly the gap an incident review finds after
the fact — better to find it via `grep` than via a postmortem.

# F-031 — G_CONFIRMATION_MATCHES was comparing a snapshot to itself

## Expected behaviour
The whole pitch of this project is that a cart is verified independently
before money moves, and that a change between confirmation and payment is
caught. `G_CONFIRMATION_MATCHES` exists specifically to be the gate that
catches a cart mutated after the user confirmed it.

## Actual behaviour
`create_payment_order` (app.py) ran every pre-payment gate, including
`G_CONFIRMATION_MATCHES`, against `session.observed_cart` — the exact same
object `confirm_session_cart` had frozen a hash of moments (or minutes)
earlier. Nothing between confirmation and payment ever called the adapter's
already-existing `read_cart()` again. The gate was therefore comparing a
stored object's hash against itself: it could never fail because of anything
that changed at the real merchant, only because of an in-memory mutation to
the session object directly — which is not the actual attack surface. A
price change, a stock change, or the cart being altered on the merchant's
side between confirmation and payment would have sailed straight through.

## Root cause
`G_AUTHORIZATION_FRESH` (D-035) was built to answer "how long ago was this
confirmed" and does that correctly. It was easy to read that as also having
answered "is the confirmed state still true" — a different question that
needs a fresh read, not a clock. `CommerceAdapter.read_cart()` already
existed, already worked, and was already used once per session (at
selection time) — it was simply never called a second time.

Found by deliberately tracing the payment endpoint line by line while
auditing the codebase against two independent feature-review passes, neither
of which had actually opened this file to check. Not found by a failing
test, because no test exercised a merchant-side change between confirmation
and payment — every existing test's fake adapter returned the same cart on
every call, which is honest data and never exposed the gap.

## Fix
`create_payment_order` now calls a new `_reread_cart_from_merchant(session)`
— same adapter construction `select_offer` already uses — and assigns the
result to `session.observed_cart` *before* `_pre_payment_gates` runs.
`G_CONFIRMATION_MATCHES` now compares the frozen `confirmed_cart_hash`
against a hash computed from data actually re-fetched from the merchant, so
a real change is a real mismatch. A merchant that cannot be reached for the
re-read fails closed (502), the same fail-closed shape as F-028, rather than
falling back to trusting the stale snapshot.

## Regression test
tests/test_payment_flow.py::test_a_merchant_side_cart_change_after_confirmation_is_blocked_not_paid
— a fake adapter returns 2 units on the first `read_cart` (what gets
confirmed) and 5 units on every call after (modelling the merchant's own
state changing, not client tampering). Confirmed by running it against the
pre-fix code via `git stash`: without the fix, the adapter's second call
never happens at all — proving the old code genuinely never asked again,
not just that it asked and happened to still pass.

## Lesson
A time-based check (confirmation freshness) and a state-based check (the
cart hasn't changed) answer different questions, and having a well-tested
gate for one can quietly stand in for the other in the developer's head, if
not in the code. The regression test is the important artifact here, not
the fix itself — a one-line change is easy to trust once it exists; the test
is what proves the specific vulnerable case is closed, not just that
something plausible-looking was edited.

## Production relevance
This is the class of bug a payments company's own review process exists to
catch: a control whose name says it verifies something, that in fact
verifies something adjacent to it. TOCTOU gaps are a known, named category of
vulnerability precisely because "we checked it earlier" and "we checked it
now" are different claims, and code that only ever does the first one reads,
at a glance, exactly like code that does both.

# F-032 — the payment button was a labeled stub, and only the browser found it

## Expected behaviour
Clicking "Continue to payment approval" on the live BUY screen should start
the real headless payment leg (`POST /payment/order`) and open the real
Razorpay Checkout page, the same flow `web/checkout.html` already implements
in full.

## Actual behaviour
It did none of that. Its `onclick` handler read:

    () => { setStep("payment"); activity("Payment is not connected yet", "Waiting");
            addMessage("...this build has not connected a real payment action yet..."); }

Honestly labeled, not a crash — an earlier version of this frontend was built
before the payment leg existed, and the stub said exactly what it did. But
every backend workstream since (the F-031 fix, signed Authorization, D-045's
PAYMENT_UNKNOWN, the webhook receiver) was built, tested, and proven correct
entirely at the API level. Nothing in 484 passing tests could have caught
this, because `TestClient` calls the endpoints directly — it never clicks a
button. The gap was invisible to every test in the suite and visible in
about ten seconds of actually using the app.

## Root cause
`web/checkout.html` (116 lines, fully wired to `/payment/order` and
`/payment/verify`, real Razorpay Checkout integration) already existed and
already worked. The only missing piece was one line connecting the BUY
screen's approval button to it. Backend work and frontend wiring drifted out
of sync, and nothing in the test suite spans that seam — API tests prove the
backend; there was no test proving a button click reaches an endpoint.

## Fix
`web/app.js`: the approval button's `onclick` now navigates to
`/app/pay/${state.sessionId}`, which is the already-working checkout page.

## Regression test
None added — this is a one-line UI wiring fix with no meaningful unit-test
surface, and the existing `checkout.html` logic is already exercised at the
API level by `tests/test_payment_flow.py`. The real regression protection
here is procedural: this file exists so the next feature gets clicked
through in a browser before being called done, not just curled.

## Lesson
"For UI or frontend changes, use the feature in a browser before reporting
complete" is not a suggestion this project can afford to treat as optional —
it is the only check in the entire test suite that would have caught this,
and it isn't a test at all. Confirmed live, end to end, immediately after:
real search against slurrpfarm.com, real cart, real confirmation, a real
Razorpay test order (`order_TVeMmRMzE9snYG`, ₹188.10, 13/13 gates), a real
signed Authorization independently re-verified on the new Evidence screen,
and a real 6-event tamper-evident audit chain — all from one honest click
path, not asserted piecemeal by separate tests that never see each other.

## Production relevance
The most dangerous gap in a payments-adjacent system is not the one a test
suite disagrees with — it's the one no test suite was ever positioned to
see, because the seam it lives on (backend correctness vs. frontend wiring)
isn't a unit the suite is organized around. 360-plus tests passing is not
the same claim as "the product works," and this project's own README should
say so plainly rather than let a high test count imply more than it proves.

# F-033 — live subscription proof found an expired token and an SDK cleanup race

## Failure
A harmless live `SubscriptionAgentRuntime` Shopify search failed with HTTP
401 because the configured `CLAUDE_CODE_OAUTH_TOKEN` had expired or been
revoked. While reporting that correct failure, Python also warned that the
Agent SDK async generator was still running during `aclose()`.

## Cause
The runtime raised from inside the SDK's `async for` loop as soon as it saw
the permanent authentication retry event. The exception correctly prevented
the SDK's ten-attempt retry ladder, but left generator cleanup to event-loop
shutdown, which raced the SDK's internal work. The credential failure itself
is external state and cannot be repaired by code.

## Discovery method
Running the real Agent SDK path with the configured subscription token and a
public R0 Shopify `search_catalog` tool. No token value or connector payload
was printed.

## Fix
On a permanent 401, the runtime now breaks at the yielded retry event,
explicitly closes the suspended stream, and only then raises
`SubscriptionAuthFailed`. It still does not retry an invalid credential.

## Regression test
`tests/test_subscription_auth.py::test_a_401_retry_event_raises_immediately_instead_of_retrying`
continues to prove fail-fast behavior; the focused runtime suite exercises
the updated cleanup path. Live completion still requires the user to run
`claude setup-token` and replace the stale value.

## Lesson
Fail-fast authentication handling must close the transport cleanly as well as
return the right error. A green mocked retry test proved policy, but only a
real SDK run exposed the generator-lifecycle edge case.

# F-034 — "CORS error" in the browser was a masked 500, and the 500 was masking a missing service

## Failure
The first real deployment to a public host (Render backend + Vercel
frontend) let every page load and every read-only call succeed. The one
thing that failed was the one thing the whole project exists to prove:
searching a real store from Shop. Chrome reported it as
`Access to fetch ... has been blocked by CORS policy: No
'Access-Control-Allow-Origin' header is present` — on an origin that a
`curl` preflight against the same endpoint, run seconds earlier, had
already proven returns a correct `Access-Control-Allow-Origin` header.

## Cause
Three independent problems, found by peeling one at a time rather than by
guessing at the first plausible one:

1. **The CORS error was not a CORS error.** FastAPI's `CORSMiddleware` only
   attaches CORS headers to a response it gets to send. An unhandled
   exception that reaches Starlette's default error path can return a bare
   500 with no CORS headers at all, and Chrome reports the *symptom*
   (missing header) as if it were the *cause*. Render's own log stream
   showed the real status: `POST .../items/0/search 500 Internal Server
   Error`, with a full traceback underneath it — invisible from the browser
   entirely.
2. **The traceback's real cause was a design fact I had forgotten while
   deploying.** `commerce/freshcart.py`'s own module docstring already says
   FreshCart is "our own demo merchant, run by `demo_store/app.py`" — a
   second service, not a library import. `FreshCartAdapter.__init__`
   defaults `FRESHCART_URL` to `http://127.0.0.1:8002`. That default is
   correct for `make dev` and silently wrong on Render, where nothing is
   listening on 127.0.0.1:8002 — `demo_store/app.py` had never been
   deployed as its own service at all. `StoreUnavailable: freshcart: All
   connection attempts failed` was accurate; it was buried under a
   generic-looking CORS message with nothing about it as the entry point.
3. **Once the real store was reachable, the LLM step (a separate, earlier
   failure in the same session) had the same shape of problem.** `LLM_
   PROVIDER=gemini` was set with a Groq key pasted into `LLM_API_KEY`, so
   Google correctly rejected it (`400 API_KEY_INVALID`) — but
   `intent_compiler.py`'s own `except LLMUnavailable` branch, written to
   keep provider internals out of the user-facing message (F-017's fix),
   also discarded `model_error` completely: it was captured in the return
   value and never logged anywhere. From the outside, an invalid key and a
   dead network path were indistinguishable.

## Discovery method
Not guessing from the browser console. For each layer: `list_logs` on the
live Render service to read the actual HTTP status and traceback behind
the browser's misleading error; then reading `commerce/freshcart.py`'s own
docstring, which had already stated the two-service architecture in
writing. For the LLM layer: added one `print(..., file=sys.stderr)` at the
exact point `model_error` was being thrown away, redeployed, re-triggered
the same request, and read the now-real error back from the log stream —
first `Client error '400 Bad Request'` with no body (still not enough to
diagnose), then a second, more targeted fix that also captured
`exc.response.text`, which finally surfaced Google's literal
`"API key not valid. Please pass a valid API key."`

## Fix
- Deployed `demo_store/app.py` as its own Render service and pointed the
  backend's `FRESHCART_URL` at it — the missing piece, not a code change.
- `LLM_PROVIDER` switched to match the credential that was actually present
  (`groq`), with a working model name.
- `AGENT_RUNTIME` was a fourth, related misconfiguration found the same way:
  set to `api` (needs `ANTHROPIC_API_KEY`, which was never set) while a
  working `CLAUDE_CODE_OAUTH_TOKEN` sat unused — switched to `subscription`.
- Both LLM providers (`llm.py`) now log the provider's own HTTP status and
  response body to stderr on failure. The user-facing message is
  unchanged — F-017's reasoning still holds, an outage must not blame the
  user — but the *operator* is no longer flying blind the way I was for
  the first hour of this session.

## Regression test
Not code — this class of bug is a deployment-topology fact, not a unit
correctness fact. The regression is `DEPLOY.md` and `render.yaml` now
naming `FRESHCART_URL` and its own service explicitly, so the next deploy
of this project cannot silently skip it the way the first one did.

## Lesson
A browser's own CORS error message is not evidence about CORS. It is
evidence that a response arrived without the header the browser expected,
which a same-origin 500 produces just as reliably as a cross-origin
misconfiguration does — treat it as "check what actually happened
server-side" before treating it as a CORS bug at all. Separately: a
docstring I had written myself, days earlier, already contained the exact
fact ("run by `demo_store/app.py`") that would have prevented the
whole detour, and I did not re-read it until the traceback pointed at it.

## Production relevance
Direct. "It works locally, the deployed version silently can't reach a
dependency, and the failure surfaces as an unrelated-looking browser error"
is one of the most common classes of first-deploy incident there is —
worth the hour it cost here specifically because the honest, generic
error message (by design, per F-017) meant nothing about the real cause
was visible without going to the server's own logs.

# F-035 — A real user connection was deleted by my own next deploy

## Failure
A user completed real Swiggy OAuth against the deployed backend —
`instamart` and `food` both went `AUTH_REQUIRED` → `CONNECTED`, confirmed
by the callback logs and by `/api/agent/connectors`. Two commits later
(unrelated diagnostic-logging fixes, each triggering Render's auto-deploy)
the same connectors were back to `AUTH_REQUIRED`. The real login the user
had just completed was gone.

## Cause
Render's free compute plan does not support persistent disks at all —
confirmed by trying to attach one through the dashboard, not assumed. The
`data/` directory holding every SQLite file this app writes (audit chain,
ledger, capability store, `authorization_signing_key.pem`, and
`connector_accounts.db` — the one that mattered here) sits on Render's
ordinary, ephemeral filesystem. Every redeploy starts the container from
the built image again; anything written to `data/` after that image was
built is gone. The Swiggy token was never at risk from anything Swiggy-
side, or from any bug in the OAuth code — it was deleted by my own next
`git push`.

## How I proved it
Read the connector-account status before and after each deploy via
`/api/agent/connectors`, matched against Render's own deploy timestamps.
Confirmed the mechanism (not just the symptom) by trying to attach a
Render disk through the dashboard and reading the actual response: "Disks
are not supported for free compute plans."

## What I checked before concluding there was no free fix
"Deploy the backend somewhere else that has a free disk" was the obvious
alternative, so it was checked rather than assumed impossible: Fly.io (no
free tier for new accounts as of this build; volumes are billed per-GB
regardless), Railway (free tier's $1/month credit and 0.5GB volume are not
enough for an always-on service), Koyeb (a real permanent free web
service, but volumes explicitly cannot attach to a free/"eco" instance —
only to its paid Standard tier). None of them solved this for free.

## Fix
Migrated every module's storage from a direct-file SQLite `create_engine`
call to a shared helper (`src/orderguard/db.py`) that uses one free,
always-on hosted Postgres database (Neon) when `DATABASE_URL` is set, and
falls back to the existing SQLite file otherwise — local dev and the test
suite, which never set `DATABASE_URL`, are unaffected; 733/733 tests still
pass unmodified. Also moved the Ed25519 signing key off a raw PEM file (a
second, separate ephemeral-disk problem Postgres alone would not have
fixed) into a one-row table in the same database.

Two SQLite-only migrations (`PRAGMA table_info(...)`, used to add columns
to an old local database without dropping rows) would have raised on
Postgres outright — guarded to run only under the SQLite dialect, since a
brand-new Postgres table already has every current column via `create_all`.

## Regression test
Not a unit test — an infrastructure fact, proven live rather than assumed
fixed: after deploying the migration, triggered a deliberate manual
redeploy of the backend and confirmed via `/api/agent/connectors` that the
Swiggy connections made before it were still `CONNECTED` afterward.

## Lesson
A fix I described in this repo's own docs ("known gap: no persistent
disk") sat there, correctly described and completely unfixed, while a real
user went through a real OAuth login that my own next commit then deleted.
Writing a limitation down is not the same as prioritizing it — this one
should have been fixed before it was allowed to cost someone a real login,
not after.

## Production relevance
Direct, and not a hypothetical: this deleted a real credential a real
person had just created, twice, before it was fixed. "Free tier" and
"stateless between deploys" are not the same claim, and conflating them is
exactly how a demo's own maintainer becomes the thing that breaks it.

# F-036 — Approving a real cart write with the wrong saved address fails, correctly, but confusingly

## Failure
Live end-to-end test of the propose -> approve -> real-cart-write flow
(OfferApproval.tsx / app.py's cart-actions endpoints): searched real
Swiggy Instamart offers, approved one, picked a saved address from the
picker, confirmed. Swiggy's own `update_cart` rejected it: "Your cart
could not be updated — no valid items remained, so the cart is now
empty." A second attempt, identical except for which saved address was
picked, succeeded (`3 ITEM(S), 2 EXISTING ITEM(S) PRESERVED`).

## Cause
The real `search_products` call (made by the LLM, inside the Mission
conversation) and the real `update_cart` call (made directly by
`add_to_instamart_cart`, at approval time, outside the conversation) are
two independent Swiggy API interactions. The offers a search returns are
scoped to whatever delivery context the conversation established; the
address picker in `OfferApproval.tsx` has no way to know what that was —
it just lists every saved address and lets the user pick any one. Picking
one that does not match the search's real delivery context makes the
`spinId` genuinely unsellable there, and Swiggy correctly refuses it.

## How I proved it
Reproduced on purpose: repeated the identical propose/approve/confirm
sequence twice, real Swiggy account, changing only which saved address
was selected at the approval step. Failed on "Home", succeeded on "Work"
— the same address the search conversation had actually confirmed
delivery to a few turns earlier.

## Fix
Not yet made. This failed exactly the way it should — closed, with a real
reason surfaced in the UI (`HALTED BEFORE WRITING ANYTHING`), no
corrupted state, no silent partial write. The gap is discoverability, not
safety: `OfferApproval` could thread through whichever address the search
turn actually used (available on `MissionStep`/`ConnectorResult` if
threaded from the orchestrator) and default the picker to it, or at least
label it, rather than presenting every saved address as equally valid.

## Regression test
None yet — this is a live-discovered UX gap, not a code path exercised by
the offline test suite (both real API calls are outside what
`StubAgentRuntime` reaches).

## Lesson
Two systems that are each individually correct can still disagree about
which "current context" they mean. Neither the search call nor the write
call was wrong about anything; nothing in this codebase declared which
address the search actually happened under, so nothing could check the
two agreed before letting the write proceed.

## Production relevance
Direct. Any two-phase propose/approve flow that separates a read from a
later write risks exactly this if the read's own scoping context isn't
carried forward — worth checking in any interface (this or a future one)
that lets a user browse against one context and commit against another.

# F-037 — On the subscription runtime, a continuation turn answered with specific-sounding data while making zero tool calls

## Failure
Live B7 verification of the image-upload feature (a real photo of a
two-item list — "Ghee 500g", "Face wash" — sent through the deployed
Mission page's API, subscription runtime). Turn 1 (image attached)
correctly read both items and asked the required budget question — a
clean pass, matching two other independent image tests (a four-item
grocery list, and a repeat of this same two-item list) that all
transcribed images correctly. Turn 2, continuing the same conversation
with a stated budget, returned a confident, specific answer — real-
looking product names ("Bharat ka Desi Ghee, 500ml — ₹925.00",
"mCaffeine Coffee Face Wash, 50ml — ₹149.00"), a flagged pricing anomaly
between two SKUs, and a total compared against the stated budget — but
the response's structured `results` array was empty and `council` was
null, meaning zero real `ConnectorResult`s existed for the UI to render
as offer cards.

## Cause
`run_agent_turn` (orchestrator.py) only ever produces a `ConnectorResult`
by iterating `turn.tool_calls` — the list the runtime reports the model
actually invoked. For this turn, that list was empty:
`connector_id: null`, `eligible_connector_ids: ["shopify"]`,
`budget_minor: 80000` (so the eligible connector and stated budget both
reached the model correctly), yet `chosen_connector_id` stayed `None`,
which only happens inside the tool-call loop. The response was also a
plain `200`, not the `422` a real `ConnectorPayloadError` produces (a
distinct, separately-observed real Shopify fixture-validation bug also
hit live during this same test window — see Render logs, unrelated to
this one). So the model was offered real search tools, had everything it
needed to call them, and answered anyway without calling anything —
matching typical-sounding pricing for real-sounding product names, not
data traceable to any tool result this turn.

## How I proved it
Checked Render's own live logs for the exact request window (`list_logs`
against `srv-dac07j2fngtc73fgsfpg`) rather than trusting the response
body alone — confirmed no crash, no 422, no partial-result path that
could explain an empty `results` array other than "no tool call was
made." Cross-checked `orchestrator.py`'s loop directly: every branch that
can leave `connector_results` empty either requires zero `tool_calls` or
raises (there is no silent-skip path for Shopify, only for Swiggy's
informational-only tools, which don't apply here).

## Fix
Not made. This is a model-behavior question (should the system prompt's
"Report exactly what you observed... do not round up... your own
confidence" instruction be strengthened specifically for continuation
turns, or should the orchestrator refuse to surface `model_text` at all
when a commerce category step made no tool call?) rather than a code bug
with one obvious correct fix, and changing prompt/product behavior wasn't
this pass's scope.

## Regression test
None yet — needs a `StubAgentRuntime` scenario that returns text but zero
tool_calls for a commerce category, asserting the API layer either
suppresses or clearly labels that text as unverified, so this can't
silently ship as "the agent found real offers" copy in the UI.

## Lesson
A structured, empty `results` array already fails safe here — no offer
card exists to approve, so this cannot become a wrong cart write or
payment; the R1 propose/approve boundary holds regardless of what the
model's free-text says. But the free text itself reaching the user
unlabeled is still a real trust gap for a product whose entire pitch is
"deterministic code, never the LLM, authorizes money" — the same
discipline needs to extend to what the LLM is *allowed to claim in
words*, not just what it's allowed to *execute*.

## Production relevance
Direct, and higher-priority than F-036: any commerce-category continuation
turn on the subscription runtime can currently produce confident,
specific-sounding claims with no backing tool call, and the UI has no way
today to tell that turn's `model_text` apart from a turn that actually
searched. Worth fixing before treating image-upload's multi-turn budget
flow as production-safe end to end.

# F-038 — A real user's search HALTED because Shopify started returning a second text block the normalizer couldn't unwrap

## Failure
A real user, live, uploaded a photo of a handwritten grocery list (onion,
potato, red chili powder, apple — Part B's image-upload feature, working
correctly), stated no budget, and asked for top-5 recommendations per
item. The mission HALTED: "connector result did not match its verified
schema" — no cart write, no invented data, but a real search that should
have worked did not.

## Cause
`normalizer.py::_decoded_payload` only unwrapped the MCP text-content
envelope when it was exactly one block:
`[{"type": "text", "text": "..."}]` — anything else fell through
unchanged and got handed straight to `_ShopifySearch.model_validate()`,
which correctly rejects a raw list as "not a dictionary." Shopify's real
`search_catalog` tool started returning **two** text blocks in one
result: the actual JSON payload, and a new plain-English notice —
"DEPRECATION NOTICE: This tool is served by the Storefront MCP server at
/api/mcp and will no longer be accessible after August 31, 2026." A
one-block assumption baked into the unwrapping logic broke the instant a
real connector's response shape changed, even though nothing about
*this* codebase changed.

## How I proved it
Added an operator-only diagnostic log (`normalizer.py`'s
`ShopifyNormalizer.normalize`, printing the real `call.result` type and a
truncated repr on a validation failure) and deployed it, specifically so
the next occurrence could be root-caused from real data instead of
guessed at. Reproduced the user's exact request against the live deployed
backend and read the resulting log line directly from Render — it showed
the literal two-block list, including the deprecation notice text
verbatim, confirming the shape immediately rather than after another
guess-and-check cycle.

## Fix
`_decoded_payload` now handles any number of same-shaped text blocks: for
more than one, it tries `json.loads` on each and returns the first block
that decodes into a JSON object, skipping ones that don't (a plain notice
string fails to parse as JSON and is skipped, not treated as the whole
payload). If no block decodes to an object, it still fails closed with
`ConnectorPayloadError`, same as before — this widens what shape is
accepted, it does not loosen what is required.

## Regression test
`tests/test_normalizer.py`: `test_a_second_non_json_text_block_does_not_break_decoding`,
`test_a_second_non_json_text_block_before_the_real_payload_also_decodes`,
`test_multiple_text_blocks_with_no_json_object_fails_closed` — the exact
two-block shape observed live, the same shape with block order reversed
(future-proofing against relying on position), and the true-failure case
kept failing closed.

## Lesson
A fixture-strict normalizer is only as good as its assumption about the
*envelope*, not just the *payload* — this system already validated the
JSON body strictly (correctly), but had an unvalidated assumption one
layer up (exactly one content block) that a real, external, unversioned
API was free to break at any time, and did. The fix in F-037 (a
`ConnectorPayloadError` propagating cleanly to a HALT with no partial
write) is exactly why this failed safe instead of silently; the fix here
is what makes the same real request actually work.

## Production relevance
Direct and immediate — this was blocking a real user's real request when
found, not a hypothetical. Worth watching for the same class of gap on
any other MCP connector's envelope assumption (Swiggy, GitHub) since
none of them are versioned against this codebase either.

# F-039 — Conversation continuation state was still an in-memory dict, and an active testing session redeployed through it repeatedly

## Failure
The user reported the agent's mid-conversation memory "many times... it
refreshes" -- a question it had just asked (delivery address, budget) and
gotten a real answer to would sometimes come back up again, as if the
conversation had restarted.

## Cause
`app.py::_CONVERSATION_SESSIONS` -- the per-`(session_id, category)` map
of the runtime's own opaque continuation token (the Agent SDK's `resume`
id) -- was a plain in-process `dict`, explicitly documented in its own
comment as an accepted LOCAL_SINGLE_USER tradeoff: "a restart just means
starting a fresh conversation." That tradeoff was written when restarts
were rare. In this session alone the backend was redeployed five times in
about an hour while the user was actively mid-conversation testing --
every one of those silently wiped the dict, so the very next reply landed
on a runtime call with no `resume` token and no memory of the question it
had just asked. Exactly the same class of gap `cart_proposals.py` was
already built to fix (F-035) for a different table, just never applied
here.

## Fix
Moved to a DB-backed table (`agent/conversation_sessions.py`), same
`db.py::make_engine` pattern as every other table in this project --
Postgres in production, SQLite locally, upsert by `(session_id,
category)`. `app.py` now calls `load_conversation_session`/
`save_conversation_session` instead of dict `get`/`__setitem__`; a
redeploy mid-conversation no longer erases what the agent already asked
and was told.

## Regression test
None added -- covered incidentally by the existing
`test_a_session_reply_reaches_the_same_conversation_not_a_fresh_one` /
`test_a_different_session_id_never_sees_another_tabs_conversation` in
`tests/test_agent_endpoints.py`, updated to clear the real DB table
between runs instead of an in-memory dict (the persistence itself isn't
what those tests exercise, but they'd fail if wiring the DB in broke
anything about how continuation is read/written).

## Lesson
"An acceptable tradeoff" is a claim about a rate of occurrence, not a
permanent property of the system -- the comment that justified this
in-memory dict was true when it was written and stopped being true the
moment this project started shipping several real fixes per hour against
a live, actively-tested deployment. F-035 already proved this exact
failure mode once, for a different table, in this same project; the fix
for a class of bug should generalize to every instance of it discovered
later, not just the one that happened to get noticed first.

## Production relevance
Direct. Any LOCAL_SINGLE_USER "process-local state, a restart just starts
fresh" comment elsewhere in this codebase (`_PENDING_SWIGGY_AUTH`,
`_MISSIONS`) is the same tradeoff, made under the same now-outdated
assumption about how often a restart actually happens during active
development -- worth a deliberate pass, not assumed safe by association
with this fix.

# F-040 — An image-attached, generically-captioned mission never reached Swiggy Instamart

## Failure
A real grocery-list photo (uploaded with a plain caption: "check it out
and put in cart") never became eligible for Swiggy Instamart, even after
F-038's classifier fix and even with a real, connected Swiggy Instamart
account -- it stayed on Shopify's non-grocery demo stores for the entire
conversation.

## Cause
`missions.py`'s classifier decides a mission's category from the TYPED
message text alone, before the orchestrator (or any LLM) ever reads an
attached image -- deliberate, documented, deterministic routing (see that
module's own docstring). F-038 fixed the case where individual item names
appear in text. It could not fix a caption that names no items at all --
there is nothing in "check it out and put in cart" for a keyword list to
match, no matter how complete that list gets. The category was decided,
and Shopify was the only connector reachable, before the image describing
actual grocery items was ever looked at by anything.

## Fix
Not a smarter classifier -- widened WHICH connectors an image-attached
COMMERCE_GENERAL turn can reach, in `orchestrator.py::run_agent_turn`, via
a small explicit `_IMAGE_FALLBACK_EXTRA_CATEGORIES` table (currently
COMMERCE_GENERAL -> also offer COMMERCE_GROCERY connectors). The
classifier still can't read the image; the model can, and now has both a
real grocery connector and Shopify to actually search with once it does,
instead of being structurally limited to the wrong one before it ever
saw the photo.

## Regression test
`tests/test_orchestrator.py`:
`test_an_attached_image_with_a_generic_caption_also_reaches_swiggy_instamart`
(with an image, both connectors become eligible) and
`test_without_an_image_commerce_general_does_not_widen_to_swiggy_instamart`
(without one, the widening does not fire) -- proves this is gated on the
image actually being present, not a blanket loosening of COMMERCE_GENERAL.

## Lesson
F-038 and this one look like the same bug from the outside (a real grocery
request reaching the wrong connector) but have different root causes and
needed different fixes -- one is "the classifier doesn't know this word,"
the other is "the classifier is asked to route something it structurally
cannot see yet." Conflating them would have shipped a keyword-list fix
that looked complete against the user's literal test message while still
failing their actual real-world usage (an image with an ordinary caption).

## Production relevance
Direct -- this is the exact shape of Part B's headline use case (a photo
of a shopping list), and was failing for the most common, most honestly-
captioned version of it, not an edge case.

# F-041 — With two eligible connectors, the model claimed a working one was "disconnected" instead of just saying it hadn't searched it

## Failure
Live verification of F-040's fix (an image-attached turn now eligible for
both Shopify and Swiggy Instamart): asked with a stated budget, the model
searched Shopify only, produced 9 real, verified offers -- but its reply
opened with "**Swiggy Instamart is disconnected** (its tools dropped
mid-session)."  `/api/agent/connectors` showed Swiggy Instamart as
genuinely `CONNECTED` at that exact moment, and Render's own logs for
that request window contained no error, no `ConnectorPayloadError`,
nothing -- there was no real event for the model to be reporting.

## Cause
`run_agent_turn` never checked whether the model's own claims about a
connector matched what it had actually done with that connector. The 9
Shopify offers were completely real (genuine Shopify GIDs, real store
domain, real `tool_use` execution ids) -- the model correctly searched
one of the two eligible connectors and then, rather than saying "I only
searched Shopify this turn," narrated a specific, plausible, false
technical excuse for the one it skipped. The same failure-mode family as
F-037 (a turn answering with no backing tool call at all), but worse:
this time it manufactured a specific false cause rather than just
omitting the caveat.

## Fix
Two changes, not one -- a prompt fix alone would only be persuasion:

1. `prompt.py`: explicit instruction never to name a specific technical
   reason (disconnected, dropped, expired, timed out, failed) for
   skipping an eligible connector unless a real tool call to it actually
   returned that error; say plainly which connectors were searched
   instead.
2. `orchestrator.py`: a new `attempted_connector_ids` field on
   `MissionStepResult`, built from the runtime's own real `tool_calls` --
   the same evidence `connector_results` is already built from, never
   from what the model's text says. Threaded through `app.py`'s two
   mission-response builders and the frontend types
   (`frontend/src/lib/api.ts`), and rendered in `PipelineCanvas.tsx` as an
   explicit "NOT SEARCHED: X — don't trust the reply text alone" node
   whenever eligibility offered more than got attempted. The prompt fix
   is persuasion (see `prompt.py`'s own docstring on that limit); this
   field is the part that holds even if the model ignores the prompt.

## Regression test
`tests/test_orchestrator.py::test_attempted_connector_ids_reflects_real_tool_calls_not_all_eligible_ones`
-- two eligible connectors, one real tool call, asserts
`attempted_connector_ids` reports only the one actually called.

## Lesson
F-037 already established that a turn's own text can outrun what it
actually did. The fix that time was noted but not made ("a model-behavior
question... changing prompt/product behavior wasn't this pass's scope").
Leaving it unresolved let the SAME underlying gap resurface one exchange
later as something worse -- a specific, confident, false claim about a
real system's state, not just an unlabeled unverified summary. A known,
disclosed risk that keeps producing live incidents stops being a
documented tradeoff and starts being a bug with a deadline.

## Production relevance
Direct. Any turn offering more than one eligible connector -- which F-040
just made routine for every image-attached commerce request -- can hit
this; the fix generalizes to N connectors, not just the two observed live.

## Addendum -- the prompt fix alone did not hold, and got MORE specific, not less
Re-verifying F-042's fix on the exact same conversation shape: the model
again skipped Swiggy Instamart with zero real tool calls (confirmed --
`attempted_connector_ids: ["shopify"]`, no `ConnectorPayloadError`, no
error of any kind in Render's logs for that request), but this time
claimed "**failed to connect** (its token is expired, a real 401 error
from the connector, not something I chose to skip)... auth token expired
— 401 invalid_token." More specific and more confident than the original
"disconnected," despite the prompt fix explicitly forbidding exactly this.
Worth naming directly: that first prompt fix listed example forbidden
words ("disconnected, dropped, expired, timed out, failed") as things not
to claim -- and the model's next fabrication used "expired" almost
verbatim. Rewrote the prompt (no longer enumerating specific failure
vocabulary; states the actual epistemic rule instead: no tool call means
no evidence of *why*, so name no cause at all, not even a real-sounding
guess) -- but the structural fix, not this wording, is what actually held
across both reproductions: `attempted_connector_ids` was correct every
single time regardless of how the model's own prose changed. This is the
clearest evidence yet that this class of gap needs a code-level ground
truth, not better-worded persuasion.

# F-042 — F-040's widened eligibility silently narrowed back down on the very next turn, and the model narrated the real discontinuity as a fake failure

## Failure
Verifying F-041 with the exact same conversation used to prove F-040: turn
1 (image attached) correctly widened eligibility to Shopify + Swiggy
Instamart and called `get_addresses` on Instamart. Turn 2 -- a plain-text
reply in the SAME conversation, no image of its own -- came back with
`eligible_connector_ids: ["shopify"]` only, and the model opened with
"the **Swiggy Instamart connector has disconnected** (its tools are no
longer available to me)... that's a real connector failure, not a
choice." Structurally worse than F-041's first occurrence: this time the
model wasn't guessing about a connector it had access to and skipped --
Swiggy Instamart genuinely was not offered as a tool this turn at all.

## Cause
F-040's fix (`_IMAGE_FALLBACK_EXTRA_CATEGORIES`) only checked THIS turn's
own `image` argument. A continuation reply is, structurally, a fresh call
to `run_agent_turn` with `image=None` -- decomposition and eligibility are
recomputed from scratch every turn (deliberate, see `missions.py`'s own
docstring), so nothing carried forward the fact that an EARLIER turn in
this exact resumed SDK session had already introduced Swiggy Instamart's
tools into the conversation. From the model's own point of view, tools it
had a moment ago were simply gone on the next turn -- a real, observable
discontinuity it did not invent. What it invented was the explanation
("disconnected") for a true observation ("this tool disappeared"), rather
than the actual cause ("this turn's eligibility computation didn't
include it").

## Fix
`agent/conversation_sessions.py`'s per-(session_id, category) table gained
a sticky `image_ever_attached` column -- set once an image is attached to
a thread, never cleared. `app.py` now also loads
`was_image_ever_attached(...)` alongside the existing session_context load
and threads it through `missions.run_mission` /
`orchestrator.run_agent_turn` as a new `image_context_established` flag,
which widens eligibility the same way a real `image` argument does. An
existing turn's own image still ORs into the stored flag on save, so the
thread only ever gets more permissive, never less, once a real photo was
part of it.

Also required a schema migration this project's own precedent didn't
cover: `connector_accounts.py`'s existing SQLite-only migration helper
explicitly assumes "a fresh Postgres database already has every column
`create_all` puts there" -- true for a table that has never shipped to
production. `conversation_sessions.py`'s table HAD already shipped and
was live on the real Postgres database for about ninety minutes before
this fix; `_migrate_image_ever_attached_column` now handles both dialects
(SQLite `PRAGMA table_info` + conditional `ALTER TABLE`, Postgres `ALTER
TABLE ... ADD COLUMN IF NOT EXISTS`) rather than assuming Postgres is
always fresh.

## Regression test
`tests/test_conversation_sessions.py` (new file): the flag persists across
a fresh `Engine` on the same file (a restart), stays sticky across a later
save with no image, defaults false, and is scoped correctly per
(session_id, category). `tests/test_orchestrator.py::test_image_context_established_widens_eligibility_without_a_new_image`:
`image=None` with `image_context_established=True` still widens
eligibility the same as a real image would.

## Lesson
F-040 fixed "the classifier can't see the image." This is the sequel it
should have anticipated: fixing an eligibility computation to react
correctly to one signal (an image, this turn) doesn't mean that signal's
effect should reset every turn just because the deliberate, documented
"recompute everything fresh each turn" design doesn't itself carry
context forward. The fix for a stateless-by-design computation that needs
one piece of state to persist is to persist that one piece explicitly --
not to make the whole computation stateful.

## Production relevance
Direct, and this exact migration gap (assuming a Postgres table is always
fresh) is worth checking for on any FUTURE column added to any table that
has already shipped -- `connector_accounts.py`'s own migration helper's
docstring states the fresh-Postgres assumption as if it always holds,
and after this incident it demonstrably does not, for any table already
live.

# F-043 — An oversized Shopify search result poisoned the whole turn, discarding every other store's real results with it

## Failure
The user's own live session (screenshot, real Mission page) HALTED after
they typed a budget: "Halted before changing anything: connector result
did not match its verified schema." Real, reproducible -- checked
Render's own logs for the exact request.

## Cause
The Claude Agent SDK's own transport, not this codebase, replaces an
oversized tool result with a file-pointer message when it exceeds an
internal token cap: *"Error: result (100,742 characters) exceeds maximum
allowed tokens. Output has been saved to .../tool-results/....txt... You
MUST read the content from the file..."* One Shopify store's own catalog
was large enough to trigger this. The pointer text -- not real product
data -- is what reached `normalizer.py`'s JSON parser, which correctly
failed on it (it isn't JSON) and raised `ConnectorPayloadError`. That
exception propagated out of the per-call loop in `orchestrator.py` with
no isolation, discarding every OTHER store's real, already-normalized
results from the SAME turn along with the one bad one -- one oversized
catalog took down the entire search.

Worse: the pointer message instructs the model to read the saved file to
recover the content, but `Read` is deliberately in
`subscription_runtime.py`'s `_ALWAYS_DISALLOWED` list -- a real security
boundary (this agent must never read arbitrary filesystem paths), not an
oversight. So even if the exception had been survivable, the instruction
inside it is structurally impossible for this agent to follow. There is
no way to recover the real data from this specific call; the only honest
options are "fail this one store" or "fail the whole turn," and it was
doing the second when it should do the first.

## Fix
`ShopifyNormalizer.normalize` now detects this exact SDK marker
(`"exceeds maximum allowed tokens"`, matched directly against the raw
result -- stable regardless of which envelope shape wraps it) and treats
it as informational, returning `None` the same way Swiggy's
`get_addresses`/`get_cart` calls already do, instead of raising. One
oversized store's real, permanent, retry-proof limitation now costs
exactly that one store's results for that one turn -- every other
connector's real search results in the same turn survive.

## Regression test
`tests/test_normalizer.py::test_an_sdk_truncated_shopify_result_is_skipped_not_a_turn_killing_error`
-- the exact live-observed pointer message, asserts `normalize()` returns
`None` rather than raising.

## Lesson
An external system's own internal limits (here, the Agent SDK's tool-
result token cap) are not this codebase's to fix, but the BLAST RADIUS of
hitting one is. The bug was never "Shopify sometimes returns too much
data" -- that's real and permanent, not something to chase further. The
bug was treating one unrecoverable call exactly the same as a
provenance violation or a genuinely malformed fixture: as grounds to
discard an entire turn's worth of otherwise-real results.

## Production relevance
Direct -- found in the user's own real session, not a constructed test.
Any store with a large enough catalog can trigger this on any query
broad enough to match many products; this fix makes that store's absence
from that turn's results the whole cost, not a full search failure.

# F-044 — A model's own earlier false claim about a connector became self-reinforcing for the rest of the conversation

## Failure
The user's own live session (screenshot, real Mission page): turn 1 (image
attached) falsely claimed "your Swiggy Instamart connector failed to
connect this turn (auth token expired)" -- the F-041/F-042 class of
hallucination, already known. Turn 2, after the user gave a budget, the
Mission Trace's own `attempted_connector_ids` (real, ground-truth) showed
`["shopify"]` only -- Swiggy Instamart, genuinely eligible and genuinely
connected the whole time, was never attempted. The model instead ran 26
separate Shopify searches across every connected demo store, returning
armchairs, kurtas, perfumes and lip balm for a grocery list of onion,
potato, and chili powder -- Shopify simply doesn't sell groceries, so
none of it was useful, and the one connector that does was never tried.

## Cause
A prompt fix already existed telling the model not to trust an unverified
claim, including its own. It did not reliably work, because the model's
own EARLIER assistant turn, sitting right there in its resumed
conversation history, reads exactly like something it already checked and
reported. A general instruction ("don't trust past claims") is easy for a
model to satisfy in the abstract while still, in practice, treating a
specific prior statement in its own transcript as settled fact rather
than re-deriving it. This is a different, harder failure mode than F-041
(a single turn fabricating a claim) -- it is that fabrication becoming
load-bearing for every later turn in the same thread, with nothing ever
forcing a re-check.

## Fix
Not more persuasion -- a deterministic fact, recomputed and re-stated
fresh every continuation turn. `conversation_sessions.py` gained a
second sticky, cumulative field: `attempted_connector_ids_json`, the real
union of every connector actually called (evidence from orchestrator.py's
own `attempted_connector_ids`, never the model's text) anywhere earlier in
the thread. `orchestrator.run_agent_turn` now takes
`previously_attempted_connector_ids` and, on any continuation turn where
an eligible connector is NOT in that set, appends an explicit note to the
message actually sent to the runtime: "X eligible for this request but
not yet called with a real tool request anywhere in this conversation --
any earlier claim about its status was never actually verified. Attempt
it for real this turn..." Kept off the real `message` value used for
`for_query`/`extract_budget_minor` (both must only ever see the user's
own words). Only fires on a genuine continuation (`session_context`
present) -- a brand-new first turn has no stale claim yet to correct, so
adding the note there would be pure noise.

Required its own schema migration, same as F-042 -- the table had already
shipped to production with only `image_ever_attached` added; the existing
`_migrate_columns` helper was generalized to a name->DDL mapping so both
sticky columns migrate the same way on SQLite and Postgres.

## Regression test
`tests/test_conversation_sessions.py`: the attempted-connector set
accumulates across turns instead of being replaced (turn 1 attempts
shopify, turn 2 attempts swiggy-instamart, both must be remembered).
`tests/test_orchestrator.py`: the note appears on a continuation with an
unverified eligible connector; no note when that connector is already in
`previously_attempted_connector_ids`; no note on a brand-new turn with no
`session_context` at all (kept consistent with this project's existing
decomposition tests, which assert the runtime receives the message
unmodified).

## Lesson
F-041's fix (surfacing ground truth to the UI) and this one look similar
but solve different problems. F-041 makes a false claim visible and
labeled so a human isn't misled by it. This fix is about the MODEL itself
-- because an LLM can be misled by its own prior output the same way a
human reading a transcript could, a system that wants a stale claim
actually corrected needs to put a fresh, unavoidable fact in front of the
model every time, not just hope a general instruction holds against the
specific pull of "I already said this."

## Production relevance
Direct and severe -- this was the mechanism turning one bad turn into a
permanently degraded conversation: every later reply in the thread
inherited the original false claim and never recovered on its own,
producing exactly the "26 irrelevant searches, real connector never
tried" result the user reported.

## Addendum -- the first note wording changed the model's PROSE, not its actions
Live-verified immediately after deploying: turn 1 still originated the
same false claim (that's F-041's separate origin point, not what this fix
targets). Turn 2, with the note now injected, the model's reply changed to
"I just attempted Swiggy Instamart for real... the server rejected the
connection with an expired-token auth error" -- but
`attempted_connector_ids` for that turn was `[]`. Zero real tool calls,
to either connector. The model absorbed the note's own vocabulary
("attempt it for real this turn") and used it to construct a MORE
convincing-sounding version of the identical fabrication, rather than
actually making the call. The structural ground truth
(`attempted_connector_ids`, the UI's "don't trust the reply text" warning)
was still completely correct throughout -- but the underlying experience
(a real search never happening) was not fixed by the first wording.

Rewrote the note from contextual background ("here is a fact you might
consider") to an explicit instruction with a named prohibition on the
exact failure just observed: "Before you write anything else, call it. Do
not write a sentence like 'I attempted X' or 'X failed/rejected the
connection' unless you are describing a tool_use block you actually
emitted THIS turn and a real tool_result you actually received back for
it." Whether this closes the gap for good or only shrinks it further has
not yet been re-verified live at the time of this addendum -- the honest
status is "changed the model's language once already; the deterministic
`attempted_connector_ids` field remains the part that does not depend on
whether this wording works any better than the last one."

## Second addendum -- the model was telling the truth, and F-041/F-044's whole premise was wrong for this case

Re-verifying the stronger instruction wording live surfaced something more
important than whether it worked: the model refused to obey the injected
note at all, correctly identifying it as an untrusted instruction embedded
in user-turn content ("real instructions to me arrive in properly tagged
system blocks, not embedded inside your chat turn") -- the exact
prompt-injection defense `prompt.py` itself asks for, now firing against
this fix's own mechanism. It went on to state, more specifically than
before: "I have no callable tool for swiggy-instamart at all right now...
no tools from it were even loaded into my tool list."

That claim was checked against real evidence instead of assumed false.
The Claude Agent SDK's own `SystemMessage(subtype="init")` reports
per-server MCP connection status -- `mcp_servers: [{"name":
"swiggy-instamart", "status": "failed"}]` -- and this codebase had never
read anything from that message besides `session_id`. **The handshake
was genuinely, verifiably failing.** The model was not hallucinating a
cause; it was accurately reporting a real fact this codebase had no way
to check and was instead judging against a stale, misleading proxy:
`/api/agent/connectors`'s "CONNECTED" status only checks whether a token
row exists in the database, never whether the connector's live MCP
handshake is currently succeeding -- the two had silently drifted apart,
almost certainly because a real OAuth token expired sometime during this
session's many hours of live testing.

This means F-041's original finding and every fix cycle since inherited
a false premise: some fraction of what was treated as "the model
fabricating a technical failure" was the model correctly reporting a real
one, just described with an unverifiable specific cause (the SDK's report
is a bare `status: "failed"`, not an HTTP code -- "expired token" and
"401" were themselves guesses, just attached to a true underlying fact).
F-037's original hallucination (fabricated Shopify prices with zero tool
calls) and F-041's Shopify-only case remain real, separately-verified
instances of the model answering without evidence — this correction
narrows what F-044 was solving, it does not erase the other cases.

### Fix
Stopped guessing and started reading the real signal. `runtime/base.py`'s
`AgentTurnResult` gained `failed_connector_ids: list[str]`, populated in
`subscription_runtime.py` from the init message's own `mcp_servers`
status list (mapped back to a real `connector_id` via the same
`spec_by_server` lookup already used for tool-call provenance). Threaded
through `orchestrator.py`'s `MissionStepResult`, both `app.py` response
builders, and a new sticky `failed_connector_ids_json` column on
`conversation_sessions.py` (same accumulate-don't-replace pattern as
`attempted_connector_ids_json`, same dual-dialect migration). The
unverified-connector nudge now excludes anything in
`previously_failed_connector_ids` -- telling a model to keep retrying a
connector already confirmed broken by real evidence is noise, not a
correction. `PipelineCanvas.tsx` renders a verified MCP failure as its own
node ("MCP CONNECTION FAILED — VERIFIED BY THE AGENT SDK'S OWN CONNECTION
REPORT") distinct from the "NOT SEARCHED, don't trust the reply" warning,
since the two now mean genuinely different things.

### Regression test
`tests/test_runtime_adapters.py`: a mocked SDK init message reporting a
failed server produces the right `failed_connector_ids`; a clean init
message produces none. `tests/test_orchestrator.py`: a connector in
`previously_failed_connector_ids` gets no retry nudge while a genuinely
unverified one still does. `tests/test_conversation_sessions.py`: the
failed set is sticky and tracked separately from the attempted set.

### Lesson
The most expensive assumption in this whole thread was treating
"unverifiable" and "false" as the same thing. `/api/agent/connectors`'s
"CONNECTED" label was never wrong on its own terms (a token row really
did exist) -- it was answering a different question than the one being
asked of it, and nothing forced that mismatch into view until the SDK's
own, already-available signal was finally read instead of discarded.
Every fix before this one (F-041's ground-truth field, F-044's nudge)
was still real, correct engineering for the cases that ARE model
narration outrunning evidence -- but none of them could have found this,
because they were all built on the same unverified assumption about what
"CONNECTED" meant. Read the primary signal before building more scaffolding
around a secondary one.

### Production relevance
Direct, and reframes the practical fix for the user's original report:
reconnecting the Swiggy Instamart connector (a fresh OAuth handshake) is
what actually resolves it, not further prompt or code changes to how the
model behaves when a connector is unavailable. The new `failed_connector_ids`
signal is also now the honest way to detect this class of problem going
forward, in the UI, without relying on a stale token-existence check or
the model's own account of what happened.

### Third addendum -- visually testing the fix found a gap in the fix's own UI

Testing this live in the actual Mission page (not just the API): the
model's reply was now honest and specific ("the swiggy-instamart
connector failed to connect — the server rejected its authorization
token as expired, HTTP 401... a connection issue on the connector's end,
not something I can retry around"), matching real, verified data. But the
Mission Trace panel still showed the old, generic "ELIGIBLE — NOT CALLED
YET" label for the connector node, with no mention of the verified
failure at all.

Cause: `PipelineCanvas.tsx`'s new "MCP CONNECTION FAILED" node was only
ever added as a SEPARATE node, gated behind `hasConnector` (i.e.
`step.connector_id` being non-null). But the exact scenario this whole
fix exists for — the ONE eligible connector's handshake failing before
any tool call can even be attempted — is precisely the case where
`chosen_connector_id` stays null all turn, so `hasConnector` is false and
the new node never rendered; the primary connector node fell through to
its old generic fallback instead.

Fixed by checking for a verified failure in the PRIMARY connector node's
own construction, before its generic "not called yet" fallback, not only
in the separate supplementary node used for the multi-connector case.

Lesson: the same class of gap this whole investigation is about — a
signal that exists but isn't checked in the specific place that matters
most — can hide inside your own fix for it. Testing the actual UI a user
sees, not just the API response shape, is what caught this; the API-level
verification in the prior addendum was correct and complete for the data
layer, and still missed a real, visible gap one layer up.

### Fourth addendum -- the Connectors page itself was still lying

A user pointed out the obvious next question: if Swiggy Instamart's real
handshake was verifiably failing, why does the Connectors page still
always say "CONNECTED"? Checked directly: `/api/agent/connectors`'s
status came from `ACCOUNTS.status(c.id)`, which only ever checks whether
a token row exists and, at best, whether a LOCALLY stored `expires_at`
guess has passed — never whether the connector's live MCP handshake is
currently succeeding. A token revoked or expired server-side, without us
also being told, looks identical to a healthy one under that check
forever. This is the exact same "unverifiable proxy mistaken for the real
thing" mistake as the second addendum's root cause, just on a different
page.

### Fix
`ConnectorAccount` gained `last_mcp_status`/`last_mcp_checked_at`,
migrated on both dialects (this table shipped to production long before
this session; same lesson as every prior migration in this file — never
assume a live Postgres table is fresh). `ConnectorAccountStore` gained
`record_mcp_status()` (updates an existing row only — never creates one,
never implies a connection that was never made) and `mcp_health()` (real
status + when it was checked, or `(None, None)` if never actually
attempted this deployment — a real, honest third state, not folded into
either CONNECTED or FAILED). `subscription_runtime.py`'s init-message
handling now captures the SDK's FULL per-server status map, not only
failures, via a new `AgentTurnResult.mcp_server_statuses` field, threaded
through `MissionStepResult` and both `app.py` mission/run endpoints, which
now call `ACCOUNTS.record_mcp_status()` for every connector actually
reported on after each real mission turn. `/api/agent/connectors` exposes
this as `mcp_verified_status`/`mcp_verified_checked_at` alongside the
existing (now-labeled-honestly-limited) `status` field, and the
Connectors page renders it as its own badge — "MCP HANDSHAKE FAILED,
checked 3m ago" instead of a silent, permanent "CONNECTED".

### Regression test
`tests/test_connector_accounts.py`: health starts unset until a real turn
reports it; recording updates it; recording for a connector with no
stored token is a no-op, never a phantom row.

### Lesson
Every fix in this whole F-044 thread eventually traced back to the same
shape of mistake: a proxy value (a token's existence, a locally-stored
expiry, a static page load) was being read as if it answered "is this
working right now," when it only ever answered a narrower, related
question. The fix each time was the same move — find the primary, live
signal that was already available and had simply never been read, and
make THAT the source of truth instead of adding another layer of
inference on top of the proxy.

### Production relevance
Direct — this is the actual, permanent fix for "why does it always say
connected," not a one-time correction. It self-updates on every real
mission turn from here forward, for any connector, not just Swiggy
Instamart.

# F-045 — Deploying the connector-health fix itself silently hung the whole service for ten minutes

## Failure
Deploying F-044's fourth addendum (the `connectoraccount` table migration)
produced total silence: no startup logs, no error, no traceback -- just
nothing, for over six minutes, while `curl` against the live URL timed
out completely. The service was genuinely down, not just slow to deploy.

## Cause
Real, observed directly via Render's own instance metrics: the new
instance's memory climbed to 147MB (a real, loaded Python process) while
its CPU stayed near zero -- consistent with blocking on I/O, not crashing
or never starting. Postgres `ALTER TABLE` takes an ACCESS EXCLUSIVE lock
with **no default timeout**. This project's Postgres connection has been
through an unusually large number of redeploys, crashes, and a free-tier
sleep/wake cycle within this one long session -- more than enough
opportunity for an orphaned connection from an earlier, now-dead instance
to still be holding a lock Neon hadn't yet reaped. The new migration code
(this same addendum's own `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`,
run with no `lock_timeout` set) blocked on that lock indefinitely, with
nothing to ever time it out or report it -- so the whole deploy hung
before uvicorn ever logged a single line.

## How I proved it
Checked `get_metrics` for the stuck instance directly rather than
guessing: real memory growth with near-zero CPU is the specific signature
of an I/O wait, not a crash (which frees memory) or a hang before Python
even starts (which shows no memory growth at all). Confirmed a SECOND
instance hit the identical point and also stalled before concluding this
was systemic rather than a one-off. The deploy ultimately recovered on
its own at the ten-minute mark, consistent with the blocking lock being
released by Neon's own idle-connection reaping running independently.

## Fix
Both live Postgres migrations (`connector_accounts.py`,
`conversation_sessions.py` — the only two that run `ALTER TABLE` against
an already-shipped table) now run `SET LOCAL lock_timeout = '10s'` before
the `ALTER TABLE`, inside the same transaction. A stuck lock now fails
loudly with a clear Postgres error within 10 seconds instead of hanging
the entire deploy, silently, for an unbounded amount of time.

## Regression test
None added — this is a live infrastructure timing property (real lock
contention against a real Postgres connection pool), not something an
offline unit test can reproduce or usefully assert on.

## Lesson
A migration that has run safely several times before (this project's own
`_migrate_columns`/`_migrate_account_columns` pattern, used successfully
across F-039, F-042, and F-044's first three addenda) is not proof the
NEXT run is safe — each one is a fresh negotiation for a real lock
against whatever else happens to be holding the table at that exact
moment, and a long, redeploy-heavy session is exactly the condition that
makes a stale lock likeliest. Fixing the CODE was correct every time
before; this time the code was fine and the risk was in the deploy
environment's own accumulated state — worth remembering that "the same
pattern that worked before" and "safe" are not the same claim.

## Production relevance
Direct and severe — this was a real, user-facing outage (confirmed via
`curl` timing out completely) caused by shipping the very fix meant to
make the product MORE trustworthy. The `lock_timeout` fix generalizes to
every future migration this project ever adds to an already-live table,
not just these two.

# F-046 — Swiggy's own OAuth server refuses this deployment's client entirely; reconnecting through the app was never actually possible

## Failure
Following F-044's own advice ("reconnect Swiggy Instamart to fix this"),
the user clicked the real Reconnect button this session added. Swiggy's
own login page answered, not with a login form, but with: "Oops,
Onrender isn't whitelisted yet — This client isn't supported for Swiggy
MCP sign-in yet... You can request whitelisting by creating an issue on
our GitHub repo."

## Cause
Swiggy's MCP OAuth server only accepts an authorization request from a
pre-approved allowlist of client origins. This deployment's backend,
hosted on Render (`*.onrender.com`), is not on that list — and, per an
existing note already in `connectors.py`, neither is `localhost`. The
`docs/CONNECTORS.md` claim this whole project's Swiggy integration was
built against — "Live-proven via a Claude Code session" — is the reason
a working token ever existed at all: it was obtained through Claude
Code's own local MCP session on the user's machine, a client Swiggy DOES
allow, then carried into `ConnectorAccount` by a different path than the
one this app's own "Connect"/"Reconnect" button drives. That original
path to a valid token was never actually reproducible from inside this
hosted deployment itself — the button existed, and now correctly
appears, but Swiggy was always going to refuse what it does the moment
it's clicked.

## How I proved it
Did not assume the button's job was done once it was visible and wired
correctly. Watched the user actually click it and followed where Swiggy's
own server sent the browser — a real, external, third-party response,
not something in this codebase to debug further.

## Fix
None available from this codebase alone — this is Swiggy's access
control, not a bug. Real options, in order of how much they depend on
someone else:
1. Request whitelisting for this deployment's redirect URI via the link
   Swiggy's own page provides (an external process, timeline not in this
   project's control).
2. Re-derive a fresh token the same way the original one was obtained —
   through a real Claude Code session's own local MCP connection to
   Swiggy (a whitelisted client) — then find/build a real path to carry
   that token into this deployment's `ConnectorAccount` store, the same
   way the first one apparently arrived.
3. Accept Swiggy Instamart stays unavailable for a real, hosted reconnect
   until (1) resolves.

## Regression test
None — this is an external service's access policy, not code behavior
this project's own test suite can assert on.

## Lesson
A "Reconnect" button that renders correctly and points at a technically
valid OAuth URL is not the same claim as "reconnecting is possible" — the
whole reconnect flow was built and verified against the SHAPE of a real
OAuth handshake (PKCE, redirect, token exchange — all real, all tested)
without ever confirming Swiggy's own server would actually let THIS
deployment's client complete one. The gap wasn't in what this code does;
it was in an assumption about what the other end of the handshake would
allow, never directly checked until the real user actually tried it.

## Production relevance
Direct and load-bearing for this entire buildathon submission's Swiggy
story: real, live Swiggy Instamart access currently depends on a token
obtained outside this hosted deployment's own control, through a channel
(a local Claude Code session) that cannot be re-triggered from
production. Worth stating plainly rather than implying the "Reconnect"
button is a complete, self-service fix.
