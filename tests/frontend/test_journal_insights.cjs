const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../src/btframework/replay/static/journal.js'),'utf8'),context);
const plain=value=>JSON.parse(JSON.stringify(value));
function record(id,side,value,pnl,r){return {id,side,manual:{note:'',criteria:{divergence_m5:value}},outcome:{pnl,r},engine_context:{assessment:{decision:'reject',direction:1}}};}
const records=[record('a','BUY','bearish',10,1),record('b','BUY','bearish',-10,-1),record('c','SELL','bearish',20,2),record('d','SELL','bullish',-10,-1),record('e','BUY','bearish',0,null),{...record('pending','BUY','bearish',10,1),outcome:null}];
test('bearish divergence is split into opposing LONGs and aligned SHORTs with correct denominators',()=>{
 const groups=plain(context.journalPatternGroups(records,'divergence_m5','entry'));
 const long=groups.find(g=>g.side==='BUY'),short=groups.find(g=>g.side==='SELL'&&g.value==='bearish');
 assert.equal(long.relation,'opposing');assert.equal(long.n,3);assert.equal(long.wins,1);assert.equal(long.losses,1);assert.equal(long.flat,1);assert.equal(long.winRate,1/3);assert.equal(long.nR,2);assert.equal(long.meanR,0);
 assert.equal(short.relation,'aligned');assert.equal(short.n,1);assert.equal(short.meanR,2);
 assert.equal(groups.reduce((sum,g)=>sum+g.n,0),5);
});
test('late edits do not rewrite entry-time cohorts or merge annotation origins',()=>{
 const changed=JSON.parse(JSON.stringify(records));
 changed[0].revisions=[{manual:{criteria:{divergence_m5:'bullish'}}}];
 changed[0].annotation={note:'late',criteria:{divergence_m5:'bullish'}};
 const initial=plain(context.journalPatternGroups(changed,'divergence_m5','entry'));
 assert.equal(initial.find(g=>g.side==='BUY').n,3);
 const latest=plain(context.journalPatternGroups(changed,'divergence_m5','latest'));
 const late=latest.find(g=>g.origin==='late');assert.equal(late.value,'bullish');assert.equal(late.relation,'aligned');assert.equal(late.n,1);
 assert.equal(latest.find(g=>g.side==='BUY'&&g.origin==='entry').n,2);
});
test('missing risk remains missing, pending orders are excluded, and ATR is not directional',()=>{
 const result=plain(context.journalMetrics([{outcome:{pnl:3,r:null}},{outcome:{pnl:-2,r:null}},{outcome:null}]));
 assert.equal(result.n,2);assert.equal(result.nR,0);assert.equal(result.meanR,null);
 assert.equal(context.journalCriterionRelation('atr','expanding','BUY'),'nondirectional');
 assert.equal(context.journalCriterionRelation('atr','expanding','SELL'),'nondirectional');
});
test('drilling into a cohort returns exactly the native closed cases in that cohort',()=>{
 for(const group of context.journalPatternGroups(records,'divergence_m5','entry')) {
  const matches=records.filter(record=>context.jrMatchesGroup(record,group));
  assert.equal(matches.length,group.n);assert.deepEqual(matches.map(r=>r.id).sort(),plain(group.records).map(r=>r.id).sort());
 }
});
test('unknown labels and missing snapshots are explicit groups, not negatives',()=>{
 const missing=record('missing','BUY',undefined,5,.5);missing.manual.criteria={};missing.engine_context=null;
 const groups=plain(context.journalPatternGroups([missing],'divergence_m5','entry'));
 assert.equal(groups[0].value,'unassessed');assert.equal(groups[0].relation,'unassessed');
 const engine=plain(context.journalPatternGroups([missing],'engine','entry'));
 assert.equal(engine[0].value,'unavailable');assert.equal(engine[0].origin,'missing');
});
test('additional context conditions both cohorts and drilled cases without mixing annotation times',()=>{
 const set=JSON.parse(JSON.stringify(records));
 set[0].manual.criteria.roc='favor_accelerating';set[1].manual.criteria.roc='opposing';set[2].manual.criteria.roc='favor_accelerating';
 const filter={dimension:'roc',value:'favor_accelerating'};
 const selected=set.filter(record=>context.journalMatchesContext(record,filter,'entry'));
 assert.deepEqual(selected.map(r=>r.id),['a','c']);
 const groups=context.journalPatternGroups(selected,'divergence_m5','entry');
 for(const group of groups) assert.equal(set.filter(record=>context.jrMatchesGroup(record,{...group,context:filter})).length,group.n);
 set[0].revisions=[{manual:{criteria:{divergence_m5:'bearish',roc:'opposing'}}}];set[0].annotation=set[0].revisions[0].manual;
 assert.equal(context.journalMatchesContext(set[0],filter,'entry'),true);
 assert.equal(context.journalMatchesContext(set[0],filter,'latest'),false);
});
test('editing only a note does not relabel unchanged entry-time criteria as retrospective',()=>{
 const row=record('a','BUY','bearish',10,1);row.manual.criteria.roc='opposing';
 row.revisions=[{manual:{note:'later note',criteria:{divergence_m5:'bearish',roc:'favor_accelerating'}}}];row.annotation=row.revisions[0].manual;
 assert.equal(context.journalCriterionOrigin(row,'divergence_m5','latest'),'entry');
 assert.equal(context.journalCriterionOrigin(row,'roc','latest'),'late');
 const filter={dimension:'roc',value:'favor_accelerating'};
 const groups=context.journalPatternGroups([row],'divergence_m5','latest','all',filter);
 assert.equal(groups[0].origin,'late');
 assert.equal(context.jrMatchesGroup(row,{...groups[0],context:filter}),true);
});
