const tradeDraft = { mode: null, entry: null, sl: null, tp: null };
let historyLimit = 800;
let activeHandleDrag = false;
let cancelPriceDrag = null;
const toolChecked = (id) => document.getElementById(id)?.checked ?? false;
const planPrice = (value) =>
  Number(
    (
      Math.round(Number(value) / Number(state.profile.tick_size)) *
      Number(state.profile.tick_size)
    ).toFixed(pricePrecision()),
  );
function resetDraft() {
  Object.assign(tradeDraft, { mode: null, entry: null, sl: null, tp: null });
  updatePlanStatus();
}
function selectPlanMode(mode) {
  if (chartBrowseDate != null) return error(Error("historicalView"));
  if (state?.position || state?.intent) {
    error(Error("occupied"));
    return;
  }
  tradeDraft.mode = mode;
  updatePlanStatus();
  [0, 1].forEach(bindChartTools);
}
function updatePlanStatus() {
  document.querySelectorAll("[data-plan]").forEach((button) => {
    button.classList.toggle(
      "selected",
      button.dataset.plan === tradeDraft.mode,
    );
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.plan === tradeDraft.mode),
    );
  });
  if (!$("planStatus")) return;
  const active = state?.position || state?.intent;
  if (active) {
    $("planStatus").textContent = t("manageOnChart");
    return;
  }
  let text = t(tradeDraft.mode ? "pick_" + tradeDraft.mode : "planHint");
  if (
    tradeDraft.entry != null &&
    tradeDraft.sl != null &&
    tradeDraft.tp != null
  ) {
    const direction = $("side").value === "BUY" ? 1 : -1;
    const risk = direction * (tradeDraft.entry - tradeDraft.sl),
      reward = direction * (tradeDraft.tp - tradeDraft.entry);
    text =
      risk > 0 && reward > 0
        ? t("risk") +
          " " +
          fmt(
            risk *
              Number($("quantity").value) *
              Number(state.profile.contract_size),
          ) +
          " " +
          state.profile.currency +
          " · RR " +
          fmt(reward / risk)
        : t("invalid_bracket");
  }
  if (state?.cursor >= state?.total) text += " · " + t("endPlanHint");
  $("planStatus").textContent = tradeDraft.mode ? t("pick_" + tradeDraft.mode) : (tradeDraft.sl != null && tradeDraft.tp != null && text.startsWith(t("risk")) ? "" : text);
  if (typeof updateBrokerTicket === "function") updateBrokerTicket();
}
function setPlanPrice(key, value) {
  if (key === "entry" && $("order_type").value === "MARKET")
    value = state.market_price ?? value;
  value = planPrice(value);
  tradeDraft[key] = value;
  $(key === "entry" ? "price" : key).value = value;
  tradeDraft.mode = null;
  updatePlanStatus();
  if (state?.ready) render();
}
function initTradingTools() {
  document
    .querySelectorAll("[data-plan]")
    .forEach(
      (button) => (button.onclick = () => selectPlanMode(button.dataset.plan)),
    );
  $("clearPlan").onclick = () => {
    resetDraft();
    for (const id of ["price", "sl", "tp"]) $(id).value = "";
    if (state?.ready) render();
  };
  initBrokerTicket();
  initChartFullscreen();
  for (const id of [
    "showPine",
    "showDivergences",
    "showFormingDivergences",
    "showDivergenceLines",
    "showRanges",
    "showPastRanges",
    "showROC",
    "showOBV",
    "showMA",
    "obvMode",
  ])
    $(id).onchange = () => {
      if (state?.ready)
        render().then(resizeFullscreenCharts);
    };
  $("moreHistory").onclick = () => {
    historyLimit = Math.min(10000, historyLimit + 2000);
    act("state");
  };
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !fullscreenChartPanel()) {
      resetDraft();
      if (state?.ready) render();
    }
  });
}
function plotTimeX(rows, time) {
  let left = 0,
    right = rows.length;
  while (left < right) {
    const mid = (left + right) >> 1;
    if (rows[mid].time <= time) left = mid + 1;
    else right = mid;
  }
  if (left === 0) return rows[0].index - 0.5;
  if (left >= rows.length) return rows.at(-1).index + 0.5;
  return rows[left - 1].index;
}
function indicatorRanges(rows, range, minutes) {
  const visible = rows.filter(
    (row) => row.index >= range[0] && row.index <= range[1],
  );
  const fit = (key, zero = false) => {
    const values = visible
      .flatMap((row) => (Array.isArray(key) ? key : [key]).map(field => row[field]))
      .filter((value) => value != null);
    if (zero) values.push(0);
    if (!values.length) return undefined;
    const min = Math.min(...values),
      max = Math.max(...values),
      pad = Math.max((max - min) * 0.15, 0.00001);
    return [min - pad, max + pad];
  };
  const result = {};
  if (toolChecked("showROC")) result["yaxis2.range"] = fit(minutes === 1 ? ["roc9", "roc45"] : "roc9", true);
  if (toolChecked("showOBV"))
    result["yaxis3.range"] =
      $("obvMode").value === "flow" ? [-1.1, 1.1] : fit("obv");
  return result;
}
function enhanceChart(index, rows, traces, layout) {
  $("divergence" + index).textContent = "";
  if (!rows.length) return;
  for (let i = 1; i < rows.length; i++) {
    const minutes = Number($(index ? "right" : "left").value);
    const gap = (rows[i].time - rows[i-1].time) / 60 - minutes;
    if (gap <= 0 || rows[i].index < layout.xaxis.range[0] || rows[i].index > layout.xaxis.range[1]) continue;
    layout.shapes.push({type:"line", x0:rows[i].index-.5, x1:rows[i].index-.5, yref:"paper", y0:0, y1:1, editable:false, line:{color:"#60758e",width:1,dash:"dot"}});
    layout.annotations.push({x:rows[i].index-.5,yref:"paper",y:1,text:"⏱ "+Math.round(gap)+"m",showarrow:false,font:{size:9,color:"#91a3bb"},yanchor:"bottom"});
  }
  const tf = Number($(index ? "right" : "left").value),
    visual = state.visuals?.[index] || {},
    target = $("chart" + index);
  const pine = toolChecked("showPine") && state.strategy === "mnq_m5",
    roc = toolChecked("showROC"),
    obv = toolChecked("showOBV");
  const live = state.position || state.intent;
  traces.slice(1, 3).forEach((trace, i) => {
    trace.visible = toolChecked("showMA");
    trace.line.color = i ? "#10bfc0" : "#b653ff";
    trace.line.simplify = !(pine && tf <= 5 && toolChecked("showMA"));
  });
  if (traces[3]) traces[3].visible = false;
  if (pine && tf <= 5) {
    if (toolChecked("showMA"))
      for (const region of maBandRegions(rows, tf)) {
        if (region.points.at(-1).x < layout.xaxis.range[0] || region.points[0].x > layout.xaxis.range[1]) continue;
        const vertices = [
          ...region.points.map(point => [point.x, point.fast]),
          ...region.points.slice().reverse().map(point => [point.x, point.slow]),
        ];
        layout.shapes.push({
          type: "path", xref: "x", yref: "y",
          path: vertices.map(([x,y],i) => (i ? "L" : "M")+x+","+y).join(" ")+" Z",
          fillcolor: region.bull ? "rgba(26,202,165,.09)" : "rgba(255,82,114,.09)",
          line: {width:0}, layer: "below", editable: false,
        });
      }
    if (toolChecked("showRanges"))
      for (const area of visual.ranges || []) {
        if (!toolChecked("showPastRanges") && area.state !== "active") continue;
        const color = area.scale === 0.75 ? "#ff9800" : "#f5c442",
          start = plotTimeX(rows, area.start_time),
          end = plotTimeX(rows, area.end_time - 1) + 0.5,
          born = plotTimeX(rows, area.known_at - 1);
        layout.shapes.push({
          type: "rect",
          x0: start,
          x1: end,
          y0: area.floor,
          y1: area.ceiling,
          line: { color, width: 1.5 },
          fillcolor:
            area.scale === 0.75
              ? "rgba(255,152,0,.05)"
              : "rgba(245,196,66,.05)",
          editable: false,
          layer: "below",
        });
        for (const price of [
          area.floor - area.tolerance,
          area.ceiling + area.tolerance,
        ])
          layout.shapes.push({
            type: "line",
            x0: born,
            x1: end,
            y0: price,
            y1: price,
            line: { color, width: 1, dash: "dash" },
            editable: false,
          });
        layout.annotations.push({
          x: born,
          y: area.ceiling,
          text:
            "R " +
            area.scale.toLocaleString(lang, { minimumFractionDigits: 2 }),
          showarrow: false,
          yshift: 12,
          font: { size: 10, color },
          hovertext:
            t("confirmedRange") +
            " · " +
            new Date(area.known_at * 1000).toISOString() +
            "<br>" +
            fmt(area.floor) +
            " — " +
            fmt(area.ceiling),
        });
      }
    const events = [
      ...(visual.events || []),
      ...state.markers.filter((e) => e.provisional),
    ];
    const seen = new Set(),
      lanes = [-10000, -10000, -10000, -10000];
    const codes = {
      cross: "X",
      cross_provisional: "X?",
      break: "B",
      retest: "R",
      confirmation: "C",
      continuation: "CONT",
      failure: "F",
      reversal: "REV",
    };
    for (const event of events) {
      if (!codes[event.kind]) continue;
      const stamp = Date.parse(event.known_at + "Z") / 1000 - 1;
      if (stamp < rows[0].time || stamp > rows.at(-1).time + tf * 60) continue;
      const eventX = plotTimeX(rows, stamp);
      if (eventX < layout.xaxis.range[0] - 5 || eventX > layout.xaxis.range[1] + 5) continue;
      const id = event.kind + event.known_at;
      if (seen.has(id)) continue;
      seen.add(id);
      const x = plotTimeX(rows, stamp),
        lane = lanes.findIndex((last) => x - last > 4),
        slot = lane < 0 ? 0 : lane;
      lanes[slot] = x;
      layout.annotations.push({
        x,
        y: event.price,
        text: codes[event.kind],
        showarrow: false,
        yshift: (event.direction > 0 ? -1 : 1) * (14 + slot * 14),
        bgcolor: event.provisional
          ? "#f5c442"
          : event.direction > 0
            ? "#1acaa5"
            : "#ff5272",
        font: { size: 9, color: "#101722" },
        borderpad: 2,
        hovertext: t(event.kind) + " · " + event.known_at + " UTC",
      });
    }
  }
  drawDivergences(index, rows, traces, layout, pine);
  const context = $("framework" + index);
  context.hidden = !pine;
  if (pine) {
    context.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = t("frameworkM5");
    context.append(title);
    for (const [label, value] of [
      ["stage", t(visual.stage || "unknown")],
      ["rangeNow", t(visual.range_state || "unknown")],
      ["ROC9", fmt(visual.values?.roc)],
      ["ATR14", fmt(visual.values?.atr)],
      ["OBV20", fmt(visual.values?.obv_flow)],
    ]) {
      const span = document.createElement("span");
      span.textContent = t(label) + ": " + value;
      context.append(span);
    }
  }
  const count = Number(roc) + Number(obv),
    priceBottom = count === 2 ? 0.43 : count === 1 ? 0.26 : 0;
  layout.legend.traceorder = "normal";
  layout.yaxis.domain = [priceBottom, 1];
  layout.yaxis.title = { text: "" };
  if (roc) {
    layout.yaxis2 = {
      domain: obv ? [0.22, 0.36] : [0, 0.18],
      anchor: "x",
      side: "right",
      gridcolor: "#192639",
      zeroline: true,
      zerolinecolor: "#526780",
      title: { text: "ROC %", font: { size: 10 } },
      range: indicatorRanges(rows, layout.xaxis.range, tf)["yaxis2.range"],
    };
    for (const period of tf === 1 ? [9, 45] : [9])
      traces.push({
        type: "scatter",
        mode: "lines",
        x: rows.map((r) => r.index),
        y: rows.map((r) => r["roc" + period]),
        yaxis: "y2",
        name: "ROC" + period,
        line: {
          width: 1.4,
          color: period === 45 ? "#4a9fff" : tf === 5 ? "#a6cef5" : "#ffbe2a",
          dash: period === 45 ? "dash" : "solid",
        },
        hoverinfo: "y+name",
      });
  }
  if (obv) {
    const flow = $("obvMode").value === "flow",
      key = flow ? "obv_flow" : "obv";
    layout.yaxis3 = {
      domain: [0, roc ? 0.15 : 0.18],
      anchor: "x",
      side: "right",
      gridcolor: "#192639",
      zeroline: true,
      zerolinecolor: "#526780",
      title: { text: flow ? "OBV20" : "OBV", font: { size: 10 } },
      tickformat: flow ? ".2f" : ".3s",
      range: indicatorRanges(rows, layout.xaxis.range, tf)["yaxis3.range"],
    };
    traces.push({
      type: "scatter",
      mode: "lines",
      x: rows.map((r) => r.index),
      y: rows.map((r) => r[key]),
      yaxis: "y3",
      name: flow ? t("flowOBV") : "OBV",
      line: { color: "#b653ff", width: 1.4 },
      hoverinfo: "y+name",
    });
  }
  if (!live && ticketExpanded) {
    for (const key of ["entry", "sl", "tp"])
      if (tradeDraft[key] != null) {
        layout.shapes.push({
          type: "line",
          xref: "paper",
          x0: 0,
          x1: 1,
          y0: tradeDraft[key],
          y1: tradeDraft[key],
          line: {
            color:
              key === "sl" ? "#ff5272" : key === "tp" ? "#1acaa5" : "#60a5fa",
            width: 2,
            dash: "dash",
          },
          editable: false,
        });

      }
  }
  const plan = live
    ? { entry: live.entry ?? live.price, sl: live.sl, tp: live.tp }
    : (ticketExpanded ? tradeDraft : {});
  if (plan.entry != null)
    for (const [key, color] of [
      ["sl", "rgba(255,82,114,.13)"],
      ["tp", "rgba(26,202,165,.13)"],
    ])
      if (plan[key] != null)
        layout.shapes.push({
          type: "rect",
          xref: "paper",
          x0: 0.76,
          x1: 0.98,
          y0: plan.entry,
          y1: plan[key],
          line: { width: 0 },
          fillcolor: color,
          editable: false,
          layer: "below",
        });
  document
    .querySelectorAll("[data-plan]")
    .forEach((button) => (button.disabled = !!live));
  if (live && tradeDraft.entry != null) resetDraft();
  updatePlanStatus();
  const baseContext = $("history" + index);
  if (state.context_source && tf >= state.context_minutes)
    baseContext.textContent += " · " + state.context_source;
  target.classList.toggle("planning", !!tradeDraft.mode);
}
function bindChartTools(index) {
  const target = $("chart" + index),
    plot = target._fullLayout,
    axis = plot?.yaxis;
  if (!axis) return;
  let surface = target.querySelector(".plan-surface");
  if (!surface) {
    surface = document.createElement("div");
    surface.className = "plan-surface";
    surface.innerHTML = '<div class="price-guide"><span></span></div>';
    target.append(surface);
  }
  Object.assign(surface.style, {
    display: tradeDraft.mode ? "block" : "none",
    left: plot.xaxis._offset + "px",
    top: axis._offset + "px",
    width: plot.xaxis._length + "px",
    height: axis._length + "px",
  });
  const valueAt = (event) =>
    axis.p2d(event.clientY - surface.getBoundingClientRect().top);
  surface.onclick = (event) => {
    if (!tradeDraft.mode) return;
    event.preventDefault();
    event.stopPropagation();
    setPlanPrice(tradeDraft.mode, valueAt(event));
  };
  surface.onmousemove = (event) => {
    const guide = surface.firstElementChild;
    guide.style.top =
      event.clientY - surface.getBoundingClientRect().top + "px";
    guide.firstElementChild.textContent = fmt(planPrice(valueAt(event)));
  };
  let handles = target.querySelector(".order-handles");
  if (!handles) {
    handles = document.createElement("div");
    handles.className = "order-handles";
    target.append(handles);
  }
  if (!activeHandleDrag) drawPriceBadges(target, plot, handles);
  if (target._tradeToolsBound) return;
  target._tradeToolsBound = true;
  target.on("plotly_afterplot", () => { if (!activeHandleDrag) bindChartTools(index); });
  target.on("plotly_relayout", () => { if (!activeHandleDrag) requestAnimationFrame(() => bindChartTools(index)); });
}

