"""MCP tool results normalized into typed ``ConnectorResult`` payloads. The
Shopify branch reuses ``commerce.shopify_mcp``'s already-tested money
parsers against a real fixture shape from that same adapter's own docstring;
Swiggy/GitHub fixtures are built from this project's recorded live-evidence
notes (see ``normalizer.py``'s own docstring on what's tested vs. best-effort).
"""

import pytest

from orderguard.agent.normalizer import ConnectorPayloadError, normalize
from orderguard.agent.results import CommerceResult, DevTaskResult
from orderguard.agent.runtime.base import ToolCallEvent


def test_shopify_search_normalizes_into_commerce_offers():
    call = ToolCallEvent(
        connector_id="shopify", tool_name="search_catalog",
        arguments={"store": "slurrpfarm.com"},
        result={
            "products": [{
                "id": "p1", "title": "Millet Cereal", "url": "https://slurrpfarm.com/p1",
                "variants": [{
                    "id": "v1", "title": "Default Title",
                    "price": {"amount": 26400, "currency": "INR"},
                    "availability": {"available": True},
                }],
            }],
        },
    )
    result = normalize(call, capability="COMMERCE_GENERAL", risk_tier="R0", provenance="stub")
    assert isinstance(result.payload, CommerceResult)
    assert len(result.payload.offers) == 1
    offer = result.payload.offers[0].offer
    assert offer.price_minor == 26400
    assert offer.currency == "INR"
    assert offer.title == "Millet Cereal"


def test_shopify_search_rejects_a_malformed_variant_instead_of_guessing():
    call = ToolCallEvent(
        connector_id="shopify", tool_name="search_catalog", arguments={},
        result={"products": [{"id": "p1", "title": "Bad", "variants": [
            {"id": "v1", "price": {"amount": "not-a-number", "currency": "INR"}},
        ]}]},
    )
    with pytest.raises(ConnectorPayloadError):
        normalize(call, capability="COMMERCE_GENERAL", risk_tier="R0", provenance="stub")


# Trimmed from a real live call (2026-08-31, search_products query="milk",
# address in Electronic City — see docs/CONNECTORS.md and normalizer.py's
# SwiggyNormalizer docstring). The full response had 20+ products; this
# keeps one product with two variations, which is enough to prove both the
# per-variation offer split and the rupees->paise conversion.
_REAL_SWIGGY_SEARCH_RESULT = {
    "nextOffset": "1",
    "products": [
        {
            "displayName": "Heritage Daily Health Toned Milk",
            "brand": "Heritage",
            "inStock": True,
            "isAvail": True,
            "productId": "86CIN32V02",
            "parentProductId": "5PP1IH3MYF",
            "isPromoted": False,
            "variations": [
                {
                    "spinId": "KZJCAK9CG9", "skuId": "UY5XCIY7F2",
                    "quantityDescription": "500 ml x 4",
                    "displayName": "Heritage Daily Health Toned Milk",
                    "brandName": "Heritage",
                    "price": {"mrp": 108, "offerPrice": 108, "unitLevelPrice": "5.4/100 ml"},
                    "isInStockAndAvailable": True,
                    "imageUrl": "https://media-assets.swiggy.com/x.png",
                    "rating": {"value": "4.4", "count": "118.9k"},
                    "sla": {"value": "12", "unit": "MINS"},
                    "vegClassifier": "VEG_CLASSIFIER_VEG",
                    "maxQuantity": 1,
                    "maxQuantityMessage": "Only 1 unit(s) of this item can be added per order.",
                },
                {
                    "spinId": "M4XP8K9M1H", "skuId": "XT17X20GXR",
                    "quantityDescription": "500 ml",
                    "displayName": "Heritage Daily Health Toned Milk",
                    "brandName": "Heritage",
                    "price": {"mrp": 27, "offerPrice": 27, "unitLevelPrice": "5.4/100 ml"},
                    "isInStockAndAvailable": True,
                    "imageUrl": "https://media-assets.swiggy.com/x.png",
                    "rating": {"value": "4.4", "count": "118.9k"},
                    "sla": {"value": "12", "unit": "MINS"},
                    "vegClassifier": "VEG_CLASSIFIER_VEG",
                    "maxQuantity": 15,
                    "maxQuantityMessage": "That's all we have in stock at the moment!",
                },
            ],
        },
    ],
}


