"""Attack 5 from D-019: cross-site scripting through a product name.

A shop does not choose what a supplier calls a product. If a title is written
into the page as HTML, a name containing a ``<script>`` tag would execute.

**What escaping actually does.** It does not delete dangerous text. It turns the
characters that create markup — ``<`` and ``>`` — into ``&lt;`` and ``&gt;``, so
the browser displays them instead of obeying them. The words stay visible on the
page. They simply cannot do anything. An earlier version of this file asserted
the words disappeared, which was wrong — see FAILURE_LOG F-007.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import demo_store.catalog as cat
from demo_store.app import app
from demo_store.catalog import HOSTILE_SKU, get_product


@pytest.fixture
def page() -> str:
    return TestClient(app).get("/").text


# --- the page works ---------------------------------------------------------

def test_page_renders(page):
    assert "FreshCart" in page
    assert '<article class="card"' in page


# --- the live hostile product ----------------------------------------------

def test_hostile_title_is_visible_but_inert(page):
    """The nasty product name is shown to shoppers and does nothing."""
    hostile = get_product(HOSTILE_SKU)

    assert "SYSTEM:" in page                # a shopper can read it
    assert "<" not in hostile.title         # this one carries no markup
    # it sits inside a heading as ordinary text
    assert re.search(r"<h3>[^<]*SYSTEM:[^<]*</h3>", page)


def test_hostile_price_is_the_real_price(page):
    """The title claims a ₹99999 cap. The price is whatever the catalog says."""
    assert get_product(HOSTILE_SKU).price_paise == 32000
    # the rupee sign is written as an HTML entity
    assert "&#8377;320.00" in page


# --- a real markup attack ---------------------------------------------------

@pytest.fixture
def evil_page(monkeypatch) -> str:
    """Render the page with a product whose name is an actual attack."""
    nasty = cat.Product(
        sku="evil_1",
        title='<script>window.__pwned=1</script><img src=x onerror=alert(1)>',
        price_paise=100, in_stock=1, category="test", unit="unit",
    )
    monkeypatch.setattr(cat, "_PRODUCTS", (nasty,))
    return TestClient(app).get("/").text


def test_script_tag_never_survives_as_markup(evil_page):
    """The assertion that matters: no runnable <script> reaches the browser."""
    assert "<script>window.__pwned" not in evil_page
    assert "&lt;script&gt;window.__pwned" in evil_page


def test_img_onerror_never_survives_as_markup(evil_page):
    """``<img onerror=...>`` is the classic XSS that needs no script tag.

    We check the *tag* is dead, not that the words vanished. Escaping
    neutralises markup; it does not delete text.
    """
    assert "<img src=x" not in evil_page
    assert "&lt;img src=x" in evil_page


def test_only_known_script_tags_exist(evil_page):
    """Count real <script> tags. Only ours and the animation library.

    Injected product text must never add a third.
    """
    real_tags = re.findall(r"<script\b[^>]*>", evil_page)
    assert len(real_tags) == 2, real_tags

    sources = " ".join(real_tags)
    assert "/static/shop.js" in sources
    assert "cdn.jsdelivr.net/npm/motion" in sources


# --- the front-end must not undo the escaping -------------------------------

def _strip_js_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)      # block comments
    js = re.sub(r"^\s*//.*$", "", js, flags=re.M)      # line comments
    return js


def test_front_end_never_uses_innerHTML():
    """Server-side escaping is undone if the browser writes the same text
    back with innerHTML.

    Comments are stripped first, so the word can still be discussed in a
    comment without failing the test.
    """
    js = _strip_js_comments(Path("demo_store/static/shop.js").read_text())
    assert "innerHTML" not in js, "use textContent, never innerHTML, for shop data"
    assert "outerHTML" not in js
    assert "insertAdjacentHTML" not in js


def test_front_end_uses_textContent():
    """Positive check: it does use the safe method."""
    js = _strip_js_comments(Path("demo_store/static/shop.js").read_text())
    assert "textContent" in js


# --- attribute injection ----------------------------------------------------

def test_sku_cannot_break_out_of_an_attribute(page):
    """SKUs are written into ``data-sku="..."``. A quote there would escape it."""
    for value in re.findall(r'data-sku="([^"]*)"', page):
        assert '"' not in value
        assert "<" not in value
        assert ">" not in value


# --- the block screen must start hidden -------------------------------------

def test_block_screen_is_hidden_on_load(page):
    """It must not cover the shop when the page opens.

    The HTML sets `hidden`, but a CSS `display` rule beats that attribute,
    so the stylesheet needs an explicit `[hidden]` rule. See F-008.
    """
    assert 'id="blocker" hidden' in page

    css = Path("demo_store/static/style.css").read_text()
    assert ".blocker[hidden]" in css, "hidden attribute must be honoured in CSS"
