import test from 'node:test';
import assert from 'node:assert/strict';
import { aiUsage, aiCostEstimate, researchLabel, researchCoverage, researchState, researchVariants } from '../src/researchView.js';
test('AI usage reads actual direct and extraction accounting without inventing cost',()=>{
  assert.deepEqual(aiUsage({result:{ai_call:{usage:{total_tokens:90,prompt_tokens:70,completion_tokens:20},duration_ms:123,cost:null}}}),{tokens:90,input:70,output:20,duration:123,cost:null,model:null});
  assert.equal(aiUsage({usage:{total_tokens:0},cost:0}).tokens,0);
  assert.equal(aiUsage({usage:{total_tokens:0},cost:0}).cost,0);
  assert.equal(aiUsage({}).tokens,null);
  assert.equal(researchLabel('cross_competition_load'),'跨赛事负荷');
});
test('optional price estimate never becomes actual invoiced cost',()=>{
 const record={response_model:'deepseek-flash',cost:null,cost_estimate:{currency:'USD',minimum:.01,maximum:.02,source_url:'https://api-docs.deepseek.com'}};
 assert.equal(aiUsage(record).cost,null);assert.equal(aiUsage(record).model,'deepseek-flash');
 assert.equal(aiCostEstimate(record).text,'USD 0.0100 ～ 0.0200');assert.equal(aiCostEstimate({}),null);
});
test('research coverage and production selection remain explicit',()=>{
  assert.equal(researchCoverage({coverage:{training_samples:80,history_samples:100}}),'80 / 100 场');
  assert.equal(researchCoverage({coverage:{training_samples:0}}),'0 场');
  assert.equal(researchCoverage({}),'未记录');
  assert.equal(researchState({status:'research_baseline'}),'研究基准');
  assert.equal(researchState({status:'candidate',selected:true}),'生产已选用');
  assert.equal(researchLabel('opp_known_league_minutes_14d'),'对手已知近14天联赛分钟');
  assert.equal(researchLabel('target_starting_xi'),'本场确认首发');
  assert.equal(researchVariants.xg_personnel_coach_h2h,'xG＋历史人员、教练与交锋');
});
