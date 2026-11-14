function journalAnnotationAt(record, basis) {
  return basis === "entry" ? record.manual : record.annotation || record.revisions?.at(-1)?.manual || record.manual;
}
function journalAnnotationOrigin(record, basis) {
  if (basis !== "entry" && record.revisions?.length) return "late";
  return record.retrospective_only ? "missing" : "entry";
}
function journalIsClosed(record) {
  return !!record.outcome && Number.isFinite(record.outcome.pnl);
}
function journalMetrics(records) {
  const closed=records.filter(journalIsClosed), rs=closed.map(record=>record.outcome.r).filter(value=>typeof value === "number" && Number.isFinite(value));
  const wins=closed.filter(record=>record.outcome.pnl>0).length;
  const losses=closed.filter(record=>record.outcome.pnl<0).length;
  const totalR=rs.reduce((sum,value)=>sum+value,0);
  return {n:closed.length,wins,losses,flat:closed.length-wins-losses,nR:rs.length,
    winRate:closed.length ? wins/closed.length : null,meanR:rs.length ? totalR/rs.length : null,totalR:rs.length ? totalR : null};
}
function journalEngineValue(record) {
  const model=record.engine_context?.assessment;
  if(!model || model.decision === "unavailable") return "unavailable";
  if(model.decision === "propose" && model.direction !== (record.side === "BUY" ? 1 : -1)) return "opposite";
  return model.decision;
}
function journalCriterionRelation(dimension,value,side) {
  if(!value || value === "unassessed") return "unassessed";
  if(dimension === "engine") return value === "propose" ? "aligned" : value === "opposite" ? "opposing" : value === "unavailable" ? "unassessed" : "nondirectional";
  if(dimension.startsWith("divergence_")) {
    if(value === "none") return "no_signal";
    if(value === "mixed") return "mixed";
    if(!["BUY","SELL"].includes(side)) return "unassessed";
    return (value === "bullish") === (side === "BUY") ? "aligned" : "opposing";
  }
  if(dimension === "obv_trend") {
    if(value === "flat") return "neutral";
    return (value === "increasing") === (side === "BUY") ? "aligned" : "opposing";
  }
  if(["obv","roc","structure_h1","structure_h4"].includes(dimension)) {
    if(value.startsWith("favor_") || value === "aligned") return "aligned";
    if(value === "opposing") return "opposing";
    return value === "mixed" ? "mixed" : "neutral";
  }
  return "nondirectional";
}
function journalCriterionOrigin(record,dimension,basis) {
  if(basis === "entry") return record.retrospective_only ? "missing" : "entry";
  if(record.retrospective_only) return record.revisions?.length ? "late" : "missing";
  return journalAnnotationAt(record,"latest")?.criteria?.[dimension] === record.manual?.criteria?.[dimension] ? "entry" : "late";
}
function journalGroupOrigin(record,dimension,basis,filter) {
  const primary=dimension === "engine" ? record.engine_context?.assessment ? "snapshot" : "missing" : journalCriterionOrigin(record,dimension,basis);
  if(!filter?.dimension || !filter.value) return primary;
  const secondary=journalCriterionOrigin(record,filter.dimension,basis);
  return primary === "missing" || secondary === "missing" ? "missing" : primary === "late" || secondary === "late" ? "late" : primary;
}
function journalPatternGroups(records,dimension,basis,side="all",filter=null) {
  const groups=new Map();
  for(const record of records) {
    if(!journalIsClosed(record) || side !== "all" && record.side !== side || !journalMatchesContext(record,filter,basis)) continue;
    const value=dimension === "engine" ? journalEngineValue(record) : journalAnnotationAt(record,basis)?.criteria?.[dimension] || "unassessed";
    const origin=journalGroupOrigin(record,dimension,basis,filter);
    const key=JSON.stringify([record.side,value,origin]);
    if(!groups.has(key)) groups.set(key,{key,side:record.side,value,origin,dimension,basis,
      relation:journalCriterionRelation(dimension,value,record.side),records:[]});
    groups.get(key).records.push(record);
  }
  return [...groups.values()].map(group=>({...group,...journalMetrics(group.records)}))
    .sort((a,b)=>a.side.localeCompare(b.side) || a.value.localeCompare(b.value) || a.origin.localeCompare(b.origin));
}

function journalMatchesContext(record,filter,basis) {
  return !filter?.dimension || !filter.value || journalAnnotationAt(record,basis)?.criteria?.[filter.dimension] === filter.value;
}

