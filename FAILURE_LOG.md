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
