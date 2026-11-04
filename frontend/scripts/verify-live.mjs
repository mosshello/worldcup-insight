import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
async function settledTabSlider(page) {
  await page.waitForFunction(() => {
    const tabs=document.querySelector('.detail-tabs');
    const active=tabs?.querySelector('.t-tabs__nav-item.t-is-active');
    const slider=tabs?.querySelector('.t-tabs__bar');
    if (!active || !slider) return false;
    const a=active.getBoundingClientRect(),s=slider.getBoundingClientRect();
    return Math.abs(a.left-s.left)<1 && Math.abs(a.width-s.width)<1;
  },undefined,{timeout:10000});
}
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>localStorage.setItem('football:model-competition','afc.champions'));
await page.goto('http://127.0.0.1:8891/static/console/index.html#/models');
await page.getByText('比赛资格',{exact:true}).waitFor({timeout:60000});
await settledTabSlider(page);
await page.screenshot({path:'../.ai/tmp/refactor-live-overview.png',fullPage:true});
await page.getByText('数据集与质量',{exact:true}).click();
await page.getByText('赛季对账',{exact:true}).waitFor();
await settledTabSlider(page);
await page.screenshot({path:'../.ai/tmp/refactor-live-datasets.png',fullPage:true});
await page.getByText('模型与使用',{exact:true}).click();
await page.getByText('模型版本',{exact:true}).first().waitFor();
await settledTabSlider(page);
await page.screenshot({path:'../.ai/tmp/refactor-live-models.png',fullPage:true});
const evidence={url:page.url(),scripts:await page.locator('script[type="module"]').evaluateAll(nodes=>nodes.map(n=>n.src)),text:await page.locator('body').innerText(),errors};
writeFileSync('../.ai/tmp/refactor-live-ui.json',JSON.stringify(evidence,null,2));
await browser.close();
if(errors.length) throw new Error(errors.join('\n'));
console.log(JSON.stringify({scripts:evidence.scripts,errors,screenshots:3}));