def test_swiggy_search_normalizes_into_commerce_offers():
    call = ToolCallEvent(
        connector_id="swiggy-instamart", tool_name="search_products", arguments={},
        result=_REAL_SWIGGY_SEARCH_RESULT,
    )
    result = normalize(call, capability="COMMERCE_GROCERY", risk_tier="R0", provenance="stub")
    assert isinstance(result.payload, CommerceResult)
    # One product, two variations -> two offers, not one.
    assert len(result.payload.offers) == 2

    # variant_id is spinId, not skuId: update_cart's real schema requires
    # items[].spinId (skuId is only an optional extra) — confirmed against
    # the live tool schema, not assumed.
    pack_of_4 = next(o.offer for o in result.payload.offers if o.offer.variant_id == "KZJCAK9CG9")
    assert pack_of_4.price_minor == 10800  # ₹108 -> paise, not ₹1.08
    assert pack_of_4.currency == "INR"
    assert pack_of_4.title == "Heritage Daily Health Toned Milk"
    assert pack_of_4.variant_title == "500 ml x 4"
    assert pack_of_4.available is True

    single = next(o.offer for o in result.payload.offers if o.offer.variant_id == "M4XP8K9M1H")
    assert single.price_minor == 2700  # ₹27 -> paise
    assert single.variant_title == "500 ml"


def test_a_plain_search_ranks_the_exact_match_above_a_derivative_product():
    """Real, live-found gap (2026-09-04): every real offer used to score
    relevance=1.0 unconditionally, so a search for "onion" gave "Onion" and
    "Onion Paste" the identical score -- Swiggy's own search returning a
    loosely-related derivative product could then outrank the actual thing
    asked for on price alone. relevance must distinguish an exact-ish title
    match from one with real extra words, and the offers list must actually
    come back sorted by it, not just carry the number unused."""
    result_data = {
        "products": [
            {
                "displayName": "Onion Paste", "inStock": True, "isAvail": True,
                "productId": "P-PASTE",
                "variations": [{
                    "spinId": "V-PASTE", "skuId": "SKU-PASTE", "displayName": "Onion Paste",
                    "price": {"offerPrice": 40}, "isInStockAndAvailable": True,
                }],
            },
            {
                "displayName": "Onion", "inStock": True, "isAvail": True,
                "productId": "P-ONION",
                "variations": [{
                    "spinId": "V-ONION", "skuId": "SKU-ONION", "displayName": "Onion",
                    "price": {"offerPrice": 45}, "isInStockAndAvailable": True,
                }],
            },
        ],
    }
    call = ToolCallEvent(
        connector_id="swiggy-instamart", tool_name="search_products",
        arguments={"query": "onion"}, result=result_data,
    )
    result = normalize(call, capability="COMMERCE_GROCERY", risk_tier="R0", provenance="stub")
    offers = result.payload.offers

    onion = next(o for o in offers if o.offer.variant_id == "V-ONION")
    paste = next(o for o in offers if o.offer.variant_id == "V-PASTE")
    assert onion.relevance == 1.0
    assert paste.relevance < onion.relevance
    # Cheaper AND worse-matching must still lose -- relevance is checked
    # before price, the same order rank() already documents.
    assert offers[0].offer.variant_id == "V-ONION"


