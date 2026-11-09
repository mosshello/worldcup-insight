export const markets = { had:'胜平负', hhad:'让球', ttg:'总进球', crs:'比分', hafu:'半全场' };
export const nodes = { opening:'开盘 / 首次采集', t60:'赛前60分钟', t15:'赛前15分钟' };
export const sources = { market:'市场参考', base:'基础模型', enhanced:'情报增强', ai:'AI独立判断' };
export const statuses = { pending:'待观察', running:'生成中', completed:'已留档', failed:'失败', unavailable:'资料不足', unsupported:'不支持', missed:'错过时点', cancelled:'已取消', queued:'待运行', late:'晚到记录' };
export const recordExperimentId = record => record?.experiment_id || '__legacy__';
export function observationExperiments(detail) {
  const indexed=new Map((detail?.experiments||[]).map(e=>[e.id,{...e,sortTime:e.created_at||''}]));
  for(const record of detail?.observations||[]){const id=recordExperimentId(record),time=record.created_at||'';if(!indexed.has(id))indexed.set(id,{id,created_at:null,sortTime:time});else if(!indexed.get(id).created_at&&time>indexed.get(id).sortTime)indexed.get(id).sortTime=time;}
  const active=detail?.active_experiment_id || [...indexed.values()].find(e=>e.active)?.id;
  if(active&&!indexed.has(active))indexed.set(active,{id:active,created_at:null,sortTime:''});
  const result=[...indexed.values()].sort((a,b)=>Number(b.id===active)-Number(a.id===active)||b.sortTime.localeCompare(a.sortTime));
  const selected=active||result[0]?.id;
  return result.map(e=>({...e,active:e.id===selected}));
}
export function optionLabel(code, key) {
  if(code==='had') return {h:'主胜',d:'平',a:'客胜'}[key] || key;
  if(code==='hhad') return {h:'让胜',d:'让平',a:'让负'}[key] || key;
  if(code==='hafu') return [...key].map(k=>({h:'胜',d:'平',a:'负'}[k]||k)).join('');
  if(code==='ttg') { const n=key.replace(/^s/,''); return `${n==='7'?'7+':n}球`; }
  const match=/^s(\d{2})s(\d{2})$/.exec(key);
  return match ? `${Number(match[1])}-${Number(match[2])}` : ({s1sh:'胜其它',s1sd:'平其它',s1sa:'负其它'}[key]||key);
}
export const percent = value => typeof value==='number' && Number.isFinite(value) ? `${(value*100).toFixed(1)}%` : '—';
export function probabilities(record, code) {
  const value = record?.result?.probabilities || record?.probabilities || {};
  const p = value[code] || (Object.values(value).every(v=>typeof v==='number') ? value : {});
  return Object.entries(p).filter(([,v])=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=1).sort((a,b)=>b[1]-a[1]);
}
export function observationText(record, code) {
  if(!record) return '待观察';
  if(record.status!=='completed') return statuses[record.status] || '待核查';
  const entries=probabilities(record,code);
  if(!entries.length) return '不支持';
  const selection=record.result?.selections?.[code] || record.selections?.[code];
  if(Array.isArray(selection)&&!selection.length) return '观望';
  const chosen=Array.isArray(selection)&&selection.length ? entries.filter(([k])=>selection.includes(k)) : [entries[0]];
  const key=chosen.map(([k])=>optionLabel(code,k)).join(' / '), p=chosen.reduce((sum,[,v])=>sum+v,0);
  const line=record.result?.handicap ?? record.handicap ?? record.result?.line ?? record.line;
  return `${code==='hhad' && Number.isFinite(line)?`(${line>0?'+':''}${line}) `:''}${key} ${percent(p)}`;
}
