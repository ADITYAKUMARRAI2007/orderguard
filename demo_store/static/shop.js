// FreshCart shop front-end.
//
// Security note: every value that comes from the server is written with
// textContent, never innerHTML. A product named "<script>...</script>" therefore
// appears as visible text and never executes.
//
// The browser also never sends a price. It sends {sku, quantity} and the server
// looks the price up. Editing anything in devtools cannot change what a thing costs.

const CART_ID = "demo-cart";

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

const rupees = (paise) => "₹" + (paise / 100).toFixed(2);

function paintCart(cart) {
  const count = cart.lines.reduce((n, l) => n + l.quantity, 0);
  document.getElementById("cartCount").textContent = count;
  document.getElementById("cartTotal").textContent = rupees(cart.total_paise);

  const pill = document.getElementById("cartPill");
  pill.classList.remove("bump");
  void pill.offsetWidth;          // restart the animation
  pill.classList.add("bump");
}

document.querySelectorAll("button.add").forEach((btn) => {
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const cart = await api(`/api/cart/${CART_ID}/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // no price here, on purpose
        body: JSON.stringify({ sku: btn.dataset.sku, quantity: 1 }),
      });
      paintCart(cart);
    } catch (err) {
      showBlocked([{ check: "Stock", why: err.message }]);
    } finally {
      btn.disabled = false;
    }
  });
});

// The video moment. Called when a safety check refuses the purchase.
function showBlocked(reasons) {
  const list = document.getElementById("blockReasons");
  list.replaceChildren();
  for (const r of reasons) {
    const li = document.createElement("li");
    const b = document.createElement("b");
    b.textContent = r.check + ": ";       // safe text write, never raw markup
    li.append(b, document.createTextNode(r.why));
    list.appendChild(li);
  }
  document.getElementById("blocker").hidden = false;
}

document.getElementById("blockClose").addEventListener("click", () => {
  document.getElementById("blocker").hidden = true;
});

// Exposed so the demo can trigger the block screen from the console or a script.
window.showBlocked = showBlocked;

api(`/api/cart/${CART_ID}`).then(paintCart).catch(() => {});