def test_shopify_search_ranks_by_relevance_using_the_nested_catalog_query():
    call = ToolCallEvent(
        connector_id="shopify", tool_name="search_catalog",
        arguments={"store": "example.com", "catalog": {"query": "onion"}},
        result={
            "products": [
                {
                    "id": "p-paste", "title": "Onion Paste", "url": "",
                    "variants": [{
                        "id": "v-paste", "title": "200g",
                        "price": {"amount": 4000, "currency": "INR"},
                        "availability": {"available": True},
                    }],
                },
                {
                    "id": "p-onion", "title": "Onion", "url": "",
                    "variants": [{
                        "id": "v-onion", "title": "1kg",
                        "price": {"amount": 4500, "currency": "INR"},
                        "availability": {"available": True},
                    }],
                },
            ],
        },
    )
    result = normalize(call, capability="COMMERCE_GENERAL", risk_tier="R0", provenance="stub")
    offers = result.payload.offers
    assert offers[0].offer.variant_id == "v-onion"
    assert offers[0].relevance == 1.0
    paste = next(o for o in offers if o.offer.variant_id == "v-paste")
    assert paste.relevance < 1.0


def test_no_query_available_keeps_the_old_all_relevant_behavior():
    """Backward-compatible default: a call this codebase can't extract a
    query from (or a caller that never passes ``arguments`` at all) must
    not start silently zeroing out every offer's relevance."""
    call = ToolCallEvent(
        connector_id="swiggy-instamart", tool_name="search_products", arguments={},
        result=_REAL_SWIGGY_SEARCH_RESULT,
    )
    result = normalize(call, capability="COMMERCE_GROCERY", risk_tier="R0", provenance="stub")
    assert all(o.relevance == 1.0 for o in result.payload.offers)


def test_swiggy_unavailable_variation_or_product_is_reported_unavailable():
    result_data = {
        "products": [{
            "displayName": "Milk", "brand": "X", "inStock": False, "isAvail": True,
            "productId": "P1",
            "variations": [{
                "spinId": "S1", "skuId": "V1", "displayName": "Milk",
                "price": {"mrp": 30, "offerPrice": 30},
                "isInStockAndAvailable": True,
            }],
        }],
    }
    call = ToolCallEvent(
        connector_id="swiggy-instamart", tool_name="search_products", arguments={}, result=result_data,
    )
    result = normalize(call, capability="COMMERCE_GROCERY", risk_tier="R0", provenance="stub")
    # Variation itself says available, but the product is out of stock —
    # the offer must reflect the product-level state, not just the variation's.
    assert result.payload.offers[0].offer.available is False


def test_swiggy_get_addresses_is_informational_not_a_failure():
    """Regression for a real, reproduced incident: Swiggy's own
    search_products tool description REQUIRES calling get_addresses first
    ("You MUST call get_addresses first... NEVER guess, invent, or use
    placeholder values"). A real live mission correctly did exactly that,
    and the orchestrator raised ConnectorPayloadError on the successful
    prerequisite call before it ever reached the search — the mission failed
    with "unsupported Swiggy fixture" despite nothing having gone wrong."""
    call = ToolCallEvent(
        connector_id="swiggy-instamart", tool_name="get_addresses", arguments={},
        result={"addresses": [{"id": "217934016", "addressLine": "..."}], "total": 1},
    )
    result = normalize(call, capability="COMMERCE_GROCERY", risk_tier="R0", provenance="stub")
    assert result is None


def test_swiggy_get_cart_is_also_informational_not_a_failure():
    call = ToolCallEvent(
        connector_id="swiggy-instamart", tool_name="get_cart", arguments={},
        result={"items": [], "total": 0},
    )
    result = normalize(call, capability="COMMERCE_GROCERY", risk_tier="R0", provenance="stub")
    assert result is None


