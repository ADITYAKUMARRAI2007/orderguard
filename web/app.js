const state = { sessionId: null, session: null, currentItem: 0, searching: false };
const $ = (selector) => document.querySelector(selector);
const conversation = $("#conversation");

function money(paise, currency = "INR") {
  if (typeof paise !== "number") return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency }).format(paise / 100);
}

function addMessage(text, who = "assistant") {
  const el = document.createElement("div");
  if (who === "user") {
    el.className = "user-message";
    const bubble = document.createElement("div");
    bubble.className = "user-bubble";
    bubble.textContent = text;
    el.append(bubble);
  } else {
    el.className = "assistant-message";
    el.innerHTML = '<div class="message-avatar">◎</div><div><div class="message-label">OrderGuard <span>now</span></div><p></p></div>';
    el.querySelector("p").textContent = text;
  }
  conversation.append(el);
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function activity(text, status = "Working") {
  $("#activityStatus").textContent = status;
  const log = $("#activityLog");
  log.replaceChildren();
  const time = document.createElement("span"); time.className = "log-time"; time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const message = document.createElement("span"); message.textContent = text;
  log.append(time, message);
}

function setStep(name) {
  const order = ["understand", "clarify", "research", "review", "payment"];
  const index = order.indexOf(name);
  document.querySelectorAll(".step").forEach((step, i) => {
    step.classList.toggle("active", i === index);
    step.classList.toggle("done", i < index);
  });
}

function renderPlan(session, title = "Your order plan") {
  state.session = session;
  const intent = session.intent;
  $("#planTitle").textContent = title;
  $("#planNumber").textContent = intent ? `${intent.items.length}` : "—";
  const card = $("#planCard"); card.replaceChildren();
  if (!intent) {
    const q = document.createElement("div"); q.className = "question-card";
    q.textContent = session.clarifications?.join(" ") || "I need a little more information before I can make a safe plan.";
    card.append(q); return;
  }
  const summary = document.createElement("div"); summary.className = "plan-summary";
  const rows = [
    ["Store", intent.merchant],
    ["Items", intent.items.map((i) => `${i.quantity} × ${i.requested_product}`).join(", ")],
    ["Spending limit", money(intent.maximum_total_paise, intent.currency)],
  ];
  rows.forEach(([label, value]) => { const row = document.createElement("div"); row.className = "summary-row"; const a = document.createElement("span"); a.textContent = label; const b = document.createElement("strong"); b.textContent = value; row.append(a, b); summary.append(row); });
  card.append(summary);
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  let body = null; try { body = await response.json(); } catch (_) { /* empty response */ }
  if (!response.ok) throw new Error(body?.detail || `Request failed (${response.status})`);
  return body;
}

async function startRequest(text) {
  addMessage(text, "user");
  activity("Writing down your request", "Understanding"); setStep("understand");
  $("#planTitle").textContent = "Understanding request";
  try {
    const session = await api("/api/sessions", { method: "POST", body: JSON.stringify({ user_id: "local-user", request_text: text }) });
    state.sessionId = session.session_id; renderPlan(session);
    if (!session.intent) {
      setStep("clarify"); activity("Waiting for one detail from you", "Needs your answer");
      addMessage(session.clarifications.join(" ") || "Could you tell me a little more?");
      return;
    }
    addMessage(`I understood this as ${session.intent.items.map((i) => `${i.quantity} × ${i.requested_product}`).join(", ")} from ${session.intent.merchant}. I’ll now check what is actually available.`);
    await searchNextItem();
  } catch (error) { activity(error.message, "Stopped safely"); addMessage(`I stopped before changing anything: ${error.message}`); }
}

async function continueRequest(text) {
  addMessage(text, "user"); activity("Updating the plan with your answer", "Understanding"); setStep("understand");
  try {
    const session = await api(`/api/sessions/${state.sessionId}/messages`, { method: "POST", body: JSON.stringify({ message: text }) });
    renderPlan(session);
    if (!session.intent) { setStep("clarify"); activity("Waiting for another detail", "Needs your answer"); addMessage(session.clarifications.join(" ")); return; }
    addMessage("Thanks — the plan is complete. I’ll research the available options now."); await searchNextItem();
  } catch (error) { activity(error.message, "Stopped safely"); addMessage(`I could not continue safely: ${error.message}`); }
}

async function searchNextItem() {
  const intent = state.session.intent;
  if (state.currentItem >= intent.items.length) { renderOffers(); return; }
  state.searching = true; setStep("research"); activity(`Checking stores for ${intent.items[state.currentItem].requested_product}`, "Researching");
  try {
    const outcome = await api(`/api/sessions/${state.sessionId}/items/${state.currentItem}/search`, { method: "POST" });
    state.session.offers_by_item = state.session.offers_by_item || {}; state.session.offers_by_item[state.currentItem] = outcome;
    state.currentItem += 1;
    if (state.currentItem < intent.items.length) await searchNextItem(); else renderOffers();
  } catch (error) { activity(error.message, "One store failed"); addMessage(`I could not complete the search: ${error.message}`); }
  finally { state.searching = false; }
}

function renderOffers() {
  setStep("research"); activity("Options are ready for your review", "Waiting for you");
  const card = $("#planCard"); card.replaceChildren();
  const wrap = document.createElement("div"); wrap.className = "offer-list";
  Object.entries(state.session.offers_by_item || {}).forEach(([itemIndex, outcome]) => {
    outcome.offers.slice(0, 5).forEach((scored) => {
      const offer = scored.offer; const row = document.createElement("div"); row.className = "offer";
      if (offer.image) { const img = document.createElement("img"); img.src = offer.image; img.alt = ""; row.append(img); }
      const info = document.createElement("div"); info.className = "offer-info"; const title = document.createElement("div"); title.className = "offer-title"; title.textContent = `${offer.title}${offer.variant_title ? ` · ${offer.variant_title}` : ""}`; const meta = document.createElement("div"); meta.className = "offer-meta"; meta.textContent = `${offer.store_label || offer.store} · ${scored.in_stock ? "In stock" : "Unavailable"}`; info.append(title, meta);
      const price = document.createElement("div"); price.className = "offer-price"; price.textContent = money(scored.line_total_minor, offer.currency);
      const choose = document.createElement("button"); choose.className = "choose"; choose.textContent = "Choose"; choose.disabled = !scored.in_stock; choose.onclick = () => selectOffer(Number(itemIndex), offer, row);
      row.append(info, price, choose); wrap.append(row);
    });
  });
  if (!wrap.children.length) { const empty = document.createElement("div"); empty.className = "question-card"; empty.textContent = "No usable options were returned. I have not changed a cart."; card.append(empty); return; }
  card.append(wrap); addMessage("I found these options. I won’t choose between different products for you — choose the one you want, and I’ll verify the cart afterward.");
}

async function selectOffer(itemIndex, offer, row) {
  row.querySelector("button").disabled = true; activity(`Adding ${offer.title} to the cart`, "Adding to cart"); setStep("review");
  try {
    const key = `${offer.store}|${offer.variant_id}`;
    const session = await api(`/api/sessions/${state.sessionId}/items/${itemIndex}/select`, { method: "POST", body: JSON.stringify({ offer_key: key, explicit_user_selection: true }) });
    state.session = session; row.style.borderColor = "#76b890"; renderPlan(session, "Cart selected — checking it");
    activity("Read the cart back independently", "Verifying");
    addMessage("The item was added. I’m reading the cart again from the store now, instead of trusting my own add request.");
    const allSelected = Object.keys(session.selected_by_item || {}).length === session.intent.items.length;
    if (allSelected) showConfirmation(); else { activity("Waiting for the other item choice", "Needs your choice"); renderOffers(); }
  } catch (error) { row.querySelector("button").disabled = false; activity(error.message, "Stopped safely"); addMessage(`I stopped before confirming the cart: ${error.message}`); }
}

async function showConfirmation() {
  setStep("review"); activity("Cart matches the selected plan", "Waiting for approval");
  try {
    const confirmation = await api(`/api/sessions/${state.sessionId}/confirm`, { method: "POST" });
    const card = $("#planCard"); card.replaceChildren();
    const summary = document.createElement("div"); summary.className = "plan-summary";
    const ok = document.createElement("div"); ok.className = "question-card"; ok.style.borderLeftColor = "#53a775"; ok.style.background = "#eff9f0"; ok.style.color = "#39704d"; ok.textContent = confirmation.intent ? "Cart verified. Nothing has been paid yet." : "Cart changed or could not be verified."; summary.append(ok);
    if (confirmation.comparison) { const r = document.createElement("div"); r.className = "summary-row summary-total"; const a = document.createElement("span"); a.textContent = "Verified total"; const b = document.createElement("strong"); b.textContent = money(state.session.observed_cart?.total_paise, state.session.intent.currency); r.append(a, b); summary.append(r); }
    if (confirmation.intent) { const approve = document.createElement("button"); approve.className = "choose"; approve.style.width = "100%"; approve.style.padding = "11px"; approve.textContent = "Continue to payment approval"; approve.onclick = () => { setStep("payment"); activity("Payment is not connected yet", "Waiting"); addMessage("The cart is verified. Payment is the next guarded step, but this build has not connected a real payment action yet, so I stopped safely."); }; summary.append(approve); }
    card.append(summary); addMessage(confirmation.intent ? "Everything matches your approved request. Please review the total below." : "I found a difference in the cart, so I did not continue.");
  } catch (error) { activity(error.message, "Stopped safely"); addMessage(`The cart could not be confirmed: ${error.message}`); }
}

$("#composer").addEventListener("submit", (event) => { event.preventDefault(); const input = $("#requestInput"); const text = input.value.trim(); if (!text) return; input.value = ""; if (state.sessionId && state.session?.intent === null) continueRequest(text); else startRequest(text); });
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => { $("#requestInput").value = button.dataset.prompt; $("#requestInput").focus(); }));
$("#voiceButton").addEventListener("click", () => { addMessage("Voice input will use the same written plan and safety checks. The microphone connector is the next UI integration."); activity("Voice input is not connected yet", "Waiting"); });
