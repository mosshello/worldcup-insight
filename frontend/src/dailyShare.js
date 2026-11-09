import { beijingDateTime } from './api.js';
import { selectedOdds, currentSelectedOdds } from './dailyOdds.js';

const codes = ['had', 'hhad', 'hafu', 'crs', 'ttg'];
const labels = ['胜平负', '让球', '半全场', '比分', '进球数'];
const palette = { ink:'#1f2329', muted:'#68717e', teal:'#008858', amber:'#d54941', line:'#e7e9ed', paper:'#f5f6f8' };
export function sharePages(data, size=12) {
  const rows = data.rows || [];
  return Array.from({length:Math.ceil(rows.length/size)},(_,i)=>({day:data.day, rows:rows.slice(i*size,(i+1)*size), index:i+1, total:Math.ceil(rows.length/size)}));
}
export function shareCell(row, code) {
  const m = row.markets?.find(v=>v.code===code) || {};
  const p = row.forecast?.market_assessments?.find(v=>v.code===code);
  const extra = row.forecast?.explorations?.find(v=>v.code===code);
  const extraResult = row.exploration_results?.find(v=>v.code===code);
  const text = !m.selection || m.selection==='未归档' ? '未记录' : m.selection;
  const multiple = (p?.selected_keys?.length || 0) > 1;
  const status = m.hit===true ? (multiple?'已覆盖':'命中') : m.hit===false ? (multiple?'未覆盖':'未命中') : m.status==='未归档选择' ? '无预测' : m.status || '待赛果';
  return { prediction:text+(code==='hhad' && m.line!=null ? ` (${m.line>0?'+':''}${m.line})` : ''),
    probability:p?.selected_keys?.length && Number.isFinite(p.coverage) ? `覆盖概率 ${p.coverage.toFixed(1)}%` : '',
    odds:currentSelectedOdds(row,code) || (selectedOdds(p) ? `留档SP ${selectedOdds(p)}` : ''),
    extraOdds:currentSelectedOdds(row,code,true) || (selectedOdds(extra) ? `观察SP ${selectedOdds(extra)}` : ''),
    actual:`赛果 ${m.actual || '—'} · ${status}`, hit:m.hit,
    extra:extra ? `进取 ${extra.selection} (${extra.coverage.toFixed(1)}%)${extraResult?.hit===true?' · 命中':extraResult?.hit===false?' · 未命中':''}` : '' };
}

