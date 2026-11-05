import { test, expect } from '@playwright/test';

test('daily sweep highlights rates, exports a share image and fits narrow screens', async ({page},testInfo) => {
  const codes=['had','hhad','hafu','crs','ttg'];
  const source={model:'足球模型',market:'市场参考',legacy:'旧日志参考'};
  const markets=codes.map((code,i)=>({code,selection:['主胜','让平','胜胜','2-1 / 1-0 / 2-0','2球 / 3球'][i],actual:['主胜','让负','—','2-1','3球'][i],status:i===2?'待赛果':i===1?'未命中':'命中',hit:i===2?null:i!==1,line:code==='hhad'?-1:null}));
  const summary=Object.fromEntries(Object.keys(source).map(k=>[k,Object.fromEntries(codes.map(c=>[c,c==='hafu'?{hits:0,settled:0}:c==='hhad'?{hits:0,settled:1}:{hits:1,settled:2}]))]));
  const requests=[], writes=[], errors=[];
  let currentOdds=3.25;
  page.on('pageerror', e=>errors.push(e.message));
  page.on('request', r=>{if(r.url().includes('/api/')&&r.method()!=='GET')writes.push(r.url());});
  await page.route('**/api/**', route=>{
    const url=new URL(route.request().url());
    let data={success:true};
    if(url.pathname.endsWith('/daily-sweep')) {
      requests.push(url.searchParams.get('date'));
      const kind=['model','legacy'].includes(url.searchParams.get('source'))?url.searchParams.get('source'):'market';
      data={...data,day:url.searchParams.get('date')||'2026-11-04',days:['2026-11-04'],summary,sources:source,
        collector:{status:'available',last_success:'2026-11-05T01:40:00Z'},
        rows:[{id:'sporttery:daily',number:'周三001',home:'测试主队',away:'测试客队',league:'欧冠',day:'2026-11-04',kickoff:'2026-11-05T04:00:00+08:00',score:'2-1',markets,
          forecast:{kind,source:source[kind],captured_at:'2026-11-04T10:00:00Z',market_assessments:codes.map((code,i)=>({code,selected_keys:['h'],selection:markets[i].selection,options:kind==='legacy'?[]:[{key:'h',label:markets[i].selection,odds:2.5+i}],coverage:64.2}))},available_sources:['model','market','legacy'],
          settlement_versions:[{id:1,created_at:'2026-11-05T01:40:00Z',markets,score:'2-1',source:'体彩官方'}]}]};
      if(kind==='legacy') data.rows=Array.from({length:13},(_,i)=>({...data.rows[0],id:'sporttery:day-'+i,home:'主队'+(i+1),number:'周三'+String(i+1).padStart(3,'0')}));
      if(kind!=='legacy') for(const row of data.rows){
        row.current_quotes={checked_at:'2026-11-04T11:00:00Z',markets:Object.fromEntries(codes.map((code,i)=>[code,{line:code==='hhad'?-1:null,options:[{key:'h',label:markets[i].selection,odds:currentOdds+i}]}]))};
        row.forecast.market_assessments.find(m=>m.code==='hhad').line=-1;
      }
    } else if(url.pathname.endsWith('/matches')) data={...data,matches:[]};
    else if(url.pathname.endsWith('/state')) data={...data,state:{favorites:[],notes:{},versions:{},reviewNotes:{},drafts:{}}};
    else if(url.pathname.endsWith('/review-updates')) data={...data,cursor:0,updates:[]};
    return route.fulfill({json:data});
  });
  await page.goto('/club-preview.html#/daily-sweep');
  const nav=page.getByRole('navigation',{name:'主导航'});
  await expect(nav.getByRole('link')).toHaveText(['比赛中心','实时比分','复盘记录','每日扫盘命中','数据与模型']);
  const match=page.getByRole('article',{name:'测试主队对测试客队'});
  for(const title of ['胜平负','让球','半全场','比分','进球数']) await expect(match.getByRole('region',{name:title+'对比',exact:true})).toBeVisible();
  await expect(match).toContainText('2-1 / 1-0 / 2-0');
  await expect(match).toContainText('待赛果');
  await expect(match).toContainText('75.0%');
  await expect(match).toContainText('参考覆盖概率 64.2%');
  await expect(match.locator('.current-odds').first()).toContainText('最新SP 主胜 @ 3.25');
  currentOdds=3.5;await page.getByRole('button',{name:'刷新记录',exact:true}).click();
  await expect(match.locator('.current-odds').first()).toContainText('最新SP 主胜 @ 3.50');
  await match.locator('.odds-note summary').first().click();
  await expect(match.getByText('主胜 @ 2.50',{exact:true})).toBeVisible();
  for(const [i,title] of ['胜平负','让球','半全场','比分','进球数'].entries()) {
    await expect(match.getByRole('region',{name:title+'对比',exact:true})).toContainText(`@ ${(2.5+i).toFixed(2)}`);
  }
  await expect(match.locator('.is-hit')).toHaveCount(3);
  await expect(match.locator('.is-miss')).toHaveCount(1);
  await expect(page.getByLabel('胜平负命中率',{exact:true})).toContainText('50.0%');
  await expect(page.getByLabel('让球命中率',{exact:true})).toContainText('0.0%');
  await expect(page.getByLabel('半全场命中率',{exact:true})).toContainText('待统计');
  await expect(page.getByLabel('半全场命中率',{exact:true})).not.toContainText('0.0%');
  const download=page.waitForEvent('download');
  await page.getByRole('button',{name:'导出记录'}).click();
  expect((await download).suggestedFilename()).toBe('每日扫盘-2026-11-04.csv');
  await page.getByRole('button',{name:'查看记录'}).click();
  await expect(page.locator('.t-drawer--open')).toContainText('赛果核对历史');
  await expect(page.locator('.t-drawer--open').getByRole('link',{name:'查看关联复盘与赛后分析'})).toHaveAttribute('href','#/reviews/sporttery%3Adaily');
  await page.keyboard.press('Escape');
  await page.getByRole('textbox',{name:'判断来源',exact:true}).click();
  await page.locator('.t-select-option').filter({hasText:/^足球模型$/}).click();
  await expect(match).toContainText('足球模型预测');
  await expect(match).toContainText('模型覆盖概率 64.2%');
  await page.getByRole('button',{name:'生成分享图',exact:true}).click();
  const preview=page.getByRole('img',{name:'每日扫盘分享图，第1张'});
  await expect(preview).toBeVisible();
  await expect.poll(()=>preview.evaluate(e=>e.naturalWidth)).toBe(2520);
  const png=page.waitForEvent('download');
  await page.getByRole('button',{name:'下载当前图片',exact:true}).click();
  const image=await png;
  expect(image.suggestedFilename()).toBe('每日扫盘-2026-11-04-1.png');
  await image.saveAs(testInfo.outputPath('daily-share.png'));
  await page.keyboard.press('Escape');
  await expect(preview).toBeHidden();
  for(const width of [1440,1137,768,600,390,360]) {
    await page.setViewportSize({width,height:900});
    const overflow=await page.evaluate(()=>[...document.querySelectorAll('.daily-workspace,.daily-workspace *, .daily-header nav')].filter(e=>!e.closest('.t-drawer') && e.clientWidth>0 && e.scrollWidth>e.clientWidth+2 && getComputedStyle(e).display!=='inline' && getComputedStyle(e).pointerEvents!=='none').map(e=>({tag:e.tagName,cls:e.className,width:e.clientWidth,scroll:e.scrollWidth,html:e.outerHTML.slice(0,280)})));
    expect(overflow,`horizontal overflow at ${width}`).toEqual([]);
    const size=await page.evaluate(()=>({body:document.body.scrollWidth,width:innerWidth}));
    expect(size.body).toBeLessThanOrEqual(size.width);
    for(const title of ['胜平负','让球','半全场','比分','进球数']) {
      const box=await match.getByRole('region',{name:title+'对比',exact:true}).boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width);
    }
  }
  await expect(match).toContainText('测试主队');
  await page.getByRole('textbox',{name:'判断来源',exact:true}).click();
  await page.locator('.t-select-option').filter({hasText:/^旧日志参考$/}).click();
  await expect(page.locator('.daily-match')).toHaveCount(10);
  await page.getByRole('textbox',{name:'搜索比赛',exact:true}).fill('主队13');
  await expect(page.locator('.daily-match')).toHaveCount(1);
  await page.getByRole('button',{name:'生成分享图',exact:true}).click();
  await expect(page.getByRole('img',{name:'每日扫盘分享图，第1张'})).toBeVisible();
  await page.getByRole('button',{name:'下一张',exact:true}).click();
  const second=page.getByRole('img',{name:'每日扫盘分享图，第2张'});
  await expect(second).toBeVisible();
  const previewBounds=await second.boundingBox();expect(previewBounds.x+previewBounds.width).toBeLessThanOrEqual(360);
  const secondDownload=page.waitForEvent('download');
  await page.getByRole('button',{name:'下载当前图片',exact:true}).click();
  expect((await secondDownload).suggestedFilename()).toBe('每日扫盘-2026-11-04-2.png');
  expect(writes).toEqual([]);expect(errors).toEqual([]);
});
