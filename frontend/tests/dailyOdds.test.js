import { test } from 'node:test';
import assert from 'node:assert/strict';
import { selectedOdds } from '../src/dailyOdds.js';
import { shareCell } from '../src/dailyShare.js';

test('selected odds preserve key mapping and selection order across all five markets', () => {
  for (const [code, keys, labels] of [
    ['had',['a'],['客胜']], ['hhad',['d'],['让平']], ['hafu',['dh'],['平胜']],
    ['crs',['s02s01','s03s01'],['2-1','3-1']], ['ttg',['s4','s7'],['4球','7+球']],
  ]) {
    const market={code,line:-1,selected_keys:keys,options:keys.map((key,i)=>({key,label:labels[i],odds:2.5+i})).reverse()};
    const original=JSON.stringify(market);
    const expected=labels.map((label,i)=>`${label} @ ${(2.5+i).toFixed(2)}`).join(' / ');
    assert.equal(selectedOdds(market),expected);
    assert.equal(shareCell({current_quotes:{markets:{[code]:market}},forecast:{market_assessments:[market],explorations:[{...market,coverage:12,selection:labels[0]}]}},code).odds,'最新SP '+expected);
    assert.equal(JSON.stringify(market),original);
  }
});

test('missing and invalid archived odds never become implied or live odds', () => {
  assert.equal(selectedOdds({selection:'观望',selected_keys:[],options:[{key:'h',odds:2}]}),'');
  for(const odds of [null,undefined,0,1,-2,NaN,Infinity,'2.50']) {
    assert.equal(selectedOdds({selected_keys:['h'],selection:'主胜',options:[{key:'h',label:'主胜',odds}]}),'主胜 @ 未留存');
  }
  assert.equal(selectedOdds({selected_keys:['h'],selection:'主胜'}),'主胜 @ 未留存');
  assert.equal(selectedOdds({selected_keys:['h'],selection:'主胜',options:[{key:'h',odds:2},{key:'h',odds:3}]}),'主胜 @ 未留存');
});