let journalFormSignature = "";
let journalSessionKey = "";
let journalLoadSignature = "";
let journalLoading = false;
let journalData = [];
let journalEditing = null;
let journalDeepLinkOpened = false;
const journalAutoNotes = {entry:null,edit:null};

function readJournalAnnotation() {
  return {note: $("journalNote").value, criteria:Object.fromEntries(
    [...$("journalFields").querySelectorAll("select")].filter(node => node.value).map(node => [node.dataset.criterion, node.value]))};
}
function clearJournalForm() {
  $("journalNote").value = "";
  journalAutoNotes.entry=null;
  if($("journalAutoStatus")) $("journalAutoStatus").textContent="";
  $("journalFields").querySelectorAll("select").forEach(node => node.value = "");
}
function renderJournalStatus() {
  const sessionKey = JSON.stringify([state.profile.symbol,state.replay_start]);
  if (sessionKey !== journalSessionKey) { clearJournalForm(); journalSessionKey = sessionKey; }
  const catalog = state.journal_catalog || {fields:[]};
  const signature = JSON.stringify([catalog,lang]);
  if (signature !== journalFormSignature) {
    const previous = readJournalAnnotation();
    $("journalFields").replaceChildren(...journalFieldNodes(catalog,previous.criteria,"journalCriterion_",!!(state.position || state.intent)));
    for(const option of $("jrDimension").options) option.textContent=jrDimension(option.value);
    $("jrExpand").textContent=t($("jrFocusDialog")?.open ? "jrBackCharts" : "jrExpand");
    for(const option of $("jrContextDimension").options) option.textContent=option.value ? jrDimension(option.value) : t("jrNoContext");
    for(const option of $("jrContextValue").options) option.textContent=option.value ? jrCondition($("jrContextDimension").value,option.value) : t("jrAnyValue");
    journalFormSignature = signature;
  }
  $("journalCount").textContent = (state.journal_count || 0) + state.trades.filter(trade => !trade.journal_id).length;
  $("journalStorageWarning").hidden = !state.journal_storage_error;
  const loadSignature = JSON.stringify([state.journal_count,state.scoreboard.trades,state.position?.journal_id,state.intent?.journal_id,lang]);
  if ($("journalDetails").open && loadSignature !== journalLoadSignature && !journalLoading)
    loadJournal(loadSignature);
  if(!journalDeepLinkOpened && ["#journal","#journal-patterns"].includes(location.hash)) {
    journalDeepLinkOpened=true;toggleJournalFocus();jrActivate(location.hash === "#journal-patterns" ? "patterns" : "trades");
  }
}
function proposedOnSide(record) {
  const assessment = record.engine_context?.assessment;
  return assessment?.decision === "propose" && assessment.direction === (record.side === "BUY" ? 1 : -1);
}
function journalDecision(record) {
  const assessment = record.engine_context?.assessment;
  if (!assessment) return t("journalNoEngine");
  return t("entry_" + assessment.decision) + " · " + t("entry_reason_" + assessment.reason) +
    (assessment.direction && assessment.direction !== (record.side === "BUY" ? 1 : -1) ? " · " + t("journalOppositeSide") : "");
}
function criterionLabel(record, key, value) {
  const field = record.catalog.fields.find(item => item.id === key);
  return t(field?.label_key || key) + ": " + t(field?.options.find(item => item.id === value)?.label_key || value);
}
async function loadJournal(signature = "") {
  if (journalLoading) return;
  journalLoading = true;
  try {
    const data = await api("journal");
    journalData = data.journal;
    journalLoadSignature = signature;
    renderJournalRows();
  } catch {
    $("journalSummary").textContent = t("journalLoadError");
  } finally { journalLoading = false; }
}
let journalView = "trades";
let journalPage = 0;
let journalOpenRecord = null;
let journalCohort = null;
const journalPageSize = 6;
const journalDimensions = ["divergence_m5","divergence_m1","roc","obv","obv_trend","atr","setup","structure_h1","structure_h4","engine"];
function jrNode(tag,className,text) {
  const node=document.createElement(tag);if(className) node.className=className;if(text != null) node.textContent=text;return node;
}
function jrSide(side) {return t(side === "BUY" ? "jrLong" : side === "SELL" ? "jrShort" : "journalUnset");}
function jrOrigin(origin) {return t("jrOrigin_"+origin);}
function jrCondition(dimension,value) {
  if(value === "unassessed") return t("journalUnset");
  if(dimension === "engine") return value === "opposite" ? t("journalOppositeSide") : value === "unavailable" ? t("journalNoEngine") : t("entry_"+value);
  return t("journal_value_"+value);
}
function jrDimension(dimension) {return t(dimension === "engine" ? "jrEngine" : "journal_"+dimension);}
function jrR(value) {return value == null ? "—" : new Intl.NumberFormat(lang,{maximumFractionDigits:4}).format(value === 0 ? 0 : value)+"R";}
function jrOutcomeText(metrics) {
  return {winsText:metrics.wins+" "+t(metrics.wins===1 ? "jrWinner" : "jrWinners"),
    lossesText:metrics.losses+" "+t(metrics.losses===1 ? "jrLoser" : "jrLosers"),
    flatText:metrics.flat+" "+t(metrics.flat===1 ? "jrFlat" : "jrFlats")};
}
function jrResultBreakdown(metrics) {const words=jrOutcomeText(metrics);return [words.winsText,words.lossesText,words.flatText].join(" · ");}
function jrRate(value) {return value == null ? "—" : new Intl.NumberFormat(lang,{style:"percent",maximumFractionDigits:1}).format(value);}
function jrFormat(template,values) {return t(template).replace(/\{(\w+)\}/g,(_,key)=>values[key] ?? "");}
function jrSearchText(value) {return value.normalize("NFD").replace(/\p{Diacritic}/gu,"").toLocaleLowerCase(lang);}
function jrMatchesGroup(record,group) {
  if(!journalIsClosed(record) || record.side !== group.side || !journalMatchesContext(record,group.context,group.basis)) return false;
  const value=group.dimension === "engine" ? journalEngineValue(record) : journalAnnotationAt(record,group.basis)?.criteria?.[group.dimension] || "unassessed";
  const origin=journalGroupOrigin(record,group.dimension,group.basis,group.context);
  return value === group.value && origin === group.origin;
}
function jrTradeRecords() {
  const filter=$("journalFilter").value,side=$("journalSide").value,search=jrSearchText($("journalSearch").value.trim());
  return journalData.filter(record=>{
    if(side !== "all" && record.side !== side || journalCohort && !jrMatchesGroup(record,journalCohort)) return false;
    if(filter === "wins" && !(record.outcome?.pnl > 0) || filter === "losses" && !(record.outcome?.pnl < 0) ||
       filter === "open" && !["open","pending"].includes(record.status) || filter === "unfilled" && record.status !== "unfilled" ||
       filter === "proposed" && !proposedOnSide(record) || filter === "not_proposed" && proposedOnSide(record)) return false;
    const annotation=journalAnnotationAt(record,"latest");
    const searchable=[record.decided_at,jrSide(record.side),annotation?.note || "",...Object.entries(annotation?.criteria || {}).map(([key,value])=>criterionLabel(record,key,value))].join(" ");
    return !search || jrSearchText(searchable).includes(search);
  }).slice().reverse();
}
function jrActivate(view,focus=false) {
  journalView=view;
  if($("jrFocusDialog")?.open) $("jrFocusDialog").scrollTop=0;
  for(const name of ["trades","patterns"]) {
    const label=name === "trades" ? "Trades" : "Patterns";
    const selected=name === view,button=$("jr"+label+"Tab");
    button.setAttribute("aria-selected",String(selected));button.tabIndex=selected ? 0 : -1;
    $("jr"+label+"Panel").hidden=!selected;
    if(selected && focus) button.focus();
  }
  renderJournalRows();
}
function renderJournalRows() {
  if(journalView === "patterns") renderJournalPatterns(); else renderJournalTrades();
}
function renderJournalTrades() {
  const records=jrTradeRecords();
  if(journalOpenRecord) {
    const index=records.findIndex(record=>record.id === journalOpenRecord);
    if(index >= 0) journalPage=Math.floor(index/journalPageSize);
  }
  journalPage=Math.max(0,Math.min(journalPage,Math.ceil(records.length/journalPageSize)-1));
  const first=journalPage*journalPageSize,last=Math.min(first+journalPageSize,records.length);
  const closed=records.filter(journalIsClosed).length;
  $("journalSummary").textContent=jrFormat("jrListSummary",{count:records.length,closed,other:records.length-closed});
  $("jrPageLabel").textContent=jrFormat("jrShowing",{first:records.length ? first+1 : 0,last,total:records.length});
  $("jrPrev").disabled=journalPage === 0;$("jrNext").disabled=last >= records.length;
  const banner=$("jrCohortBanner");banner.hidden=!journalCohort;banner.replaceChildren();
  if(journalCohort) {
    const members=journalData.filter(record=>jrMatchesGroup(record,journalCohort)),metrics=journalMetrics(members);
    const sentence=jrFormat("jrReading",{side:jrSide(journalCohort.side),condition:jrDimension(journalCohort.dimension)+" · "+jrCondition(journalCohort.dimension,journalCohort.value)+(journalCohort.context?.dimension ? " · "+jrDimension(journalCohort.context.dimension)+": "+jrCondition(journalCohort.context.dimension,journalCohort.context.value) : ""),
      relation:t("jrRelation_"+journalCohort.relation),...jrOutcomeText(metrics),n:metrics.n,mean:metrics.meanR == null ? "—" : new Intl.NumberFormat(lang,{maximumFractionDigits:4}).format(metrics.meanR),nr:metrics.nR});
    const text=jrNode("div",null);text.append(jrNode("strong",null,sentence),jrNode("small",null,jrOrigin(journalCohort.origin)));
    const clear=jrNode("button",null,t("jrClearGroup"));clear.type="button";clear.onclick=()=>{journalCohort=null;journalPage=0;renderJournalRows();};
    banner.append(text,clear);
  }
  $("journalEntries").replaceChildren(...(records.length ? records.slice(first,last).map(jrTradeRow) : [jrNode("p","jr-empty",t(journalData.length ? "jrNoMatches" : "journalEmpty"))]));
}
function jrTradeRow(record) {
  const annotation=journalAnnotationAt(record,"latest"),details=jrNode("details","jr-trade"),summary=jrNode("summary","jr-trade-row");
  details.dataset.journalId=record.id;details.open=journalOpenRecord === record.id;
  const at=new Date(record.decided_at+( /(?:Z|[+-]\d\d:\d\d)$/.test(record.decided_at) ? "" : "Z"));
  const date=jrNode("span","jr-date");date.append(jrNode("strong",null,new Intl.DateTimeFormat(lang,{day:"2-digit",month:"short",year:"numeric",timeZone:"UTC"}).format(at)),jrNode("small",null,new Intl.DateTimeFormat(lang,{hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"UTC"}).format(at)+" UTC"));
  const side=jrNode("span","jr-trade-side");side.append(jrNode("b","jr-side "+(record.side === "BUY" ? "jr-long" : "jr-short"),jrSide(record.side)),jrNode("small",null,"× "+record.quantity));
  const setup=jrNode("span","jr-setup",annotation.criteria.setup ? jrCondition("setup",annotation.criteria.setup) : "—");setup.title=t("journal_setup");
  const engine=jrNode("span","jr-engine-chip",jrCondition("engine",journalEngineValue(record)));engine.title=journalDecision(record);
  const result=jrNode("span","jr-result");
  if(record.outcome) {
    result.append(jrNode("b",record.outcome.pnl>0 ? "positive" : record.outcome.pnl<0 ? "negative" : "",fmt(record.outcome.pnl)+" "+state.profile.currency),jrNode("small",null,record.outcome.r == null ? t("jrRiskMissing") : jrR(record.outcome.r)));
  } else result.append(jrNode("small",null,t("journalStatus_"+record.status)));
  const count=Object.keys(annotation.criteria).length;
  const notes=jrNode("span","jr-note-count");notes.append(jrNode("span",null,count+" "+t("jrCriteriaShort")),jrNode("small",null,t(annotation.note ? "jrNotePresent" : "journalNoNote")));notes.title=t("jrCriteriaCount");
  summary.append(date,side,setup,engine,result,notes,jrNode("span","jr-chevron","⌄"));
  details.append(summary);
  const body=jrNode("div","jr-trade-body");details.append(body);
  const build=()=>{
    if(body.childElementCount) return;
    const noteColumn=jrNode("div","jr-note-column"),noteHeading=jrNode("div","jr-detail-heading");
    noteHeading.append(jrNode("h3",null,t("jrYourReading")),jrNode("span","jr-origin",jrOrigin(journalAnnotationOrigin(record,"latest"))));
    const note=jrNode("p","jr-note-text",annotation.note || t("journalNoNote"));
    const engineHeading=jrNode("h3",null,t("journalAtOrder")),engineText=jrNode("p","jr-engine-explanation",journalDecision(record));
    const edit=jrNode("button",null,t(annotation.note || count ? "journalEdit" : "journalComplete"));edit.type="button";edit.onclick=()=>openJournalEditor(record);
    noteColumn.append(noteHeading,note,engineHeading,engineText,edit);
    const criteria=jrNode("div","jr-criteria-column");criteria.append(jrNode("h3",null,t("journalCriteria")));
    const list=jrNode("dl","jr-criteria-list");
    for(const [key,value] of Object.entries(annotation.criteria)) {
      const row=jrNode("div",null),term=jrNode("dt",null,jrDimension(key)),description=jrNode("dd",null,jrCondition(key,value));
      const relation=journalCriterionRelation(key,value,record.side);
      if(relation !== "nondirectional") description.append(jrNode("small","jr-relation jr-rel-"+relation,t("jrRelation_"+relation)));
      row.append(term,description);list.append(row);
    }
    criteria.append(count ? list : jrNode("p","jr-empty",t("journalUnset")));
    const raw=jrNode("details","jr-raw"),rawSummary=jrNode("summary",null,t("jrTechnicalData"));raw.append(rawSummary);
    raw.ontoggle=()=>{if(raw.open && raw.childElementCount === 1) raw.append(jrNode("pre",null,JSON.stringify(record,null,2)));};
    body.append(noteColumn,criteria,raw);
  };
  if(details.open) build();
  details.ontoggle=()=>{
    if(details.open) {journalOpenRecord=record.id;build();for(const other of $("journalEntries").querySelectorAll(".jr-trade")) if(other !== details) other.open=false;}
    else if(journalOpenRecord === record.id) journalOpenRecord=null;
  };
  return details;
}
function renderJournalPatterns() {
  const dimension=$("jrDimension").value || "divergence_m5",basis=$("jrBasis").value,side=$("jrPatternSide").value;
  const context={dimension:$("jrContextDimension").value,value:$("jrContextValue").value};
  const population=journalData.filter(record=>journalMatchesContext(record,context,basis));
  const closed=population.filter(record=>journalIsClosed(record) && (side === "all" || record.side === side));
  const metrics=journalMetrics(closed),assessed=closed.filter(record=>dimension === "engine" ? !!record.engine_context?.assessment : !!journalAnnotationAt(record,basis)?.criteria?.[dimension]).length;
  const cards=[["jrClosedTrades",metrics.n,t("jrBothSidesSeparate")],["jrWinRate",jrRate(metrics.winRate),jrResultBreakdown(metrics)],["journalMeanR",jrR(metrics.meanR),jrFormat("jrRCoverage",{n:metrics.nR,total:metrics.n})],["jrAssessed",assessed+" / "+metrics.n,t(basis === "entry" ? "jrAtEntry" : "jrLatest")]];
  $("jrPatternSummary").replaceChildren(...cards.map(([label,value,caption])=>{const card=jrNode("div","jr-stat");card.append(jrNode("small",null,t(label)),jrNode("strong",null,String(value)),jrNode("span",null,caption));return card;}));
  const groups=journalPatternGroups(population,dimension,basis,side,context);
  if(!groups.length) {$("journalPatterns").replaceChildren(jrNode("p","jr-empty",t("jrNoClosed")));return;}
  const table=jrNode("table","jr-patterns"),head=jrNode("thead",null),heading=jrNode("tr",null);
  for(const key of ["jrDirection","jrCondition","jrSample","jrWinLoss","jrWinRate","journalMeanR","jrCases"]) heading.append(jrNode("th",null,t(key)));
  head.append(heading);const tbody=jrNode("tbody",null);
  for(const group of groups) {
    const row=jrNode("tr",null),sideCell=jrNode("td",null);sideCell.append(jrNode("b","jr-side "+(group.side === "BUY" ? "jr-long" : "jr-short"),jrSide(group.side)));
    const condition=jrNode("td","jr-pattern-condition");condition.append(jrNode("strong",null,jrCondition(dimension,group.value)));
    const badges=jrNode("div","jr-pattern-badges");badges.append(jrNode("span","jr-relation jr-rel-"+group.relation,t("jrRelation_"+group.relation)),jrNode("small","jr-origin",jrOrigin(group.origin)));condition.append(badges);
    const sample=jrNode("td","jr-sample",String(group.n));sample.title=jrFormat("jrRCoverage",{n:group.nR,total:group.n});
    const winloss=jrNode("td",null);winloss.append(jrNode("span","positive",String(group.wins)),document.createTextNode(" / "),jrNode("span","negative",String(group.losses)));winloss.title=jrResultBreakdown(group);if(group.flat) winloss.append(jrNode("small","jr-r-count",group.flat+" "+t(group.flat===1 ? "jrFlat" : "jrFlats")));
    const mean=jrNode("td",group.meanR>0 ? "positive" : group.meanR<0 ? "negative" : "",jrR(group.meanR));mean.append(jrNode("small","jr-r-count",group.nR+" / "+group.n+" "+t("journalRCount")));
    const action=jrNode("td",null),button=jrNode("button",null,t("jrSeeTrades"));button.type="button";button.setAttribute("aria-label",t("jrSeeTrades")+" · "+jrSide(group.side)+" · "+jrCondition(dimension,group.value));
    button.onclick=()=>{journalCohort={...group,context:context.dimension && context.value ? context : null};journalPage=0;journalOpenRecord=null;$("journalSide").value="all";$("journalFilter").value="all";$("journalSearch").value="";jrActivate("trades",true);};action.append(button);
    row.append(sideCell,condition,sample,winloss,jrNode("td",null,jrRate(group.winRate)),mean,action);tbody.append(row);
  }
  table.append(head,tbody);$("journalPatterns").replaceChildren(table);
}
function initJournalViews() {
  $("jrExpand").onclick=toggleJournalFocus;
  $("jrContextDimension").replaceChildren(new Option(t("jrNoContext"),""),...journalDimensions.filter(key=>key!=="engine").map(key=>new Option(jrDimension(key),key)));
  $("jrContextDimension").onchange=()=>{jrContextOptions();renderJournalPatterns();};
  $("jrContextValue").onchange=renderJournalPatterns;
  $("jrContextClear").onclick=()=>{$("jrContextDimension").value="";jrContextOptions();renderJournalPatterns();};
  jrContextOptions();
  $("jrDimension").replaceChildren(...journalDimensions.map(key=>new Option(jrDimension(key),key)));
  for(const [id,view] of [["jrTradesTab","trades"],["jrPatternsTab","patterns"]]) {
    $(id).onclick=()=>jrActivate(view);
    $(id).onkeydown=event=>{if(["ArrowLeft","ArrowRight"].includes(event.key)) {event.preventDefault();jrActivate(view === "trades" ? "patterns" : "trades",true);}};
  }
  const reset=()=>{journalPage=0;journalOpenRecord=null;journalCohort=null;renderJournalRows();};
  $("journalFilter").onchange=reset;$("journalSide").onchange=reset;$("journalSearch").oninput=reset;
  for(const id of ["jrDimension","jrPatternSide","jrBasis"]) $(id).onchange=()=>renderJournalPatterns();
  $("jrPrev").onclick=()=>{journalOpenRecord=null;journalPage--;renderJournalRows();if($("jrFocusDialog")?.open) $("jrFocusDialog").scrollTop=0;};
  $("jrNext").onclick=()=>{journalOpenRecord=null;journalPage++;renderJournalRows();if($("jrFocusDialog")?.open) $("jrFocusDialog").scrollTop=0;};
}