def test_swiggy_food_search_menu_accepts_a_fractional_price():
    """Regression for a real, reproduced incident: after the fix above
    landed, the same live scenario ("order pizza... Domino's... margherita
    options") hit a SECOND, distinct schema halt — "payload did not match
    fixture at items.5.price: Input should be a valid integer". A real live
    search_menu response had an item at that index with a fractional price
    (a discounted item), which the fixture's `price: int` field strictly
    rejected outright. Fixed by widening the fixture to `price: float`
    (Pydantic strict mode accepts int input for a float field too, so
    whole-rupee prices still work) and rounding at the paise conversion."""
    call = ToolCallEvent(
        connector_id="swiggy-food", tool_name="search_menu", arguments={},
        result={
            "items": [
                {
                    "name": "Discounted Slice", "price": 84.5, "isVeg": True,
                    "menu_item_id": "x1", "inStock": 1, "imageUrl": "",
                    "hasVariants": False, "hasAddons": False,
                },
            ],
            "total": 1, "query": "pizza",
        },
    )
    result = normalize(call, capability="COMMERCE_FOOD", risk_tier="R0", provenance="stub")
    assert len(result.payload.offers) == 1
    assert result.payload.offers[0].offer.price_minor == 8450


def test_swiggy_food_search_menu_normalizes_no_variant_items_into_offers():
    """Fixture trimmed from a real live search_menu call against
    mcp.swiggy.com/food (2026-09-01, address in Electronic City). Real,
    reproduced incident: a live multi-intent mission had the model
    correctly call search_menu after a food order, and the whole turn
    halted with "connector result did not match its verified schema" —
    this normalizer never existed; only Instamart's search_products did."""
    call = ToolCallEvent(
        connector_id="swiggy-food", tool_name="search_menu", arguments={},
        result={
            "items": [
                {
                    "name": "Margherita Pizza Regular", "price": 112, "isVeg": True,
                    "menu_item_id": "163999721", "inStock": 1,
                    "imageUrl": "https://media-assets.swiggy.com/y.jpg",
                    "hasVariants": False, "hasAddons": False,
                },
            ],
            "total": 1, "query": "margherita pizza", "restaurantIdOfAddedItem": "239857",
            "hasMore": False, "nextOffset": 0, "totalItems": 1,
        },
    )
    result = normalize(call, capability="COMMERCE_FOOD", risk_tier="R0", provenance="stub")
    assert len(result.payload.offers) == 1
    offer = result.payload.offers[0].offer
    assert offer.title == "Margherita Pizza Regular"
    assert offer.variant_id == "163999721"
    # price is rupees on the wire (112), not paise — same convention as
    # Instamart's offerPrice, converted the same way.
    assert offer.price_minor == 11200


def test_swiggy_food_search_menu_excludes_multi_variant_items_honestly():
    """A dish whose real purchasable unit is a combination across multiple
    variant groups (Crust x Size) is not represented as a candidate —
    update_food_cart's real argument shape for naming one specific
    combination has not been verified, and guessing one would be exactly
    the kind of unverified claim this project's own rules forbid."""
    call = ToolCallEvent(
        connector_id="swiggy-food", tool_name="search_menu", arguments={},
        result={
            "items": [
                {
                    "name": "Double Cheese Margherita Pizza", "price": 199, "isVeg": True,
                    "menu_item_id": "48278962", "inStock": 1, "imageUrl": "",
                    "hasVariants": True, "hasAddons": True,
                    "variantsV2": [{
                        "groupId": "36921896", "name": "Crust",
                        "variations": [{"name": "New Hand Tossed", "id": "115021822", "inStock": 1, "default": 1}],
                    }],
                },
            ],
            "total": 1, "query": "margherita pizza", "hasMore": False, "nextOffset": 0, "totalItems": 1,
        },
    )
    result = normalize(call, capability="COMMERCE_FOOD", risk_tier="R0", provenance="stub")
    assert result.payload.offers == []


def test_swiggy_food_search_restaurants_is_informational_not_a_failure():
    """A restaurant list is venues, not priced items — same "real success,
    nothing offer-shaped to report" treatment as get_addresses."""
    call = ToolCallEvent(
        connector_id="swiggy-food", tool_name="search_restaurants", arguments={},
        result={
            "restaurants": [{"id": "239857", "name": "Domino's Pizza", "availabilityStatus": "OPEN"}],
            "total": 1, "query": "pizza", "hasMore": False, "nextOffset": 1, "totalRestaurants": 1,
        },
    )
    result = normalize(call, capability="COMMERCE_FOOD", risk_tier="R0", provenance="stub")
    assert result is None