export async function renderShareImages(data) {
  await document.fonts.ready;
  const snapshots = sharePages(data), images = [];
  const width=1680, margin=42, widths=[290,210,210,235,260,235,156];
  for(const page of snapshots) {
    const canvas=document.createElement('canvas'), ctx=canvas.getContext('2d');
    if(!ctx) throw new Error('浏览器无法生成图片，请使用支持画布的浏览器');
    const font=(size=22,bold=false)=>{ctx.font=`${bold?'600':'400'} ${size}px "Microsoft YaHei", "PingFang SC", sans-serif`;};
    const wrap=(text,w,size=22,bold=false)=>{
      font(size,bold);const lines=[];let line='';
      for(const char of String(text??'')){if(char==='\n'){lines.push(line);line='';continue;}if(ctx.measureText(line+char).width>w && line){lines.push(line);line=char;}else line+=char;}
      lines.push(line);return lines;
    };
    const rowPlans=page.rows.map(row=>{
      const cells=codes.map(code=>shareCell(row,code));
      const lines=cells.map((c,i)=>[
        ...wrap(c.prediction,widths[i+1]-28,24,true).map(text=>({text,color:palette.ink,bold:true,size:24})),
        ...(c.odds?wrap(c.odds,widths[i+1]-28,19).map(text=>({text,color:palette.ink,size:19})):[]),
        ...(c.probability?wrap(c.probability,widths[i+1]-28,19).map(text=>({text,color:palette.muted,size:19})):[]),
        ...wrap(c.actual,widths[i+1]-28,20).map(text=>({text,color:c.hit===true?palette.teal:c.hit===false?palette.amber:palette.muted,size:20})),
        ...(c.extra?wrap(c.extra,widths[i+1]-28,19).map(text=>({text,color:palette.amber,size:19})):[]),
        ...(c.extraOdds?wrap(c.extraOdds,widths[i+1]-28,19).map(text=>({text,color:palette.amber,size:19})):[])
      ]);
      const fixture=[`${row.number || ''}  ${row.league || ''}`,...wrap(`${row.home} vs ${row.away}`,widths[0]-28,23,true),beijingDateTime(row.kickoff),row.forecast?.source || '未取得预测',...(row.current_quotes?.checked_at?['报价核验',beijingDateTime(row.current_quotes.checked_at)]:[])];
      return {row,cells,lines,fixture,height:Math.max(150,Math.max(fixture.length,...lines.map(l=>l.length))*30+32)};
    });
    const groups=Object.entries(data.audit?.sources || {}).filter(([kind])=>data.rows.some(r=>r.forecast?.kind===kind)).flatMap(([kind,values])=>['single','multiple'].map(group=>({kind,values,group})));
    const headerHeight=164+groups.length*40, height=headerHeight+62+rowPlans.reduce((n,r)=>n+r.height,0)+112;
    canvas.width=width*1.5;canvas.height=height*1.5;ctx.scale(1.5,1.5);
    ctx.fillStyle='#fff';ctx.fillRect(0,0,width,height);
    ctx.fillStyle='#0052d9';ctx.fillRect(0,0,width,100);
    font(36,true);ctx.fillStyle='#fff';ctx.fillText('每日足球扫盘 · 预测与赛果',margin,62);
    font(24);ctx.textAlign='right';ctx.fillText(`${page.day}  ${page.index}/${page.total}`,width-margin,62);ctx.textAlign='left';
    font(20);ctx.fillStyle=palette.muted;ctx.fillText(`全日 ${data.rows.length} 场 · 本图 ${page.rows.length} 场 · 北京时间 · 生成于 ${beijingDateTime(new Date().toISOString())}`,margin,139);
    groups.forEach(({kind,values,group},i)=>{font(21,true);ctx.fillStyle=palette.ink;const name=data.sources?.[kind] || kind;
      ctx.fillText(`${name} · ${group==='single'?'单选命中':'多选覆盖'}  `+codes.map((c,j)=>{const v=values[c]?.[group];return `${labels[j]} ${v?.settled?(100*v.hits/v.settled).toFixed(1)+'% ('+v.hits+'/'+v.settled+')':'待统计'}`;}).join('    '),margin,179+i*40);
    });
    let y=headerHeight;
    ctx.fillStyle=palette.paper;ctx.fillRect(margin,y,width-margin*2,62);
    let x=margin;['场次 / 比赛',...labels,'正式比分'].forEach((label,i)=>{font(23,true);ctx.fillStyle=palette.ink;ctx.fillText(label,x+14,y+40);x+=widths[i];});
    y+=62;
    rowPlans.forEach((plan,index)=>{
      ctx.fillStyle=index%2?'#f7f9fb':'#fff';ctx.fillRect(margin,y,width-margin*2,plan.height);
      let x=margin;
      plan.fixture.forEach((line,i)=>{font(i===1?23:19,i===1);ctx.fillStyle=i===1?palette.ink:palette.muted;ctx.fillText(line,x+14,y+32+i*30);});x+=widths[0];
      plan.lines.forEach((lines,i)=>{lines.forEach((line,j)=>{font(line.size,line.bold);ctx.fillStyle=line.color;ctx.fillText(line.text,x+14,y+32+j*30);});x+=widths[i+1];});
      font(26,true);ctx.fillStyle=palette.ink;ctx.fillText(plan.row.cancelled?'已取消':plan.row.score || '待赛果',x+14,y+42);
      y+=plan.height;ctx.strokeStyle=palette.line;ctx.beginPath();ctx.moveTo(margin,y);ctx.lineTo(width-margin,y);ctx.stroke();
    });
    font(19);ctx.fillStyle=palette.muted;
    ctx.fillText('SP按图中官方核验时间展示；图片生成后不自动更新。主预测与进取观察分别核对。',margin,y+40);
    ctx.fillText('市场参考及旧日志不冒充模型预测；未记录、观望、取消、待赛果与冲突不计命中率。',margin,y+73);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
    if(!blob) throw new Error('图片生成失败，请减少单日场次后重试');
    images.push({blob,name:`每日扫盘-${page.day}-${page.index}.png`,width:canvas.width,height:canvas.height});
  }
  return images;
}