function initJournal() {
  initJournalViews();
  $("journalAutofill").onclick=()=>autofillJournal(false);
  $("journalAutofill").title=t("journalAutoRule");
  $("journalDetails").ontoggle=()=>{if($("journalDetails").open) loadJournal();};
  $("journalRefresh").onclick=()=>loadJournal();

  $("journalExport").onclick=async()=>{
    try {const data=await api("journal");download({version:1,profile:state.profile,...data},"manual-journal.json");}
    catch {$("journalSummary").textContent=t("journalLoadError");}
  };
}

function journalFieldNodes(catalog, criteria, prefix, disabled=false) {
  return catalog.fields.map(field=>{
    const label=document.createElement("label");label.textContent=t(field.label_key);
    const select=document.createElement("select");select.id=prefix+field.id;select.dataset.criterion=field.id;
    label.htmlFor=select.id;
    select.append(new Option(t("journalUnset"),""),...field.options.map(option=>new Option(t(option.label_key),option.id)));
    select.value=criteria[field.id] || "";select.disabled=disabled;label.append(select);return label;
  });
}
function openJournalEditor(record) {
  if(!$("journalEditDialog")) {
    const dialog=document.createElement("dialog");dialog.id="journalEditDialog";dialog.className="journal-edit-dialog";dialog.setAttribute("aria-labelledby","journalEditTitle");
    dialog.addEventListener("cancel",event=>{if($("journalEditSave").disabled) event.preventDefault();});
    const form=document.createElement("form");form.id="journalEditForm";
    const heading=document.createElement("h3");heading.id="journalEditTitle";
    const hint=document.createElement("p");hint.id="journalEditHint";hint.className="ticket-note";
    const autofill=document.createElement("button");autofill.id="journalEditAutofill";autofill.type="button";autofill.onclick=()=>autofillJournal(true);
    const autoStatus=document.createElement("p");autoStatus.id="journalEditAutoStatus";autoStatus.className="ticket-note";autoStatus.setAttribute("role","status");
    const fields=document.createElement("div");fields.id="journalEditFields";fields.className="journal-fields";
    const label=document.createElement("label");label.htmlFor="journalEditNote";label.id="journalEditNoteLabel";
    const note=document.createElement("textarea");note.id="journalEditNote";note.maxLength=2000;note.rows=3;
    const status=document.createElement("p");status.id="journalEditStatus";status.setAttribute("role","status");
    const actions=document.createElement("div");actions.className="journal-edit-actions";
    const save=document.createElement("button");save.id="journalEditSave";save.type="submit";
    const cancel=document.createElement("button");cancel.id="journalEditCancel";cancel.type="button";cancel.onclick=()=>dialog.close();
    actions.append(save,cancel);form.append(heading,hint,autofill,autoStatus,fields,label,note,status,actions);dialog.append(form);document.body.append(dialog);
    form.onsubmit=saveJournalEdit;
  }
  journalEditing={id:record.id,revision:record.revisions?.length || 0};
  journalAutoNotes.edit=null;
  $("journalEditAutofill").textContent=t("journalAutofill");
  $("journalEditAutofill").title=t("journalAutoRule");
  $("journalEditDialog").dataset.journalId=record.id;
  $("journalEditAutofill").disabled=false;$("journalEditAutoStatus").textContent="";
  const annotation=record.annotation || record.manual;
  $("journalEditTitle").textContent=t("journalEdit");$("journalEditHint").textContent=t("journalEditHint");
  $("journalEditNoteLabel").textContent=t("journalNote");
  $("journalEditFields").replaceChildren(...journalFieldNodes(record.catalog,annotation.criteria,"journalEditCriterion_"));
  $("journalEditNote").value=annotation.note;$("journalEditStatus").textContent="";
  $("journalEditSave").textContent=t("journalSaveChanges");$("journalEditSave").disabled=false;
  $("journalEditCancel").textContent=t("journalCancel");$("journalEditDialog").showModal();
}
async function saveJournalEdit(event) {
  event.preventDefault();if($("journalEditSave").disabled) return;
  $("journalEditSave").disabled=true;$("journalEditCancel").disabled=true;$("journalEditStatus").textContent="";
  const journal={note:$("journalEditNote").value,criteria:Object.fromEntries([...$("journalEditFields").querySelectorAll("select")].filter(node=>node.value).map(node=>[node.dataset.criterion,node.value]))};
  try {
    const result=await api("journal_edit",{journal_id:journalEditing.id,journal,expected_journal_revision:journalEditing.revision});
    journalData=result.journal;$("journalStorageWarning").hidden=!result.journal_storage_error;
    $("journalCount").textContent=journalData.length;renderJournalRows();$("journalEditDialog").close();
    const editedRow=[...$("journalEntries").querySelectorAll(".jr-trade")].find(row=>row.dataset.journalId===journalEditing.id);
    (editedRow?.querySelector("summary") || $("jrTradesTab")).focus();
  } catch(error) { $("journalEditStatus").textContent=t(error.message) === error.message ? t("journalEditError") : t(error.message); }
  finally {$("journalEditSave").disabled=false;$("journalEditCancel").disabled=false;}
}

