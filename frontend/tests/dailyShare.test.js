import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sharePages, shareCell } from '../src/dailyShare.js';

test('sharing includes all date rows and splits long days without losing matches',()=>{
  const data={day:'2026-11-05',rows:Array.from({length:25},(_,id)=>({id}))};
  const pages=sharePages(data);
  assert.deepEqual(pages.map(p=>p.rows.length),[12,12,1]);
  assert.deepEqual(pages.flatMap(p=>p.rows),data.rows);
  assert.equal(pages[2].index,3);assert.equal(pages[2].total,3);
});
test('sharing preserves missing choices and separate exploration results',()=>{
  const row={markets:[{code:'crs',selection:'1-1',actual:'3-1',hit:false}],
    forecast:{explorations:[{code:'crs',selection:'3-1',coverage:12}]},exploration_results:[{code:'crs',hit:true}]};
  const frozen=JSON.stringify(row), view=shareCell(row,'crs');
  assert.equal(view.prediction,'1-1');assert.equal(view.actual,'赛果 3-1 · 未命中');
  assert.equal(view.extra,'进取 3-1 (12.0%) · 命中');
  assert.equal(shareCell(row,'hafu').prediction,'未记录');
  assert.equal(JSON.stringify(row),frozen);
});
