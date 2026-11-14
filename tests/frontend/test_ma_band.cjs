const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = {};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../src/btframework/replay/static/tools.js'),'utf8'),context);
const regions = (rows, minutes=1) => JSON.parse(JSON.stringify(context.maBandRegions(rows,minutes)));
const rows = pairs => pairs.map(([ma9,ma20],index) => ({index,time:index*60,ma9,ma20}));
const near = (a,b) => assert.ok(Math.abs(a-b)<1e-9,`${a} != ${b}`);

test('crossings close both boundaries at their common intersection',()=>{
  const result = regions(rows([[12,10],[8,10],[13,10],[7,10]]));
  assert.equal(result.length,4);
  for(let i=1;i<result.length;i++){
    const left=result[i-1].points.at(-1),right=result[i].points[0];
    near(left.x,right.x);near(left.fast,left.slow);near(left.fast,right.fast);
    assert.notEqual(result[i-1].bull,result[i].bull);
  }
});

test('missing averages, missing bars and flat averages never create connecting regions',()=>{
  const input=rows([[12,10],[13,10],[null,null],[14,10],[15,10],[16,10],[17,10]]);
  input[5].time+=600;input[6].time+=600;
  const result=regions(input);
  assert.deepEqual(result.map(r=>r.points.map(p=>p.x)),[[0,1],[3,4],[5,6]]);
  assert.deepEqual(regions(rows([[10,10],[10,10]])),[]);
  assert.deepEqual(regions(rows([[null,10],[NaN,10]])),[]);
});

test('every filled edge stays on the original piecewise-linear MA envelope',()=>{
  const input=rows(Array.from({length:401},(_,i)=>[100+Math.sin(i/3)*8+Math.sin(i/7),100+Math.cos(i/5)*6]));
  const result=regions(input);
  assert.ok(result.length>30);
  for(const region of result){
    assert.ok(region.points.length>=2);
    for(let j=1;j<region.points.length;j++){
      const a=region.points[j-1],b=region.points[j];
      assert.ok(b.x>a.x);
      for(const t of [0,.25,.5,.75,1]){
        const x=a.x+(b.x-a.x)*t;
        const i=Math.min(input.length-2,Math.floor(x));
        const fraction=x-i;
        const fast=input[i].ma9+fraction*(input[i+1].ma9-input[i].ma9);
        const slow=input[i].ma20+fraction*(input[i+1].ma20-input[i].ma20);
        near(a.fast+t*(b.fast-a.fast),fast);
        near(a.slow+t*(b.slow-a.slow),slow);
        assert.ok(region.bull ? fast>=slow-1e-9 : fast<=slow+1e-9);
      }
    }
  }
});

test('M5 continuity uses its own interval and preserves the input MA values',()=>{
  const input=rows([[10,12],[11,12],[13,12],[14,12]]).map(r=>({...r,time:r.time*5}));
  const copy=JSON.stringify(input);
  assert.equal(regions(input,5).length,2);
  assert.equal(JSON.stringify(input),copy);
});