async function autofillJournal(editing) {
  const button=$(editing ? "journalEditAutofill" : "journalAutofill");
  const status=$(editing ? "journalEditAutoStatus" : "journalAutoStatus");
  const fields=$(editing ? "journalEditFields" : "journalFields");
  const note=$(editing ? "journalEditNote" : "journalNote");
  if(button.disabled) return;
  button.disabled=true;status.textContent=t("journalAutoLoading");
  if(editing) {$("journalEditSave").disabled=true;$("journalEditCancel").disabled=true;}
  try {
    if(!editing) {
      if(chartBrowseDate != null) {status.textContent=t("historicalView");return;}
      halt();await act("pause");
    }
    const result=await api("journal_autofill",editing ? {journal_id:journalEditing.id} : {side:$("side").value});
    let count=0;
    for(const [key,value] of Object.entries(result.criteria)) {
      const select=fields.querySelector('[data-criterion="'+key+'"]');
      if(select && [...select.options].some(option=>option.value===value)) {select.value=value;count++;}
    }
    if(!result.as_of || !count) {status.textContent=t("journalAutoUnavailable");return;}
    const values=result.values || {};
    const quant=value=>new Intl.NumberFormat(lang,{maximumFractionDigits:6}).format(value);
    const labels={roc:"ROC",roc_delta:"ΔROC",obv_flow:t("flowOBV"),obv_delta:"ΔOBV",atr_points:"ATR",atr_raw_change_ratio:"ΔATR"};
    const metrics=Object.entries(labels).filter(([key])=>Number.isFinite(values[key])).map(([key,label])=>label+": "+(key==="atr_raw_change_ratio" ? quant(values[key]*100)+"%" : quant(values[key]))).join(" · ");
    const summary=t("journalAutoSource")+" "+result.as_of.replace("T"," ")+" UTC\n"+metrics;
    const slot=editing ? "edit" : "entry";
    if(!note.value.trim() || note.value===journalAutoNotes[slot]) note.value=summary;
    journalAutoNotes[slot]=summary;
    status.textContent=t("journalAutoFilled")+" "+count+" · "+summary;
  } catch {status.textContent=t("journalAutoError");}
  finally {
    button.disabled=false;
    if(editing) {$("journalEditSave").disabled=false;$("journalEditCancel").disabled=false;}
  }
}

