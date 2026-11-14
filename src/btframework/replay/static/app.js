const $ = (id) => document.getElementById(id);

let chartBrowseDate = null;
let placingOrder = false;
let registeredStrategies = ["manual"];
let eventSignature = "";
const chartViews = [{ follow: true }, { follow: true }];
let lang = localStorage.getItem("replay-lang") || "es",
  state = null,
  timer = null,
  queue = Promise.resolve(),
  generation = 0;
const t = (k) => messages[lang][k] || k;
const persistenceLabel = (value) => t(value === "saved" ? "persistenceSaved" : value === "off" ? "persistenceOff" : "persistenceUnknown");
function renderPersistence(value) {
  const target = $("persistenceStatus");
  target.textContent = persistenceLabel(value);
  target.className = value === "saved" ? "persistence-saved" : "persistence-unconfirmed";
}
const fmt = (v) =>
  v == null
    ? "—"
    : Number(v).toLocaleString(lang, { maximumFractionDigits: 2 });
function translate() {
  document.querySelectorAll("[data-placeholder]").forEach(e => e.placeholder = t(e.dataset.placeholder));
  document.querySelectorAll("[data-aria]").forEach(e => e.setAttribute("aria-label", t(e.dataset.aria)));
  document.documentElement.lang = lang;
  $("language").value = lang;
  document
    .querySelectorAll("[data-t]")
    .forEach((e) => (e.textContent = t(e.dataset.t)));
}
function error(e) {
  $("error").textContent = t(
    e.message === "Insufficient margin" ? "insufficientMargin" : e.message,
  );
  $("error").hidden = false;
  if (!["persistence_failed", "persistence_blocked"].includes(e.message))
    setTimeout(() => ($("error").hidden = true), 10000);
}
async function api(action, args = {}) {
  const response = await fetch("/api", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Replay-Token": document.querySelector("meta[name=replay-token]")
        .content,
    },
    body: JSON.stringify({
      action,
      args: {
        left: Number($("left").value),
        right: Number($("right").value),
        view_before: chartBrowseDate,
        compact: !$("scoreDetails").open,
        history_limit: Math.max(historyLimit, Number($("visibleBars").value) + 100),
        ...(state?.revision != null && !["state", "report", "save", "journal", "journal_autofill"].includes(action)
          ? { expected_revision: state.revision }
          : {}),
        ...args,
      },
    }),
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error);
  return data;
}
function act(action, args = {}) {
  const task = async () => {
    try {
      state = await api(action, args);
      await render();
      return true;
    } catch (e) {
      halt();
      error(e);
      try {
        state = await api("state");
        await render();
      } catch {}
      return false;
    }
  };
  queue = queue.then(task, task);
  return queue;
}
function halt() {
  generation++;
  clearTimeout(timer);
  timer = null;
}
async function pulse(token = generation) {
  if (token !== generation || !state?.playing) return;
  await act("advance", { steps: 1 });
  if (token === generation && state?.playing)
    timer = setTimeout(() => pulse(token), Number($("speed").value));
}
function row(label, value) {
  const d = document.createElement("div");
  d.className = "row";
  const a = document.createElement("span"),
    b = document.createElement("strong");
  a.textContent = t(label);
  b.textContent = value;
  d.append(a, b);
  return d;
}
function table(target, items, keys) {
  const table = document.createElement("table"),
    head = table.createTHead().insertRow();
  keys.forEach((k) => {
    const th = document.createElement("th");
    th.textContent = t(k);
    head.append(th);
  });
  const body = table.createTBody();
  items.forEach((item) => {
    const tr = body.insertRow();
    keys.forEach((k) => {
      const cell = tr.insertCell();
      cell.textContent =
        k === "event"
          ? t(item[k])
          : typeof item[k] === "number"
            ? fmt(item[k])
            : (item[k] ?? "—");
      if (["pnl", "r", "net"].includes(k) && item[k] != null)
        cell.className = item[k] >= 0 ? "positive" : "negative";
    });
  });
  $(target).replaceChildren(table);
}
function pricePrecision() {
  const tick = String(state.profile.tick_size);
  return Math.min(8, (tick.split(".")[1] || "").length);
}
function fitPrices(rows, range) {
  const visible = rows.filter(
    (row) =>
      row.index >= Math.floor(range[0]) && row.index <= Math.ceil(range[1]),
  );
  const values = visible.flatMap((row) =>
    [row.low, row.high, row.ma9, row.ma20].filter((value) => value != null),
  );
  if (!values.length) return undefined;
  const low = Math.min(...values),
    high = Math.max(...values);
  const padding = Math.max(
    (high - low) * 0.12,
    Number(state.profile.tick_size) * 4,
  );
  return [low - padding, high + padding];
}
async function chart(index, rows) {
  if (activeHandleDrag) return;
  const target = $("chart" + index),
    position = state.position || state.intent;
  const tf = Number($(index ? "right" : "left").value);
  const view = chartViews[index];
  const identity = state.profile.symbol + tf + state.replay_start;
  if (view.identity !== identity) {
    view.follow = true;
    view.range = null;
    view.identity = identity;
  }
  view.rows = rows;
  const signature = JSON.stringify([identity, state.cursor, rows.length, rows[0]?.time, rows.at(-1), state.position, state.intent, tradeDraft, ticketExpanded, view.follow, view.range, view.yRange, $("visibleBars").value, ["showPine","showMA","showRanges","showPastRanges","showROC","showOBV","showDivergences","showFormingDivergences","showDivergenceLines"].map(toolChecked), $("obvMode").value, lang, state.context_source]);
  if (target._chartSignature === signature) { bindChartTools(index); return; }
  target._chartSignature = signature;
  const count = Number($("visibleBars").value);
  const range =
    view.follow || !view.range
      ? [
          Math.max(-0.8, (rows.at(-1)?.index ?? -1) - count + 0.2),
          (rows.at(-1)?.index ?? -1) + 4,
        ]
      : view.range;
  const times = rows.map((row) => new Date(row.time * 1000).toISOString());
  const indices = rows.map((row) => row.index);
  const hover = rows.map(
    (row, i) =>
      times[i].replace("T", " ").slice(0, 16) +
      " UTC<br>O " +
      row.open +
      " · H " +
      row.high +
      " · L " +
      row.low +
      " · C " +
      row.close,
  );
  const traces = [
    {
      type: "candlestick",
      x: indices,
      open: rows.map((r) => r.open),
      high: rows.map((r) => r.high),
      low: rows.map((r) => r.low),
      close: rows.map((r) => r.close),
      increasing: {
        line: { color: "#2dd4bf", width: 1.2 },
        fillcolor: "#2dd4bf",
      },
      decreasing: {
        line: { color: "#fb7185", width: 1.2 },
        fillcolor: "#fb7185",
      },
      whiskerwidth: 0.25,
      hovertext: hover,
      hoverinfo: "text",
      name: state.profile.symbol,
      showlegend: false,
    },
  ];
  [9, 20].forEach((period, i) =>
    traces.push({
      type: "scatter",
      mode: "lines",
      x: indices,
      y: rows.map((r) => r["ma" + period]),
      line: { width: 1.8, color: i ? "#a78bfa" : "#fbbf24" },
      name: t("ma") + " " + period,
      hoverinfo: "y+name",
      connectgaps: false,
    }),
  );
  const byTime = new Map(rows.map((row) => [row.time, row.index]));
  const markers = state.markers
    .filter((e) => e.price != null)
    .map((event) => {
      const stamp = Date.parse(event.known_at + "Z") / 1000 - 1;
      return {
        ...event,
        x: byTime.get(Math.floor(stamp / (tf * 60)) * tf * 60),
      };
    })
    .filter((event) => event.x != null);
  traces.push({
    type: "scatter",
    mode: "markers",
    x: markers.map((e) => e.x),
    y: markers.map((e) => e.price),
    text: markers.map((e) => t(e.kind)),
    hoverinfo: "text",
    showlegend: false,
    marker: {
      symbol: markers.map((e) =>
        e.kind.includes("cross")
          ? "x"
          : e.kind === "retest"
            ? "circle-open"
            : "diamond",
      ),
      size: markers.map((e) => (e.kind.includes("cross") ? 9 : 7)),
      color: markers.map((e) =>
        e.provisional ? "#fbbf24" : e.direction > 0 ? "#2dd4bf" : "#fb7185",
      ),
    },
    name: t("event"),
  });
  const last = rows.at(-1);
  $("quote" + index).textContent = last
    ? last.close.toLocaleString(lang, {
        minimumFractionDigits: pricePrecision(),
        maximumFractionDigits: pricePrecision(),
      }) +
      " · " +
      t(last.complete ? "closedCandle" : "formingCandle")
    : t("noCandles");
  const historical = rows.filter((row) => row.complete).length;
  $("history" + index).textContent =
    t("historyContext") +
    ": " +
    historical +
    " · " +
    t(last?.ma20 == null ? "maWarming" : "maReady");
  const shapes = position
    ? [
        "sl",
        "tp",
        ...(state.position ? ["entry"] : position.order_type !== "MARKET" ? ["price"] : []),
      ].filter(key => position[key] != null).map((key, i) => ({
        type: "line",
        xref: "paper",
        x0: 0,
        x1: 1,
        y0: position[key],
        y1: position[key],
        line: {
          color: key === "sl" ? "#f27c8e" : key === "tp" ? "#4cdec0" : "#f6c76d",
          width: 1.5,
          dash: "dash",
        },
        editable: false,
      }))
    : [];
  const tickStep = Math.max(1, Math.ceil(count / 7));
  const tickvals = indices.filter((i) => i % tickStep === 0);
  const annotations = [];
  const layout = {
    paper_bgcolor: "#0d1521",
    plot_bgcolor: "#0d1521",
    font: { color: "#8ea3bc", size: 11 },
    margin: { l: 8, r: 104, t: 27, b: 28 },
    showlegend: true,
    legend: { orientation: "h", x: 0, y: 1.08, font: { size: 11 } },
    dragmode: "pan",
    hovermode: "closest",
    autosize: true,
    uirevision: identity,
    separators: lang === "es" ? ",." : ".,",
    xaxis: {
      range,
      rangeslider: { visible: false },
      gridcolor: "#192639",
      zeroline: false,
      type: "linear",
      tickvals,
      ticktext: tickvals.map((i) =>
        new Date(
          rows.find((row) => row.index === i).time * 1000,
        ).toLocaleString(lang, {
          ...(tf * count >= 1440 ? { month: "short", day: "2-digit" } : {}),
          timeZone: "UTC",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }),
      ),
      showspikes: true,
      spikemode: "across",
      spikesnap: "cursor",
      spikecolor: "#60758e",
      spikethickness: 1,
    },
    yaxis: {
      range: !view.follow && view.yRange ? view.yRange : fitPrices(rows, range),
      side: "right",
      gridcolor: "#192639",
      zeroline: false,
      fixedrange: false,
      tickformat: ",." + pricePrecision() + "f",
      showspikes: true,
      spikesnap: "cursor",
      spikecolor: "#60758e",
      spikethickness: 1,
    },
    shapes,
    annotations,
  };
  enhanceChart(index, rows, traces, layout);
  target._rendering = true;
  await Plotly.react(target, traces, layout, {
    responsive: true,
    displaylogo: false,
    doubleClick: "reset",
    scrollZoom: true,
    editable: false,
    edits: { shapePosition: false, annotationPosition: false },
    modeBarButtonsToRemove: ["select2d", "lasso2d"],
  });
  target._rendering = false;
  bindChartTools(index);
  if (!target._replayBound) {
    target.on("plotly_relayout", (change) => {
      if (target._rendering) return;
      const current = chartViews[index];
      if (change["yaxis.range[0]"] != null && change["xaxis.range[0]"] == null) current.yRange = [change["yaxis.range[0]"], change["yaxis.range[1]"]];
      if (
        change["xaxis.range[0]"] != null &&
        change["xaxis.range[1]"] != null
      ) {
        current.follow = false;
        current.range = [
          Number(change["xaxis.range[0]"]),
          Number(change["xaxis.range[1]"]),
        ];
        current.yRange = null;
        const fit = fitPrices(current.rows, current.range);
        if (fit) {
          current.yRange = fit;
          chart(index, current.rows);
        }
      } else if (change["xaxis.autorange"]) {
        current.follow = true;
        chart(index, current.rows);
      }
    });
    target._replayBound = true;
  }
}
async function render() {
  if (!state?.ready) {
    if (state) renderPersistence(state.persistence);
    return;
  }
  $("workspace").hidden = false;
  ["left", "right"].forEach((k) => {
    [...$(k).options].forEach(
      (o) => (o.disabled = Number(o.value) < state.base_minutes),
    );
    if (Number($(k).value) < state.base_minutes)
      $(k).value = String(state.base_minutes);
  });
  $("clock").textContent = state.profile.symbol + " · " + state.now + " UTC";
  renderPersistence(state.persistence);
  $("status").textContent = chartBrowseDate != null ? t("historicalView") : t(state.reason);
  $("next").disabled = !state.available_events.length;
  $("progress").value = (state.cursor / state.total) * 100;
  document
    .querySelectorAll(".symbolLabel")
    .forEach((e) => (e.textContent = state.profile.symbol));
  $("account").replaceChildren(
    ...["balance", "equity", "margin_used", "free_margin"].map((k) =>
      row(k, fmt(state.account[k])),
    ),
  );
  const metrics = [
    "net",
    "trades",
    "win_rate",
    "profit_factor",
    "expectancy_r",
    "drawdown",
    "total_r",
    "wins",
    "losses",
    "avg_win",
    "avg_loss",
    "commission",
  ];
  $("metrics").replaceChildren(
    ...metrics.map((k) => {
      const d = document.createElement("div");
      d.className = "metric";
      const s = document.createElement("span"),
        v = document.createElement("strong");
      s.textContent = t(k === "commission" ? "totalCommission" : k);
      v.textContent =
        k === "win_rate"
          ? state.scoreboard[k] == null
            ? "—"
            : fmt(state.scoreboard[k] * 100) + "%"
          : fmt(state.scoreboard[k]);
      d.append(s, v);
      return d;
    }),
  );
  $("events").replaceChildren(
    ...state.available_events.map((kind) => {
      const l = document.createElement("label"),
        c = document.createElement("input"),
        s = document.createElement("span");
      c.type = "checkbox";
      c.checked = state.selected.includes(kind);
      s.textContent = t(kind);
      c.onchange = () =>
        act("select", {
          events: [...$("events").querySelectorAll("input:checked")].map(
            (i) => i.value,
          ),
        });
      c.value = kind;
      l.append(c, s);
      return l;
    }),
  );
  $("eventNow").textContent = state.events
    .map((e) => t(e.kind) + " · " + (e.direction > 0 ? t("buy") : t("sell")))
    .join(" / ");
  const signature = state.events.map((e) => e.event_id).join("|");
  const previousEvent =
    signature === eventSignature ? $("event_id").value : null;
  eventSignature = signature;
  $("event_id").replaceChildren(
    ...[
      ...state.events.map((e) => ({ value: e.event_id, label: t(e.kind) })),
      { value: "manual", label: t("manual") },
    ].map((item) => {
      const option = document.createElement("option");
      option.value = item.value;
      option.textContent = item.label;
      return option;
    }),
  );
  if ([...$("event_id").options].some((o) => o.value === previousEvent))
    $("event_id").value = previousEvent;
  $("position").textContent = state.position
    ? (state.position.direction > 0 ? t("buy") : t("sell")) +
      " " +
      fmt(state.position.quantity) +
      " @ " +
      fmt(state.position.entry)
    : t("none");
  const managed = state.position || state.intent;
  for (const [id, key] of [["positionSL", "sl"], ["positionTP", "tp"], ["manageEntry", "price"]]) {
    if (document.activeElement !== $(id)) $(id).value = (id === "manageEntry" && state.position ? state.position.entry : managed?.[key]) ?? "";
    $(id).disabled = !managed || (id === "manageEntry" && !!state.position);
  }
  $("activatePending").hidden = !state.intent || !!state.position;
  $("close").hidden = !state.position;
  $("positionCard").classList.toggle("empty-position", !managed);
  $("orderState").textContent = state.position ? t("openPosition") : state.intent ? t("waitingFill") : t("none");
  $("cancelVisible").disabled = !state.intent || !!state.position;
  $("modify").disabled = !managed;
  $("liveScore").replaceChildren(...["net", "trades", "win_rate"].map(k => row(k, k === "win_rate" ? (state.scoreboard[k] == null ? "—" : fmt(state.scoreboard[k] * 100) + "%") : fmt(state.scoreboard[k]))), row("floatingPnl", fmt(Number(state.account.equity) - Number(state.account.balance))));
  const liveValues = [state.scoreboard.net, null, null, Number(state.account.equity) - Number(state.account.balance)];
  $("liveScore").querySelectorAll(".row strong").forEach((node, i) => { node.className = liveValues[i] > 0 ? "positive" : liveValues[i] < 0 ? "negative" : ""; });
  $("coverage").textContent = state.coverage ? t("observedGaps") + ": " + state.coverage.gaps + " · " + t("lastJump") + ": " + state.coverage.last_jump_minutes + "m" : "";
  if (document.activeElement !== $("navigateDate") && chartBrowseDate == null) $("navigateDate").value = state.now.slice(0, 16);
  if (state.source_bounds?.length) { $("navigateDate").min = state.source_bounds[0].slice(0,16); $("navigateDate").max = state.source_bounds[1].slice(0,16); }
  const chosenCheckpoint = $("checkpoints").value;
  $("checkpoints").replaceChildren(...(state.checkpoints || []).map(item => { const o = document.createElement("option"); o.value = item.id; o.textContent = item.label; return o; }));
  if ([...$("checkpoints").options].some(o => o.value === chosenCheckpoint)) $("checkpoints").value = chosenCheckpoint;
  $("resumeCheckpoint").disabled = !$("checkpoints").options.length;
  ["be", "trail", "close"].forEach(
    (k) => ($(k).disabled = !state.position),
  );

  document
    .querySelectorAll("#ticket input,#ticket select,#ticket button")
    .forEach((e) => (e.disabled = !!state.position || !!state.pending.length));
  if ($("scoreDetails").open) {
  table(
    "breakdown",
    Object.entries(state.scoreboard.by_event).map(([event, m]) => ({
      event,
      ...m,
    })),
    ["event", "trades", "net", "expectancy_r", "total_r"],
  );
  if (state.scoreboard.without_initial_risk) $("breakdown").append(row("withoutInitialRisk", fmt(state.scoreboard.without_initial_risk)));
  const decisions = Object.entries(state.scoreboard.opportunities);
  decisions.forEach(([k, v]) => $("breakdown").append(row(k, fmt(v))));
  if (state.reason === "end" && (state.position || state.pending.length))
    $("breakdown").append(row("unresolved", ""));
  table(
    "trades",
    state.trades
      .slice()
      .reverse()
      .map((item) => ({ ...item, event: item.event_kind })),
    [
      "opened_at",
      "closed_at",
      "event",
      "quantity",
      "entry",
      "exit",
      "pnl",
      "r",
    ].map((k) => k),
  );
  }
  updateBrokerTicket();
  renderEntryAssessment();
  renderJournalStatus();
  syncChartFullscreen();
  await Promise.all(state.charts.map((r, i) => chart(i, r)));
  if ($("scoreDetails").open) {
  const eq = state.equity_curve || [];
  await Plotly.react(
    "equity",
    [
      {
        x: eq.map((r) => r[0]),
        y: eq.map((r) => r[1]),
        type: "scatter",
        mode: "lines",
        line: { color: "#4cdec0", width: 1.5 },
        fill: "tozeroy",
        fillcolor: "rgba(76,222,192,.06)",
      },
    ],
    {
      paper_bgcolor: "#111a27",
      plot_bgcolor: "#111a27",
      font: { color: "#91a3bb", size: 10 },
      margin: { l: 60, r: 15, t: 15, b: 25 },
      xaxis: { gridcolor: "#1d2a3b" },
      yaxis: { gridcolor: "#1d2a3b" },
    },
    { responsive: true, displayModeBar: false },
  );
  }
}
$("create").onclick = async () => {
  halt();
  if (state?.ready && !confirm(t("confirmReset"))) return;
  const profile = {
    symbol: $("symbol").value.trim(),
    kind: $("kind").value,
    currency: $("currency").value.trim().toUpperCase(),
  };
  [
    "tick_size",
    "contract_size",
    "minimum",
    "step",
    "margin_per_contract",
    "leverage",
    "commission",
    "spread",
  ].forEach((k) => (profile[k] = $(k).value));
  const args = {
    profile,
    capital: $("capital").value,
    base_minutes: Number($("base_minutes").value),
    strategy: $("strategy").value,
    start: $("start").value,
    end: $("end").value,
    ...(state?.ready ? { confirm_new_session: true } : {}),
  };
  if ($("csv").files[0]) args.csv = await $("csv").files[0].text();
  $("create").disabled = true;
  $("create").textContent = t("loadingContext");
  try {
    await act("create", args);
  } finally {
    $("create").disabled = false;
    $("create").textContent = t("begin");
  }
  if (state?.ready) $("setup").hidden = true;
};
function updateStrategies() {
  const previous = $("strategy").value;
  const available = registeredStrategies.filter(
    (kind) =>
      kind === "manual" ||
      ($("symbol").value.trim() === "MNQ" &&
        $("kind").value === "futures" &&
        $("base_minutes").value === "1"),
  );
  $("strategy").replaceChildren(
    ...available.map((kind) => {
      const option = document.createElement("option");
      option.value = kind;
      option.textContent = t(kind);
      return option;
    }),
  );
  if (available.includes(previous)) $("strategy").value = previous;
}
$("symbol").onchange = $("base_minutes").onchange = updateStrategies;
$("kind").onchange = () => {
  const futures = $("kind").value === "futures";
  Object.entries(
    futures
      ? {
          tick_size: ".25",
          contract_size: "2",
          minimum: "1",
          step: "1",
          leverage: "1",
        }
      : {
          tick_size: ".00001",
          contract_size: "100000",
          minimum: ".01",
          step: ".01",
          leverage: "100",
        },
  ).forEach(([k, v]) => ($(k).value = v));
  if (!futures) $("strategy").value = "manual";
  updateStrategies();
};
async function submitTicket() {
  if (placingOrder) return;
  if (chartBrowseDate != null) return error(Error("historicalView"));
  const args = {};
  ["side", "quantity", "order_type", "sl", "tp", "price", "event_id"].forEach(k => args[k] = $(k).value);
  if (!(Number(args.quantity) > 0)) return error(Error("invalid_quantity"));
  args.journal = readJournalAnnotation();
  halt();
  placingOrder = true;
  updateBrokerTicket();
  try {
    await act("order", {...args, activate: args.order_type === "MARKET"});
  } finally {
    placingOrder = false;
    updateBrokerTicket();
  }
}
$("ticket").onsubmit = e => { e.preventDefault(); submitTicket(); };
$("modify").onclick = () => act(state.position ? "modify" : "modify_pending", {
  sl: $("positionSL").value, tp: $("positionTP").value,
  ...(!state.position && state.intent?.order_type !== "MARKET" ? {price: $("manageEntry").value} : {})
});
$("cancelVisible").onclick = () => act("cancel");
$("activatePending").onclick = () => act("advance", {steps: 1});
$("be").onclick = () => act("modify", { break_even: true });
$("trail").onclick = () => act("modify", { trailing: $("trailing").value });
$("close").onclick = async () => { halt(); await act("close", {advance: true}); };