def test_swiggy_food_get_addresses_is_also_informational_not_a_failure():
    """Regression for a real, reproduced incident: get_addresses is shared
    across Instamart and Food on Swiggy's own MCP (its tool description says
    so directly), and the model correctly called it before a food order —
    same reason Instamart requires it — but the connector registry never
    declared the tool for swiggy-food, so the whole turn was vetoed as an
    ineligible tool selection, and even after registering it, this
    normalizer only special-cased "swiggy-instamart" and would still have
    raised ConnectorPayloadError on a perfectly successful call."""
    call = ToolCallEvent(
        connector_id="swiggy-food", tool_name="get_addresses", arguments={},
        result={"addresses": [{"id": "217934016"}], "total": 1},
    )
    result = normalize(call, capability="COMMERCE_FOOD", risk_tier="R0", provenance="stub")
    assert result is None


def test_swiggy_food_get_food_cart_is_also_informational_not_a_failure():
    call = ToolCallEvent(
        connector_id="swiggy-food", tool_name="get_food_cart", arguments={},
        result={"items": [], "total": 0},
    )
    result = normalize(call, capability="COMMERCE_FOOD", risk_tier="R0", provenance="stub")
    assert result is None


def test_a_genuinely_unsupported_swiggy_operation_still_fails_closed():
    """The get_addresses/get_cart carve-out must not become a general
    escape hatch — anything else Swiggy-shaped and unrecognized still fails
    loudly, per this file's own strict-normalizer policy."""
    call = ToolCallEvent(
        connector_id="swiggy-instamart", tool_name="some_new_unreviewed_tool",
        arguments={}, result={"anything": "at all"},
    )
    with pytest.raises(ConnectorPayloadError):
        normalize(call, capability="COMMERCE_GROCERY", risk_tier="R0", provenance="stub")


def test_github_issues_normalize_into_dev_task_result():
    call = ToolCallEvent(
        connector_id="github", tool_name="list_issues", arguments={},
        result=[{"number": 42, "title": "Fix bug", "state": "open",
                 "html_url": "https://github.com/x/y/issues/42", "user": {"login": "octocat"}}],
    )
    result = normalize(call, capability="DEV_TASK", risk_tier="R0", provenance="stub")
    assert isinstance(result.payload, DevTaskResult)
    assert result.payload.items[0]["number"] == 42
    assert result.payload.items[0]["author"] == "octocat"


def test_an_error_result_fails_closed():
    call = ToolCallEvent(connector_id="github", tool_name="list_issues", arguments={}, result=None, is_error=True)
    with pytest.raises(ConnectorPayloadError):
        normalize(call, capability="DEV_TASK", risk_tier="R0", provenance="stub")


def test_an_unregistered_connector_tool_pair_fails_closed():
    call = ToolCallEvent(connector_id="mystery", tool_name="do_something", arguments={}, result={"x": 1})
    with pytest.raises(ConnectorPayloadError):
        normalize(call, capability="UNKNOWN", risk_tier="R0", provenance="stub")


@pytest.mark.parametrize("wrong_field", ["price", "sale_price", "amount", "finalPrice", "offer_price"])
def test_swiggy_unknown_price_aliases_are_never_guessed(wrong_field):
    call = ToolCallEvent(
        connector_id="swiggy-instamart", tool_name="search_products", arguments={},
        result={"products": [{
            "displayName": "Yogurt", "inStock": True, "isAvail": True, "productId": "p1",
            "variations": [{
                "spinId": "s1", "skuId": "v1", "displayName": "Yogurt",
                "isInStockAndAvailable": True, wrong_field: 29900,
            }],
        }]},
    )
    with pytest.raises(ConnectorPayloadError):
        normalize(call, capability="COMMERCE_GROCERY", risk_tier="R0", provenance="stub")