function toggleJournalFocus() {
  let dialog=$("jrFocusDialog");
  if(dialog?.open) {dialog.close();return;}
  if(!dialog) {
    dialog=jrNode("dialog","jr-focus-dialog");dialog.id="jrFocusDialog";dialog.setAttribute("aria-labelledby","jrWorkspaceTitle");
    document.body.append(dialog);
    dialog.addEventListener("close",()=>{
      const placeholder=$("jrFocusPlaceholder");
      if(placeholder) {placeholder.replaceWith($("journalDetails"));$("jrExpand").textContent=t("jrExpand");$("jrExpand").focus();}
    });
  }
  const placeholder=document.createElement("span");placeholder.id="jrFocusPlaceholder";
  $("journalDetails").before(placeholder);dialog.append($("journalDetails"));$("journalDetails").open=true;
  $("jrExpand").textContent=t("jrBackCharts");dialog.showModal();
}

function jrContextOptions() {
  const dimension=$("jrContextDimension").value;
  const options=state?.journal_catalog?.fields.find(field=>field.id===dimension)?.options || [];
  $("jrContextValue").replaceChildren(new Option(t("jrAnyValue"),""),...options.map(option=>new Option(t(option.label_key),option.id)));
  $("jrContextValue").disabled=!dimension;
}

