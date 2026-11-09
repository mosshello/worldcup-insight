import { test, expect } from '@playwright/test';
test('research models show actual inputs and candidates without replacing selected production model',async({page})=>{
 const requests=[],writes=[];
 await page.addInitScript(()=>localStorage.setItem('football:model-competition','eng.premier'));
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url());requests.push(url.pathname);if(route.request().method()!=='GET')writes.push(url.pathname);
  let body={success:true};
  if(url.pathname==='/api/v2/workspaces')body={...body,competitions:[{id:'eng.premier',name:'英超'}],workspace:{competition:{id:'eng.premier'},overview:{},training:{model:{run_id:'production-fixed'}},models:[{run_id:'production-fixed',name:'原模型'}],datasets:{},teams:[],tasks:[],sources:[],issues:[]}};
  else if(url.pathname==='/api/v1/observations')body={...body,research_status:{competitions:[{competition_id:'eng.premier',research_candidates:[{kind:'catboost',model_id:'catboost-only-had',status:'candidate',selected:false,supported_markets:['had'],input_metadata:{actually_used_fields:['xgf'],not_trained:['national_team_player_load']},coverage:{training_samples:800},reports:[]},{kind:'lightgbm_goals',model_id:'lightgbm-goals',status:'candidate',selected:false,supported_markets:['had','ttg','crs'],input_metadata:{actually_used_fields:['xgf']},coverage:{training_samples:800},reports:[]}],enhanced:{model_id:'candidate-abc',selected:false,input_metadata:{actually_used_fields:['xgf','rest'],not_trained:['injuries']},coverage:{training_samples:800,history_samples:1000},reports:[{name:'固定测试集',status:'已评估',sample_size:120,current_artifact_evaluation:false,evaluated_model_id:'prior-candidate-xyz',metrics:{had:{baseline:{n:120,accuracy:.5,log_loss:1.1,brier:.6},enhanced:{n:120,accuracy:.55,log_loss:1.05,brier:.58}}},source:'冻结报告'}],alerts:[{kind:'coach_change',message:'新教练已核实'}]}}]}};
  else if(url.pathname.endsWith('/state'))body={...body,state:{}};
  else if(url.pathname.endsWith('/review-updates'))body={...body,updates:[],cursor:0};
  await route.fulfill({json:body});
 });
 await page.goto('/club-preview.html#/models');
 await page.getByText('模型与使用',{exact:true}).click();
 const panel=page.getByRole('region',{name:'研究模型'});
 await expect(panel.getByText('研究候选',{exact:true}).first()).toBeVisible();
 await expect(panel.getByText('近期xG / 休息天数')).toBeVisible();
 await expect(panel.getByText('800 / 1000 场')).toBeVisible();
 await panel.getByRole('button',{name:'输入与报告'}).first().click();
 await expect(page.getByText('candidate-abc',{exact:true})).toBeVisible();
 await expect(page.getByText('固定测试集',{exact:true})).toBeVisible();await expect(page.getByText('当前版本尚无同版评估',{exact:true})).toBeVisible();await expect(page.getByText('单选 55.0%',{exact:true})).toBeVisible();
 await expect(page.getByText('伤停',{exact:true})).toBeVisible();
 for(const width of [320,390,1440]){await page.setViewportSize({width,height:900});await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);}
 await page.keyboard.press('Escape');
 await expect(page.getByText('研究模型详情',{exact:true})).toBeHidden();
 await expect(page.getByText('原模型',{exact:true}).first()).toBeVisible();await panel.locator('article').filter({hasText:'CatBoost胜平负'}).getByRole('button',{name:'输入与报告'}).click();await expect(page.locator('.research-detail dl').getByText('胜平负',{exact:true})).toBeVisible();await expect(page.locator('.research-detail dl')).not.toContainText('总进球');await expect(page.getByText('国家队球员负荷',{exact:true})).toBeVisible();
 expect(writes).toEqual([]);expect(requests.filter(x=>x==='/api/v1/observations')).toHaveLength(1);
});

test('prospective cells remain separate and no samples remain observing',async({page})=>{
 let empty=false;
 await page.route('**/api/**',async route=>{const path=new URL(route.request().url()).pathname;let body={success:true};if(path==='/api/v1/observations')body={...body,rows:[],summary:{targets:4},research_status:{evaluation:{cells:empty?[]:[{competition_id:'eng.premier',node:'t60',source:'ai',market:'had',experiment_id:'e1',n:2,targets:4,coverage:.5,status:'observing'},{competition_id:'esp.laliga',node:'t60',source:'enhanced',market:'had',experiment_id:'e2',n:3,targets:4,coverage:.75,status:'observing'}]}},source_status:[{id:'market-research',status:'available',reports:[{competition_id:'eng.premier',monitoring_validated:false,production_enabled:false,events:0,covered_fixture_seconds:0}]}]};else if(path.endsWith('state'))body.state={};else if(path.endsWith('review-updates'))Object.assign(body,{updates:[],cursor:0});await route.fulfill({json:body});});
 await page.goto('/club-preview.html#/observations');await page.getByRole('button',{name:'研究状态',exact:true}).click();
 await expect(page.getByText('2 场',{exact:true})).toBeVisible();await expect(page.getByText('3 场',{exact:true})).toBeVisible();await expect(page.getByText('5 场',{exact:true})).toHaveCount(0);await expect(page.getByText('价格持续性观察中',{exact:true})).toBeVisible();
 await page.keyboard.press('Escape');await expect(page.getByRole('heading',{name:'前瞻评估',exact:true})).toBeHidden();empty=true;await page.getByRole('button',{name:'刷新',exact:true}).click();await page.getByRole('button',{name:'研究状态',exact:true}).click();await expect(page.getByText('观察中 · 暂无符合条件的已结算样本')).toBeVisible();
 await page.setViewportSize({width:320,height:900});await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
});
