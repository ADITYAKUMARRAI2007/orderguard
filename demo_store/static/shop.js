// FreshCart shop front-end.
//
// Animation: Motion One (the vanilla sibling of Framer Motion — same spring
// engine, no React needed). Loaded from CDN; the shop works without it.
//
// Security, unchanged from the first version:
//  * every value from the server is written with textContent, never as markup
//  * the browser never sends a price. It sends {sku, quantity} and the server
//    looks the price up. Editing devtools cannot change what a thing costs.

const CART_ID = "demo-cart";

// Motion exposes window.Motion. If the CDN is blocked, these fall back to
// no-ops so the shop still works — animation is decoration, not function.
// Note: motion@11 takes springs as options ({type:"spring"}), not as an
// easing function. Using the old easing:spring() form throws. See F-008.
const M = window.Motion || {};
const animate = M.animate || (() => ({ finished: Promise.resolve() }));
const stagger = M.stagger || (() => 0);

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

const rupees = (paise) => "₹" + (paise / 100).toFixed(2);

/* ---------- entrance ---------- */

function revealCards() {
  const cards = document.querySelectorAll(".card");
  animate(
    cards,
    { opacity: [0, 1], y: [18, 0], scale: [0.97, 1] },
    { delay: stagger(0.035), duration: 0.5, easing: [0.2, 0.8, 0.3, 1] }
  );
}

function revealHero() {
  animate(
    ".hero h1, .hero p",
    { opacity: [0, 1], y: [10, 0] },
    { delay: stagger(0.07), duration: 0.45 }
  );
}

/* ---------- images: fall back to the emoji if a photo fails ---------- */

function wireImageFallbacks() {
  document.querySelectorAll(".thumb img").forEach((img) => {
    img.addEventListener("error", () => {
      const div = document.createElement("div");
      div.className = "fallback";
      div.textContent = img.dataset.emoji || "\u{1F4E6}"; // text, never markup
      img.replaceWith(div);
    });
  });
}

/* ---------- cart ---------- */

function paintCart(cart) {
  const count = cart.lines.reduce((n, l) => n + l.quantity, 0);
  document.getElementById("cartCount").textContent = count;
  document.getElementById("cartTotal").textContent = rupees(cart.total_paise);

  // a spring bump, so the cart visibly reacts
  animate(
    "#cartPill",
    { scale: [1, 1.14, 1] },
    { type: "spring", stiffness: 380, damping: 14 }
  );
}

function flyToCart(card) {
  animate(
    card.querySelector(".thumb img") || card,
    { scale: [1, 0.92, 1] },
    { duration: 0.32 }
  );
}

document.querySelectorAll("button.add").forEach((btn) => {
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const card = btn.closest(".card");
    try {
      const cart = await api(`/api/cart/${CART_ID}/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sku: btn.dataset.sku, quantity: 1 }), // no price
      });
      flyToCart(card);
      paintCart(cart);
    } catch (err) {
      showBlocked([{ check: "Stock", why: err.message }]);
    } finally {
      btn.disabled = false;
    }
  });
});

/* ---------- the block screen: the moment that matters ---------- */

function showBlocked(reasons) {
  const list = document.getElementById("blockReasons");
  list.replaceChildren();

  for (const r of reasons) {
    const li = document.createElement("li");
    const b = document.createElement("b");
    b.textContent = r.check + ": ";        // safe text write, never raw markup
    li.append(b, document.createTextNode(r.why));
    list.appendChild(li);
  }

  const blocker = document.getElementById("blocker");
  blocker.hidden = false;

  animate(blocker, { opacity: [0, 1] }, { duration: 0.18 });
  animate(
    ".blockcard",
    { opacity: [0, 1], scale: [0.86, 1], y: [16, 0] },
    { type: "spring", stiffness: 300, damping: 18 }
  );
  animate(
    "#blockReasons li",
    { opacity: [0, 1], x: [-10, 0] },
    { delay: stagger(0.06, { start: 0.15 }), duration: 0.3 }
  );
}

document.getElementById("blockClose").addEventListener("click", async () => {
  await animate(
    ".blockcard",
    { opacity: [1, 0], scale: [1, 0.94] },
    { duration: 0.16 }
  ).finished;
  document.getElementById("blocker").hidden = true;
});

// Exposed so the demo (or a script) can trigger the block screen.
window.showBlocked = showBlocked;

/* ---------- go ---------- */

wireImageFallbacks();
revealHero();
revealCards();

// Safety net. Animation is decoration; content must never stay hidden because
// of it. If anything is still invisible shortly after load, force it visible.
setTimeout(() => {
  document.querySelectorAll(".card, .hero h1, .hero p").forEach((el) => {
    const s = getComputedStyle(el);
    if (s.opacity === "0" || s.transform === "matrix(0, 0, 0, 0, 0, 0)") {
      el.style.opacity = "1";
      el.style.transform = "none";
    }
  });
}, 1200);
api(`/api/cart/${CART_ID}`).then(paintCart).catch(() => {});