function journalExportContent(records) {
  const section=jrNode("section",null);section.append(jrNode("h2",null,t("journalTitle")));
  for(const record of records || []) {
    const annotation=journalAnnotationAt(record,"latest"),entry=jrNode("details",null),summary=jrNode("summary",null);
    summary.textContent=record.decided_at.replace("T"," ")+" UTC · "+jrSide(record.side)+" × "+record.quantity+" · "+t("journalStatus_"+record.status)+(record.outcome ? " · "+fmt(record.outcome.pnl)+" "+state.profile.currency+" · "+jrR(record.outcome.r) : "");
    entry.append(summary,jrNode("p",null,t("journalAtOrder")+": "+journalDecision(record)),jrNode("p",null,jrOrigin(journalAnnotationOrigin(record,"latest"))));
    const note=jrNode("p",null,annotation.note || t("journalNoNote"));note.style.whiteSpace="pre-wrap";entry.append(note);
    const list=jrNode("ul",null);
    for(const [key,value] of Object.entries(annotation.criteria)) {
      const relation=journalCriterionRelation(key,value,record.side);
      list.append(jrNode("li",null,criterionLabel(record,key,value)+(relation !== "nondirectional" ? " · "+t("jrRelation_"+relation) : "")));
    }
    entry.append(list);
    const raw=jrNode("details",null);raw.append(jrNode("summary",null,t("jrTechnicalData")),jrNode("pre",null,JSON.stringify(record,null,2)));entry.append(raw);section.append(entry);
  }
  return section;
}