def test_mcp_text_content_envelope_is_decoded_without_discarding_raw_result():
    raw = [{"type": "text", "text": '{"issues":[{"number":7,"title":"Bug","state":"open","html_url":"https://github.com/x/y/issues/7","user":{"login":"octo"}}]}'}]
    call = ToolCallEvent(
        connector_id="github", tool_name="list_issues", arguments={},
        execution_id="tool-7", result=raw,
    )
    result = normalize(call, capability="DEV_TASK", risk_tier="R0", provenance="stub:github")
    assert call.result is raw
    assert result.execution_id == "tool-7"
    assert result.payload.items[0]["number"] == 7


def test_a_second_non_json_text_block_does_not_break_decoding():
    """Real, live-observed regression (2026-09-03, see FAILURE_LOG.md
    F-037): Shopify's search_catalog started returning a SECOND text block
    -- a plain-English deprecation notice -- alongside its actual JSON
    payload, wrapped in the same `[{"type": "text", "text": ...}]` envelope.
    The single-block fast path can't tell the two apart by position alone;
    it must find whichever block actually decodes as JSON."""
    raw = [
        {"type": "text", "text": '{"products": []}'},
        {
            "type": "text",
            "text": "DEPRECATION NOTICE: This tool is served by the Storefront MCP "
            "server at /api/mcp and will no longer be accessible after August 31, 2026.",
        },
    ]
    call = ToolCallEvent(
        connector_id="shopify", tool_name="search_catalog",
        arguments={"store": "slurrpfarm.com"}, result=raw,
    )
    result = normalize(call, capability="COMMERCE_GENERAL", risk_tier="R0", provenance="stub")
    assert isinstance(result.payload, CommerceResult)
    assert result.payload.offers == []


def test_a_second_non_json_text_block_before_the_real_payload_also_decodes():
    raw = [
        {"type": "text", "text": "DEPRECATION NOTICE: not JSON at all"},
        {"type": "text", "text": '{"products": []}'},
    ]
    call = ToolCallEvent(
        connector_id="shopify", tool_name="search_catalog",
        arguments={"store": "slurrpfarm.com"}, result=raw,
    )
    result = normalize(call, capability="COMMERCE_GENERAL", risk_tier="R0", provenance="stub")
    assert isinstance(result.payload, CommerceResult)
    assert result.payload.offers == []


def test_multiple_text_blocks_with_no_json_object_fails_closed():
    raw = [
        {"type": "text", "text": "just some notice"},
        {"type": "text", "text": "another plain notice"},
    ]
    call = ToolCallEvent(
        connector_id="shopify", tool_name="search_catalog",
        arguments={"store": "slurrpfarm.com"}, result=raw,
    )
    with pytest.raises(ConnectorPayloadError):
        normalize(call, capability="COMMERCE_GENERAL", risk_tier="R0", provenance="stub")


def test_an_sdk_truncated_shopify_result_is_skipped_not_a_turn_killing_error():
    """Real, live-observed regression (2026-09-03, see FAILURE_LOG.md): a
    Shopify store's own catalog was large enough that the Claude Agent SDK
    itself -- not this codebase -- substituted the real tool result with a
    "read this file for the rest" pointer message. This agent has Read
    deliberately disallowed (subscription_runtime.py), so that instruction
    can never be followed; there is no real data to recover. Before this
    fix, that pointer text reached JSON parsing, raised, and discarded
    every OTHER store's real results from the same turn along with it --
    this is skipped as informational instead, the same as Swiggy's
    get_addresses/get_cart calls."""
    raw = [{
        "type": "text",
        "text": "Error: result (100,742 characters) exceeds maximum allowed tokens. "
        "Output has been saved to /tmp/mcp-shopify-search_catalog-123.txt.\n"
        "Format: JSON array with schema: [{type: string, text: string}]\n"
        "Use offset and limit parameters to read specific portions of the file...",
    }]
    call = ToolCallEvent(
        connector_id="shopify", tool_name="search_catalog",
        arguments={"store": "hugecatalogshop.com"}, result=raw,
    )
    result = normalize(call, capability="COMMERCE_GENERAL", risk_tier="R0", provenance="stub")
    assert result is None
