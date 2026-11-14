const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const path=require('node:path');
const context=vm.createContext({state:{cursor:1,total:20,now:'2025-01-01T15:05:00',market_price:100},placingOrder:false,chartBrowseDate:null});
vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../src/btframework/replay/static/ticket.js'),'utf8'),context);
const model={decision:'propose',direction:1,proposal:{known_at:'2025-01-01T15:05:00',entry:99,sl:98,tp_estimate:101}};
test('buy and sell proposals remain passive and protect the entry',()=>{
 assert.equal(context.engineLimitBlock(model),null);
 assert.equal(context.engineLimitBlock({...model,direction:-1,proposal:{...model.proposal,entry:101,sl:102,tp_estimate:99}}),null);
 assert.equal(context.engineLimitBlock({...model,proposal:{...model.proposal,entry:101,tp_estimate:102}}),'invalid_limit');
 assert.equal(context.engineLimitBlock({...model,proposal:{...model.proposal,sl:100}}),'invalid_bracket');
});
test('historical, occupied, old, future and nonexecutable proposals are blocked',()=>{
 assert.equal(context.engineLimitBlock({...model,decision:'review'}),'engineLimitExpired');
 assert.equal(context.engineLimitBlock({...model,proposal:{...model.proposal,known_at:'2025-01-01T15:00:00'}}),'engineLimitExpired');
 assert.equal(context.engineLimitBlock({...model,proposal:{...model.proposal,known_at:'2025-01-01T15:06:00'}}),'engineLimitExpired');
 context.chartBrowseDate=1;assert.equal(context.engineLimitBlock(model),'historicalView');context.chartBrowseDate=null;
 context.state.position={};assert.equal(context.engineLimitBlock(model),'occupied');context.state.position=null;
 context.state.cursor=0;assert.equal(context.engineLimitBlock(model),'no_executable_bar');
});