$("stepButton").onclick = async () => {
  chartBrowseDate = null;
  halt();
  if (state?.playing && !(await act("pause"))) return;
  await act("advance", { steps: 1 });
};
$("next").onclick = async () => {
  chartBrowseDate = null;
  halt();
  const token = generation;
  if (!(await act("play"))) return;
  async function seek() {
    if (token !== generation) return;
    await act("advance", { steps: 500, until_event: true });
    if (token === generation && state?.playing) timer = setTimeout(seek, 0);
  }
  seek();
};
$("play").onclick = async () => {
  chartBrowseDate = null;
  halt();
  if (!(await act("play"))) return;
  pulse();
};
$("pause").onclick = () => {
  halt();
  act("pause");
};
$("left").onchange = $("right").onchange = () => act("state");
$("setupToggle").onclick = () => ($("setup").hidden = !$("setup").hidden);
$("language").onchange = () => {
  lang = $("language").value;
  localStorage.setItem("replay-lang", lang);
  translate();
  render();
};
function download(data, name) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
$("save").onclick = async () => {
  halt();
  await act("pause");
  try {
    download(await api("save"), "replay-session.json");
  } catch (e) {
    error(e);
  }
};
$("report").onclick = async () => {
  try {
    download(await api("report"), "trader-scoreboard.json");
  } catch (e) {
    error(e);
  }
};
$("restore").onchange = async () => {
  halt();
  try {
    if (state?.ready && !confirm(t("confirmRestore"))) {
      $("restore").value = "";
      return;
    }
    const session = JSON.parse(await $("restore").files[0].text());
    await act("restore", { session, confirm_replace_session: true });
    if (state?.ready) $("setup").hidden = true;
  } catch (e) {
    error(e);
  }
};
translate();
api("state")
  .then((data) => {
    state = data;
    registeredStrategies = data.strategies;
    if (!data.ready) {
      registeredStrategies = data.strategies;
      $("start").value = (data.start || "").slice(0, 16);
      $("end").value = (data.end || "").slice(0, 16);
      $("strategy").replaceChildren(
        ...data.strategies.map((k) => {
          const o = document.createElement("option");
          o.value = k;
          o.textContent = t(k);
          return o;
        }),
      );
      if (data.strategies.includes("mnq_m5")) $("strategy").value = "mnq_m5";
    } else {
      updateStrategies();
      $("strategy").value = data.strategy;
      $("setup").hidden = true;
      render();
    }
  })
  .catch(error);

