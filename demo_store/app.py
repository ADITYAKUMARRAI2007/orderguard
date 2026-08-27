"""The demo shop.

Two faces on the same data:

* a **web page** for humans (and for the video)
* a **JSON API** for the agent, so it never has to scrape HTML

Three security rules, applied from the first line rather than added later:

1. **The server owns prices.** The browser sends a product id and a quantity.
   It never sends a price. A tampered browser cannot change what something costs.
2. **All shop text is escaped on render.** A product named ``<script>...</script>``
   shows up as harmless text.
3. **Product text never reaches a safety check.** Checks compare numbers and ids.
"""

from html import escape

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .catalog import Product, all_products, get_product

app = FastAPI(title="FreshCart (demo store)", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory="demo_store/static"), name="static")

# In-memory carts, keyed by cart id. Fine for a demo; nothing persists.
_CARTS: dict[str, dict[str, int]] = {}


# --- request shapes ---------------------------------------------------------

class AddToCart(BaseModel):
    """What the browser is allowed to send.

    Note what is missing: **price**. The browser cannot propose a price,
    so it cannot tamper with one.
    """
    model_config = {"extra": "forbid"}

    sku: str = Field(min_length=1)
    quantity: int = Field(ge=1, le=999)


# --- helpers ----------------------------------------------------------------

def _product_json(p: Product) -> dict:
    return {
        "sku": p.sku,
        "title": p.title,
        "price_paise": p.price_paise,
        "in_stock": p.in_stock,
        "category": p.category,
        "unit": p.unit,
        "attributes": p.attributes,
    }


def _cart_json(cart_id: str) -> dict:
    """Build the cart from server-side prices only."""
    lines = []
    for sku, qty in _CARTS.get(cart_id, {}).items():
        p = get_product(sku)
        if p is None:
            continue
        lines.append({
            "sku": p.sku,
            "title": p.title,
            "quantity": qty,
            "unit_price_paise": p.price_paise,          # from the catalog, not the browser
            "line_total_paise": qty * p.price_paise,
        })
    subtotal = sum(line["line_total_paise"] for line in lines)
    delivery = 0 if subtotal == 0 else 3000
    return {
        "cart_id": cart_id,
        "merchant": "freshcart_demo",
        "currency": "INR",
        "lines": lines,
        "subtotal_paise": subtotal,
        "delivery_paise": delivery,
        "total_paise": subtotal + delivery,
    }


# --- the API the agent uses -------------------------------------------------

@app.get("/api/catalog")
def api_catalog() -> dict:
    """Everything the agent needs to choose, in one call."""
    return {
        "merchant": "freshcart_demo",
        "currency": "INR",
        "buying_rules": {
            "max_order_paise": 5_000_000,
            "cod_available": False,
            "time_sensitive_categories": [],
        },
        "items": [_product_json(p) for p in all_products()],
    }


@app.get("/api/product/{sku}")
def api_product(sku: str) -> dict:
    p = get_product(sku)
    if p is None:
        raise HTTPException(status_code=404, detail="no such product")
    return _product_json(p)


@app.post("/api/cart/{cart_id}/add")
def api_add(cart_id: str, body: AddToCart) -> dict:
    """Add to the cart. Refuses more than the shop has."""
    p = get_product(body.sku)
    if p is None:
        raise HTTPException(status_code=404, detail="no such product")

    current = _CARTS.setdefault(cart_id, {})
    wanted = current.get(body.sku, 0) + body.quantity
    if wanted > p.in_stock:
        raise HTTPException(
            status_code=409,
            detail=f"only {p.in_stock} of {p.sku} in stock, asked for {wanted}",
        )
    current[body.sku] = wanted
    return _cart_json(cart_id)


@app.get("/api/cart/{cart_id}")
def api_cart(cart_id: str) -> dict:
    """Read the cart back.

    This is the endpoint that makes verification possible: it reports what is
    **actually** in the cart, not what anyone believes they added.
    """
    return _cart_json(cart_id)


@app.delete("/api/cart/{cart_id}")
def api_clear(cart_id: str) -> dict:
    _CARTS.pop(cart_id, None)
    return {"cart_id": cart_id, "cleared": True}


@app.get("/api/health")
def api_health() -> dict:
    return {"ok": True, "merchant": "freshcart_demo"}


# --- the page a human sees --------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def page_home() -> str:
    cards = []
    for i, p in enumerate(all_products()):
        # escape() is what stops a hostile product name becoming HTML.
        # Every value below comes from the catalog, so every one is escaped.
        title = escape(p.title)
        sku = escape(p.sku)
        img = escape(p.image)
        brand = escape(p.attributes.get("brand") or p.category.title())
        emoji = escape(p.emoji)
        rupees = f"{p.price_paise / 100:.2f}"
        out = p.in_stock == 0
        stock_txt = "Out of stock" if out else f"{p.in_stock} in stock"
        sold_out = '<span class="soldout">Sold out</span>' if out else ""
        cards.append(f"""
        <article class="card{' is-out' if out else ''}" data-sku="{sku}" style="--i:{i}">
          <div class="thumb">
            <img src="{img}" alt="{title}" loading="lazy" data-emoji="{emoji}">
            <span class="chip">{brand}</span>{sold_out}
          </div>
          <div class="body">
            <h3>{title}</h3>
            <div class="stock{' out' if out else ''}">{stock_txt}</div>
            <div class="row">
              <span class="price">&#8377;{rupees}</span>
              <button class="add" data-sku="{sku}" {"disabled" if out else ""}>Add</button>
            </div>
          </div>
        </article>""")

    return _PAGE.replace("__CARDS__", "\n".join(cards))


_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FreshCart</title>
<link rel="preconnect" href="https://images.unsplash.com">
<link rel="stylesheet" href="/static/style.css?v=3">
</head><body>

<div class="glow"></div>

<header class="topbar">
  <div class="brand"><span class="logo">&#9673;</span> FreshCart <span class="tag">demo store</span></div>
  <div class="cart-pill" id="cartPill">
    <span id="cartCount">0</span> items
    <span class="dot">&#183;</span>
    <span id="cartTotal">&#8377;0.00</span>
  </div>
</header>

<main>
  <div class="hero">
    <h1>Everyday groceries</h1>
    <p>A demo storefront. Real prices, real stock, no real money.</p>
  </div>
  <section class="grid">__CARDS__</section>
</main>

<div class="blocker" id="blocker" hidden>
  <div class="blockcard">
    <div class="blockicon">&#9940;</div>
    <h1>PURCHASE BLOCKED</h1>
    <p class="sub">The cart does not match what was requested.</p>
    <ul id="blockReasons"></ul>
    <p class="foot">Blocked by deterministic checks. The AI cannot override this.</p>
    <button id="blockClose">Close</button>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/motion@11/dist/motion.min.js"></script>
<script src="/static/shop.js?v=3"></script>
</body></html>"""
