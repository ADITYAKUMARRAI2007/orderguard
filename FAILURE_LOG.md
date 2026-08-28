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
