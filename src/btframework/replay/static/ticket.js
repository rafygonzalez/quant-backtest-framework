let ticketExpanded = true;
let ticketPendingType = "LIMIT";
let ticketRefresh;
let ticketProfileKey = null;
let ticketHadPosition = false;

function clearTicketBracket() {
  if (typeof clearJournalForm === "function") clearJournalForm();
  for (const key of ["sl", "tp"]) $(key).value = "";
  Object.assign(tradeDraft, {mode: null, entry: null, sl: null, tp: null});
}
function bracketAmount(entry, price, quantity, contractSize, side) {
  if (![entry, price, quantity, contractSize].every(Number.isFinite) ||
      Math.min(entry, price, quantity, contractSize) <= 0) return null;
  return (side === "BUY" ? 1 : -1) * (price - entry) * quantity * contractSize;
}

function ticketEntry() {
  return $("order_type").value === "MARKET"
    ? Number(state?.market_price)
    : Number($("price").value);
}
function syncTicketDraft() {
  const preview = $("order_type").value !== "MARKET" || $("sl").value || $("tp").value;
  tradeDraft.entry = preview && ticketEntry() > 0 ? planPrice(ticketEntry()) : null;
  for (const key of ["sl", "tp"])
    tradeDraft[key] = $(key).value === "" ? null : Number($(key).value);
  updatePlanStatus();
}
function updateBrokerTicket() {
  if (!state?.ready) return;
  const live = state.position || state.intent;
  const profileKey = JSON.stringify([state.profile.symbol, state.profile.kind, state.profile.tick_size]);
  if (ticketProfileKey !== profileKey || ticketHadPosition && !live) clearTicketBracket();
  ticketProfileKey = profileKey;
  ticketHadPosition = !!live;
  if (!live)
    for (const key of ["sl", "tp"])
      tradeDraft[key] = $(key).value === "" ? null : Number($(key).value);
  const market = $("order_type").value === "MARKET";
  const buy = $("side").value === "BUY";
  $("brokerTicket").hidden = !!live || !ticketExpanded;
  $("positionCard").hidden = !live;
  $("openTicket").disabled = !!live;
  $("pendingTypeRow").hidden = market;
  $("entryPriceRow").hidden = market;
  $("price").required = !market;
  $("volumeUnit").textContent = t(state.profile.kind === "futures" ? "contracts" : "lots");
  $("quantity").min = state.profile.minimum;
  $("quantity").step = state.profile.step;
  for (const key of ["price", "tp", "sl"]) $(key).step = state.profile.tick_size;
  const quote = Number(state.market_price), halfSpread = Number(state.profile.spread) / 2;
  if (!live && market && ($("sl").value || $("tp").value)) tradeDraft.entry = planPrice(quote);
  const quoteFormat = value => value.toLocaleString(lang, {minimumFractionDigits:pricePrecision(),maximumFractionDigits:pricePrecision()});
  $("sellQuote").textContent = quote > 0 ? quoteFormat(quote - halfSpread) : "—";
  $("buyQuote").textContent = quote > 0 ? quoteFormat(quote + halfSpread) : "—";
  $("spreadQuote").textContent = t("simulatedQuote") + " · " + t("spread") + " " + fmt(state.profile.spread);
  document.querySelectorAll("[data-side]").forEach(button => {
    const selected = button.dataset.side === $("side").value;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  document.querySelectorAll("[data-order-mode]").forEach(button => {
    const selected = (button.dataset.orderMode === "market") === market;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  const quantity = Number($("quantity").value), entry = ticketEntry();
  const margin = state.profile.kind === "futures"
    ? quantity * Number(state.profile.margin_per_contract)
    : quantity * entry * Number(state.profile.contract_size) / Number(state.profile.leverage);
  for (const key of ["tp", "sl"]) {
    const output = $(key + "Amount");
    const amount = $(key).value === "" ? null : bracketAmount(entry, Number($(key).value), quantity, Number(state.profile.contract_size), $("side").value);
    const valid = amount != null && (key === "tp" ? amount > 0 : amount < 0);
    output.textContent = amount == null ? "" : valid
      ? new Intl.NumberFormat(lang, {style:"currency", currency:state.profile.currency, signDisplay:"always"}).format(amount)
      : t("checkLevel");
    output.className = "bracket-amount " + (valid ? key === "tp" ? "positive" : "negative" : "");
    output.title = t("grossBracketEstimate");
  }
  const direction = buy ? 1 : -1, risk = direction * (entry - Number($("sl").value));
  const reward = direction * (Number($("tp").value) - entry);
  const estimate = [t("estimatedMargin") + " " + fmt(entry > 0 || state.profile.kind === "futures" ? margin : null) + " " + state.profile.currency];
  if ($("sl").value && $("tp").value && risk > 0 && reward > 0)
    estimate.push(t("risk") + " " + fmt(risk * quantity * Number(state.profile.contract_size)) + " " + state.profile.currency + " · RR " + fmt(reward / risk));
  $("ticketEstimate").textContent = estimate.join("\n");
  $("submitOrder").textContent = t(placingOrder ? "sendingOrder" : market ? buy ? "confirmBuyMarket" : "confirmSellMarket" : buy ? "confirmBuyPending" : "confirmSellPending");
  $("submitOrder").classList.toggle("sell", !buy);
  $("submitOrder").disabled = placingOrder || !!live || state.cursor === 0 || state.cursor >= state.total || chartBrowseDate != null;
}
function initBrokerTicket() {
  $("order_type").value = "MARKET";
  $("accountDetails").onclick = () => $("account").classList.toggle("expanded");
  $("openTicket").onclick = () => {
    clearTicketBracket();
    ticketExpanded = true;
    updateBrokerTicket();
    if (state?.ready) render();
    $("quantity").focus();
  };
  $("hideTicket").onclick = () => {
    ticketExpanded = false;
    tradeDraft.mode = null;
    updateBrokerTicket();
    if (state?.ready) render();
    $("openTicket").focus();
  };
  document.querySelectorAll("[data-side]").forEach(button => button.onclick = () => {
    $("side").value = button.dataset.side;
    syncTicketDraft();
    render();
  });
  document.querySelectorAll("[data-order-mode]").forEach(button => button.onclick = () => {
    $("order_type").value = button.dataset.orderMode === "market" ? "MARKET" : ticketPendingType;
    tradeDraft.mode = null;
    syncTicketDraft();
    render();
  });
  $("pendingType").onchange = () => {
    ticketPendingType = $("pendingType").value;
    $("order_type").value = ticketPendingType;
    syncTicketDraft();
    render();
  };
  document.querySelectorAll("[data-step-field]").forEach(button => button.onclick = () => {
    const key = button.dataset.stepField, field = $(key);
    const increment = Number(key === "quantity" ? state.profile.step : state.profile.tick_size);
    const minimum = Number(key === "quantity" ? state.profile.minimum : state.profile.tick_size);
    const previous = field.value === "" ? Number(state.market_price) : Number(field.value);
    const value = Math.max(minimum, previous + Number(button.dataset.stepDirection) * increment);
    field.value = key === "quantity" ? Number(value.toFixed(8)) : planPrice(value);
    syncTicketDraft();
    render();
  });
  for (const key of ["price", "quantity", "sl", "tp"])
    $(key).oninput = () => {
      syncTicketDraft();
      clearTimeout(ticketRefresh);
      ticketRefresh = setTimeout(() => { if (state?.ready) render(); }, 160);
    };
}

function engineLimitBlock(model) {
  if (chartBrowseDate != null) return "historicalView";
  if (state?.position || state?.intent || placingOrder) return "occupied";
  if (!state?.cursor || state.cursor >= state.total) return "no_executable_bar";
  const p = model?.proposal;
  const age = p ? Date.parse(state.now + "Z") - Date.parse(p.known_at + "Z") : NaN;
  if (model?.decision !== "propose" || !p || !Number.isFinite(age) || age < 0 || age >= 300000)
    return "engineLimitExpired";
  const direction = model.direction;
  if (![1,-1].includes(direction) || ![p.entry,p.sl,p.tp_estimate].every(value => Number.isFinite(value) && value > 0) ||
      direction * (p.entry-p.sl) <= 0 || direction * (p.tp_estimate-p.entry) <= 0)
    return "invalid_bracket";
  if (direction * (p.entry-Number(state.market_price)) >= 0) return "invalid_limit";
  return null;
}
async function loadEngineLimit() {
  halt();
  await act("pause");
  const model = state.visuals?.find(view => view?.entry_assessment)?.entry_assessment;
  const block = engineLimitBlock(model);
  if (block) return error(Error(block));
  const p = model.proposal;
  ticketExpanded = true;
  ticketPendingType = "LIMIT";
  $("side").value = model.direction > 0 ? "BUY" : "SELL";
  $("order_type").value = $("pendingType").value = "LIMIT";
  $("price").value = p.entry;
  $("sl").value = p.sl;
  $("tp").value = p.tp_estimate;
  const event = state.events.find(event => event.kind === "confirmation" && event.direction === model.direction &&
    event.known_at === p.known_at && event.event_id.startsWith(model.episode_id + ":"));
  $("event_id").value = event && [...$("event_id").options].some(option => option.value === event.event_id) ? event.event_id : "manual";
  tradeDraft.mode = null;
  syncTicketDraft();
  await render();
  $("quantity").focus();
}
