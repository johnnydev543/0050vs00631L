'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {create}=require('../web/chart_model.js');
const cases=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
let vertices=0,models=0;
for(const test of cases)for(const width of [320,800,1200])for(const scale of ['linear','log'])for(const key of ['nav','exposure']){
  const model=create(test.list,{width,scale,key,principal:test.principal,target:test.exposure});models++;
  for(let s=0;s<test.list.length;s++)for(let i=0;i<model.times.length;i++){
    const row=test.list[s].result.daily[i],expected=key==='nav'?row.nav/10000:row.nominal_market_exposure*100;
    const [px,py]=model.points[s][i];
    assert.ok(Math.abs(model.inverseY(py)-expected)<=Math.max(1,expected)*1e-12);
    assert.equal(model.nearest(px),i);
    assert.ok(px>=model.left-1e-9&&px<=width-model.right+1e-9);
    assert.ok(py>=model.top-1e-9&&py<=model.height-model.bottom+1e-9);
    const reconstructed=model.inverseY(Number(py.toFixed(6)));
    assert.ok(Math.abs(reconstructed-expected)<=Math.max(1,expected)*.0001);
    vertices++;
  }
  assert.equal(model.dates[0],'2014-10-31');assert.equal(model.dates.at(-1),'2026-10-08');
  assert.equal(model.points[0][0][0],model.left);
  assert.equal(model.points[0].at(-1)[0],width-model.right);
}
const mismatched=structuredClone(cases[0].list);mismatched[1].result.daily[10].date='2014-11-30';
assert.throws(()=>create(mismatched),/日期不一致/);
const duplicate=structuredClone(cases[0].list);duplicate[0].result.daily[10].date=duplicate[0].result.daily[9].date;
assert.throws(()=>create(duplicate),/日期必須依序/);
const invalid=structuredClone(cases[0].list);invalid[0].result.daily[0].nav=NaN;
assert.throws(()=>create(invalid),/數值無效/);
console.log(JSON.stringify({status:'PASS',cases:cases.length,models,verified_vertices:vertices,
  checks:'All dated vertices round-trip to real NAV/exposure, nearest-date lookup, plot bounds, SVG rounding, linear/log, widths, invalid alignment/values.'}));