$("htmlReport").onclick = async () => {
  halt();
  await act("pause");
  try {
    $("scoreDetails").open = true;
    await act("state");
    const report = await api("report");
    const root = document.createElement("div");
    const heading = document.createElement("h1");
    heading.textContent = t("scoreboard") + " · " + report.profile.symbol;
    root.append(heading);
    const subtitle = document.createElement("p");
    subtitle.textContent =
      t(report.strategy) + " · " + report.now + " UTC · " + t("no_lookahead");
    root.append(subtitle);
    [
      "net",
      "trades",
      "win_rate",
      "profit_factor",
      "expectancy_r",
      "total_r",
      "drawdown",
      "drawdown_pct",
      "wins",
      "losses",
      "break_even",
      "avg_win",
      "avg_loss",
      "best_r",
      "worst_r",
      "commission",
    ].forEach((k) =>
      root.append(
        row(
          k,
          ["win_rate", "drawdown_pct"].includes(k) &&
            report.scoreboard[k] != null
            ? fmt(report.scoreboard[k] * 100) + "%"
            : fmt(report.scoreboard[k]),
        ),
      ),
    );
    ["breakdown", "trades"].forEach((k) => root.append($(k).cloneNode(true)));
    root.append(journalExportContent(report.journal));
    const title = document.createElement("h2");
    title.textContent = t("instrument");
    root.append(title);
    Object.entries(report.profile).forEach(([k, v]) =>
      root.append(
        row(
          {
            kind: "instrument",
            contract_size: "contract",
            margin_per_contract: "margin",
            tick_size: "tick",
          }[k] || k,
          k === "kind" ? t(v) : String(v),
        ),
      ),
    );
    const style =
      "details{margin:12px 0;padding:8px;border:1px solid #ccd6e0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}body{font:14px system-ui;max-width:1100px;margin:40px auto;color:#142338}h1{font-size:26px}.row{display:flex;justify-content:space-between;border-bottom:1px solid #ddd;padding:8px}table{width:100%;border-collapse:collapse;margin:25px 0}td,th{text-align:right;padding:8px;border-bottom:1px solid #ddd}td:first-child,th:first-child{text-align:left}.positive{color:#087b61}.negative{color:#b12e46}@media print{body{margin:10px}tr{break-inside:avoid}}";
    const content =
      '<!doctype html><html lang="' +
      lang +
      '"><meta charset="utf-8"><title>Replay Studio</title><style>' +
      style +
      "</style><body>" +
      root.outerHTML +
      "</body></html>";
    const url = URL.createObjectURL(new Blob([content], { type: "text/html" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "trader-scoreboard.html";
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    error(e);
  }
};

$("visibleBars").onchange = $("followLatest").onclick = async () => {
  if (chartBrowseDate != null) { chartBrowseDate = null; await act("state"); }
  chartViews.forEach((view) => { view.follow = true; view.yRange = null; });
  if (state?.ready) render();
};
$("focusCharts").onclick = () => {
  $("workspace").classList.toggle("single-chart");
  [0, 1].forEach(i => Plotly.Plots.resize($("chart" + i)));
};
$("scoreDetails").ontoggle = () => { if ($("scoreDetails").open && state?.ready) act("state"); };
$("navigateView").onclick = async () => {
  const stamp = Date.parse($("navigateDate").value + "Z") / 1000;
  if (!Number.isFinite(stamp)) return;
  if (stamp > Date.parse(state.now + "Z") / 1000) return error(Error("futureView"));
  halt();
  chartBrowseDate = stamp;
  resetDraft();
  chartViews.forEach(v => { v.follow = true; v.range = null; v.yRange = null; });
  await act("pause");
};
async function navigateSession(action, args) {
  if ($("navigateReplay").disabled) return;
  halt();
  chartBrowseDate = null;
  $("navigateReplay").disabled = $("resumeCheckpoint").disabled = true;
  $("status").textContent = t("loadingContext");
  $("workspace").setAttribute("aria-busy", "true");
  try {
    await act(action, args);
    chartViews.forEach(v => v.identity = null);
    resetDraft();
    await render();
  } finally {
    $("navigateReplay").disabled = false;
    $("resumeCheckpoint").disabled = !$("checkpoints").options.length;
    $("workspace").setAttribute("aria-busy", "false");
  }
}
$("navigateReplay").onclick = () => {
  if (!confirm(t("confirmStartFromDate"))) return;
  navigateSession("navigate", {date: $("navigateDate").value, confirm_new_session: true});
};
$("resumeCheckpoint").onclick = () => navigateSession("resume", {id: $("checkpoints").value});

initTradingTools();
initJournal();