function drawDivergences(index, rows, traces, layout, pine) {
  const tf = Number($(index ? "right" : "left").value);
  const chip = $("divergence" + index);
  chip.textContent = "";
  chip.title = t("divergenceScope");
  if (!pine || !toolChecked("showDivergences") || ![1, 5].includes(tf)) return;
  const range = layout.xaxis.range;
  const all = (state.visuals?.[index]?.divergences || []).filter(row => !row.provisional || toolChecked("showFormingDivergences"));
  const visible = all.filter(row => {
    const time = row.provisional ? row.terminal_time : row.known_at - 1;
    const x = plotTimeX(rows, time);
    return x >= range[0] && x <= range[1];
  });
  const confirmed = visible.filter(row => !row.provisional).length;
  const forming = visible.length - confirmed;
  chip.textContent = "D " + confirmed + (forming ? " · D? " + forming : "");
  chip.title = t("divergenceConfirmed") + ": " + confirmed + " · " + t("divergenceForming") + ": " + forming + " · " + fmt(.75) + " ATR";
  const lanes = [-Infinity, -Infinity, -Infinity];
  for (const row of all) {
    const color = row.direction > 0 ? "#58dfbd" : "#ff7990";
    const x0 = plotTimeX(rows, row.anchor_time), x1 = plotTimeX(rows, row.terminal_time);
    if (toolChecked("showDivergenceLines") && row.anchor_time >= rows[0].time && row.terminal_time <= rows.at(-1).time && x1 >= range[0] && x0 <= range[1]) {
      const line = {color, width:row.provisional ? 1.3 : 1.7, dash:row.provisional ? "dot" : row.period === 45 ? "dash" : "solid"};
      layout.shapes.push({type:"line",xref:"x",yref:"y",x0,x1,y0:row.anchor_price,y1:row.terminal_price,line,editable:false});
      if (toolChecked("showROC"))
        traces.push({type:"scatter",mode:"lines+markers",x:[x0,x1],y:[row.anchor_roc,row.terminal_roc],yaxis:"y2",line,marker:{size:4,color},showlegend:false,hoverinfo:"skip",connectgaps:false});
    }
  }
  const groups = new Map();
  for (const row of visible) {
    const key = [row.provisional ? row.terminal_time : row.known_at, row.direction, row.provisional].join(":");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  for (const group of groups.values()) {
    const row = group[0];
    const x = plotTimeX(rows, row.provisional ? row.terminal_time : row.known_at-1);
    const slot = Math.max(0, lanes.findIndex(last => x-last > 7));
    lanes[slot] = x;
    const color = row.direction > 0 ? "#58dfbd" : "#ff7990";
    const title = t(row.provisional ? "divergenceForming" : "divergenceConfirmed");
    layout.annotations.push({x,y:row.provisional ? row.terminal_price : row.price,
      text:(row.provisional ? "D?" : "D")+(row.direction > 0 ? "↑" : "↓")+(tf === 5 ? " M5" : group.map(item => item.period).join("/")),
      showarrow:true,arrowhead:0,arrowwidth:1,arrowcolor:color,ax:0,ay:(row.direction > 0 ? 1 : -1)*(34+slot*17),
      bgcolor:row.provisional ? "#243445" : color,bordercolor:color,borderpad:2,
      font:{size:9,color:row.provisional ? color : "#101722"},
      hovertext:title+" · "+tf+"m · "+fmt(row.scale)+" ATR<br>"+group.map(item => "ROC"+item.period+" · "+t("divergenceKnown")+" "+new Date(item.known_at*1000).toISOString().slice(0,19)+" UTC<br>"+fmt(item.anchor_price)+" → "+fmt(item.terminal_price)+" · ROC "+fmt(item.anchor_roc)+" → "+fmt(item.terminal_roc)).join("<br>")});
  }
}

function drawPriceBadges(target, plot, layer) {
  const focused = document.activeElement?.classList.contains("price-badge") ? document.activeElement.dataset.level : null;
  layer.replaceChildren();
  const axis = plot.yaxis;
  const live = state.position || state.intent;
  const plan = live ? {entry:live.entry ?? live.price,sl:live.sl,tp:live.tp} : (ticketExpanded ? tradeDraft : {});
  const last = target.data?.[0]?.close?.at(-1);
  const levels = [
    {key:"market",price:last,readonly:true},
    ...["entry","sl","tp"].map(key => ({key,price:plan[key],readonly:!!tradeDraft.mode || key === "entry" && !!state.position})),
  ].filter(row => row.price != null && axis.d2p(row.price) >= 0 && axis.d2p(row.price) <= axis._length)
    .map(row => ({...row,actual:axis.d2p(row.price)})).sort((a,b) => a.actual-b.actual);
  const half = 10, spacing = 24;
  for (let i=0;i<levels.length;i++)
    levels[i].display = Math.max(half,levels[i].actual,i ? levels[i-1].display+spacing : half);
  if (levels.length && levels.at(-1).display > axis._length-half) {
    levels.at(-1).display = axis._length-half;
    for (let i=levels.length-2;i>=0;i--)
      levels[i].display = Math.min(levels[i].display,levels[i+1].display-spacing);
  }
  const edge = plot.xaxis._offset+plot.xaxis._length;
  const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
  svg.classList.add("badge-connectors");
  layer.append(svg);
  const quoteFormat = value => Number(value).toLocaleString(lang,{minimumFractionDigits:pricePrecision(),maximumFractionDigits:pricePrecision()});
  for (const row of levels) {
    const label = row.key === "market" ? t("priceAxis") : row.key === "entry" ? t("entry") : row.key.toUpperCase();
    const badge = document.createElement(row.readonly ? "span" : "button");
    badge.className = "price-badge " + row.key;
    badge.classList.toggle("down",row.key === "market" && Number(last) < Number(target.data?.[0]?.open?.at(-1)));
    badge.dataset.level = row.key;
    badge.dataset.price = row.price;
    badge.title = label + " · " + quoteFormat(row.price);
    if (!row.readonly) {
      badge.type = "button";
      badge.classList.add("order-handle");
      badge.setAttribute("aria-label",t("dragLevel")+" "+label);
    }
    const price = document.createElement("span");
    price.className = "badge-price";
    const role = document.createElement("span");
    role.className = "badge-role";
    role.textContent = row.key === "market" ? "" : row.key === "entry" ? "E" : row.key.toUpperCase();
    badge.append(price,role);
    const connector = document.createElementNS("http://www.w3.org/2000/svg","path");
    connector.dataset.level = row.key;
    connector.classList.add(row.key);
    svg.append(connector);
    const paint = (actual, display, value) => {
      badge.style.left = edge+7+"px";
      badge.style.top = axis._offset+display-half+"px";
      price.textContent = quoteFormat(value);
      connector.setAttribute("d",`M${edge},${axis._offset+actual}H${edge+3}L${edge+7},${axis._offset+display}`);
    };
    paint(row.actual,row.display,row.price);
    layer.append(badge);
    if (focused === row.key && !row.readonly) badge.focus({preventScroll:true});
    if (row.readonly) continue;
    let dragging = false, startY = 0, value = row.price;
    const cleanup = () => {
      dragging = activeHandleDrag = false;
      cancelPriceDrag = null;
      document.removeEventListener("pointermove",move,true);
      document.removeEventListener("pointerup",finish,true);
      document.removeEventListener("keydown",escape,true);
      window.removeEventListener("blur",cancel);
    };
    const cancel = () => { cleanup(); render(); };
    const escape = event => { if (event.key === "Escape") cancel(); };
    const amend = next => {
      if (next === Number(row.price)) { render(); return; }
      if (live) act(state.position ? "modify" : "modify_pending",{[row.key === "entry" ? "price" : row.key]:next});
      else {
        tradeDraft[row.key] = next;
        $(row.key === "entry" ? "price" : row.key).value = next;
        updatePlanStatus();
        render();
      }
    };
    const move = event => {
      if (!dragging) return;
      const delta = event.clientY-startY;
      const actual = Math.min(axis._length,Math.max(0,row.actual+delta));
      value = planPrice(axis.p2d(actual));
      paint(actual,row.display+actual-row.actual,value);
    };
    const finish = event => {
      if (!dragging) return;
      move(event);
      cleanup();
      amend(value);
    };
    badge.onpointerdown = event => {
      event.preventDefault();
      event.stopPropagation();
      startY = event.clientY;
      dragging = activeHandleDrag = true;
      cancelPriceDrag = cancel;
      badge.setPointerCapture(event.pointerId);
      halt();
      document.addEventListener("pointermove",move,true);
      document.addEventListener("pointerup",finish,true);
      document.addEventListener("keydown",escape,true);
      window.addEventListener("blur",cancel);
      api("pause").catch(error);
    };
    badge.onpointercancel = cancel;
    badge.onkeydown = event => {
      if (!["ArrowUp","ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      amend(planPrice(Number(row.price)+(event.key === "ArrowUp" ? 1 : -1)*Number(state.profile.tick_size)));
    };
  }
}

let lastFullscreenChart = null;
let fullscreenReturnFocus = null;
let fullscreenTransition = false;
function fullscreenChartPanel() {
  return document.fullscreenElement?.classList.contains("chartpanel")
    ? document.fullscreenElement : document.querySelector(".chartpanel.chart-fullscreen");
}
function resizeFullscreenCharts() {
  requestAnimationFrame(() => {
    for (const i of [0,1]) {
      const plot = $("chart"+i);
      if (plot._fullLayout && plot.offsetWidth && plot.offsetHeight)
        Promise.resolve(Plotly.Plots.resize(plot)).then(() => bindChartTools(i)).catch(error);
    }
  });
}
function syncChartFullscreen() {
  const active = fullscreenChartPanel();
  document.body.classList.toggle("chart-is-fullscreen",!!active);
  document.querySelectorAll("[data-chart-fullscreen]").forEach(button => {
    const panel = button.closest(".chartpanel"), selected = panel === active;
    const key = selected ? "exitFullscreenChart" : "fullscreenChart";
    button.dataset.aria = key;
    button.setAttribute("aria-label",t(key));
    button.setAttribute("aria-expanded",String(selected));
    button.title = t(key);
    button.textContent = selected ? "× "+t("exitFullscreenShort") : "⛶";
    panel.querySelector(".fullscreen-controls").hidden = !selected;
    if (selected) {
      panel.setAttribute("role","dialog");
      panel.setAttribute("aria-modal","true");
      panel.setAttribute("aria-label",t("fullscreenChart"));
    } else {
      panel.removeAttribute("role");
      panel.removeAttribute("aria-modal");
      panel.removeAttribute("aria-label");
    }
    for (const proxy of panel.querySelectorAll("[data-fullscreen-action]")) {
      proxy.disabled = $(proxy.dataset.fullscreenAction).disabled;
      proxy.textContent = t(proxy.dataset.t);
    }
    panel.querySelector(".fullscreen-speed").value = $("speed").value;
    for (const check of panel.querySelectorAll("[data-fullscreen-study]"))
      check.checked = $(check.dataset.fullscreenStudy).checked;
    panel.querySelector(".fullscreen-status").textContent = state?.ready
      ? state.now.replace("T"," ")+" UTC · "+$("status").textContent : "";
  });
  if (active !== lastFullscreenChart) {
    const previous = lastFullscreenChart;
    lastFullscreenChart = active;
    if (activeHandleDrag) cancelPriceDrag?.();
    resizeFullscreenCharts();
    if (previous && !active) fullscreenReturnFocus?.focus({preventScroll:true});
  }
}
async function toggleChartFullscreen(index) {
  if (fullscreenTransition) return;
  fullscreenTransition = true;
  try {
  const panel = $("chart"+index).closest(".chartpanel");
  if (fullscreenChartPanel() === panel) {
    if (document.fullscreenElement === panel) await document.exitFullscreen();
    else { panel.classList.remove("chart-fullscreen"); syncChartFullscreen(); }
    return;
  }
  fullscreenReturnFocus = panel.querySelector("[data-chart-fullscreen]");
  if (document.fullscreenElement) await document.exitFullscreen();
  try {
    if (!panel.requestFullscreen) throw new Error("fullscreen_unavailable");
    await panel.requestFullscreen({navigationUI:"hide"});
  } catch {
    panel.classList.add("chart-fullscreen");
  }
  syncChartFullscreen();
  panel.querySelector("[data-chart-fullscreen]").focus({preventScroll:true});
  } finally { fullscreenTransition = false; }
}

function initChartFullscreen() {
  document.querySelectorAll("[data-chart-fullscreen]").forEach(button => {
    const group = document.createElement("div");
    group.className = "fullscreen-controls";
    group.hidden = true;
    for (const [action,key] of [["play","play"],["pause","pause"],["stepButton","nextBar"],["next","nextEvent"]]) {
      const proxy = document.createElement("button");
      proxy.type = "button";
      proxy.dataset.fullscreenAction = action;
      proxy.dataset.t = key;
      proxy.textContent = t(key);
      proxy.onclick = () => $(action).click();
      group.append(proxy);
    }
    const speed = document.createElement("select");
    speed.className = "fullscreen-speed";
    speed.dataset.aria = "speed";
    speed.setAttribute("aria-label",t("speed"));
    for (const option of $("speed").options) speed.append(option.cloneNode(true));
    speed.onchange = () => { $("speed").value = speed.value; syncChartFullscreen(); };
    group.append(speed);
    for (const [id,label] of [["showROC","ROC"],["showOBV","OBV"]]) {
      const wrapper = document.createElement("label"), check = document.createElement("input");
      check.type = "checkbox";
      check.dataset.fullscreenStudy = id;
      check.checked = $(id).checked;
      check.onchange = () => { $(id).checked = check.checked; $(id).onchange(); syncChartFullscreen(); };
      wrapper.append(check,document.createTextNode(label));
      group.append(wrapper);
    }
    const status = document.createElement("span");
    status.className = "fullscreen-status";
    group.append(status);
    button.parentElement.insertBefore(group,button.parentElement.querySelector(".chart-quote"));
    button.onclick = () => toggleChartFullscreen(Number(button.dataset.chartFullscreen)).catch(error);
  });
  document.addEventListener("fullscreenchange",syncChartFullscreen);
  document.addEventListener("keydown",event => {
    const active = fullscreenChartPanel();
    if (!active) return;
    if (event.key === "Escape") {
      if (activeHandleDrag) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const i = active.querySelector("[data-chart-fullscreen]").dataset.chartFullscreen;
      toggleChartFullscreen(Number(i)).catch(error);
    } else if (event.key === "Tab") {
      const focusable = [...active.querySelectorAll("button:not([disabled]),select,input,[tabindex]")]
        .filter(node => node.tabIndex >= 0 && node.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  },true);
  syncChartFullscreen();
}


function maBandRegions(rows, minutes) {
  const regions = [];
  let current = null;
  const finish = () => {
    if (current && current.points.some(point => point.fast !== point.slow)) regions.push(current);
    current = null;
  };
  const edge = (a, b, sign) => {
    if (!sign) { finish(); return; }
    if (!current || current.bull !== (sign > 0) || current.points.at(-1).x !== a.x) {
      finish();
      current = {bull:sign > 0, points:[a]};
    }
    current.points.push(b);
  };
  const valid = row => [row.index,row.time,row.ma9,row.ma20].every(Number.isFinite);
  for (let i=1;i<rows.length;i++) {
    const previous = rows[i-1], next = rows[i];
    if (!valid(previous) || !valid(next) || next.time-previous.time !== minutes*60 || next.index-previous.index !== 1) {
      finish();
      continue;
    }
    const a = {x:previous.index,fast:previous.ma9,slow:previous.ma20};
    const b = {x:next.index,fast:next.ma9,slow:next.ma20};
    const left = a.fast-a.slow, right = b.fast-b.slow;
    if (left > 0 && right < 0 || left < 0 && right > 0) {
      const fraction = left/(left-right);
      const price = a.fast+fraction*(b.fast-a.fast);
      const cross = {x:a.x+fraction*(b.x-a.x),fast:price,slow:price};
      edge(a,cross,Math.sign(left));
      finish();
      edge(cross,b,Math.sign(right));
    } else edge(a,b,Math.sign(left || right));
  }
  finish();
  return regions;
}

function renderEntryAssessment() {
  const model = state.visuals?.find(view => view?.entry_assessment)?.entry_assessment;
  const panel = $("entryAssessment");
  panel.hidden = !model;
  if (!model) return;
  const loadButton = $("loadEngineLimit");
  loadButton.hidden = !model.proposal;
  const block = engineLimitBlock(model);
  loadButton.disabled = !!block;
  loadButton.title = t(block || "loadEngineLimitHint");
  loadButton.onclick = loadEngineLimit;
  $("entryDecision").textContent = t("entry_" + model.decision);
  panel.dataset.decision = model.decision;
  const stageNames = {cross:"X",break:"B",retest:"R",confirmation:"C"};
  $("entryStages").replaceChildren(...Object.entries(stageNames).map(([key, label]) => {
    const node = document.createElement("span");
    node.textContent = label;
    node.className = model.stages[key] ? "known" : "";
    node.title = t(key) + (model.stages[key] ? " · " + model.stages[key].replace("T", " ") + " UTC" : "");
    return node;
  }));
  $("entryReason").textContent = (model.direction ? t(model.direction > 0 ? "buy" : "sell") + " · " : "") + t("entry_reason_" + model.reason);
  const proposal = $("entryProposal");
  proposal.hidden = !model.proposal;
  proposal.replaceChildren();
  if (model.proposal) {
    const p = model.proposal;
    for (const [key,value] of [["entry",p.entry],["sl",p.sl],["estimatedTP",p.tp_estimate]]) {
      const node = document.createElement("span");
      node.textContent = t(key) + " " + fmt(value);
      proposal.append(node);
    }
    const stamp = document.createElement("small");
    stamp.textContent = "LIMIT · " + p.known_at.replace("T"," ") + " UTC · " + t("entryTargetEstimate");
    proposal.append(stamp);
  }
  const evidence = $("entryEvidence");
  evidence.replaceChildren();
  const lines = [t("entryPlannerOnly"),model.evaluated_at ? t("entryEvaluated") + " " + model.evaluated_at.replace("T"," ") + " UTC" : t("maWarming"),
    model.model + " · " + model.config_source,
    "ROC " + (model.config.require_roc_momentum ? t("entryRequired") : t("entryOptional")) + " · ATR " + (model.config.require_atr_expansion ? t("entryRequired") : t("entryOptional")) + " · OBV " + (model.config.require_obv_confirmation ? t("entryRequired") : t("entryOptional")),
    t("entryGeometry") + " " + model.config.limit_gap_atr + " ATR · SL " + model.config.stop_buffer_atr + " ATR · TP " + model.config.target_r + "R"];
  if (model.cross_context) lines.push("ROC(X) " + fmt(model.cross_context.roc) + " · ΔROC " + fmt(model.cross_context.roc_delta));
  for (const text of lines) { const line = document.createElement("p"); line.textContent = text; evidence.append(line); }
}
