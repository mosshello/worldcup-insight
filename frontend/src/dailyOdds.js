// Read only the odds frozen with each selection; never substitute live quotes.
export function selectedOdds(market) {
  if (!market?.selected_keys?.length) return '';
  return market.selected_keys.map(key => {
    const matches = (market.options || []).filter(option => option.key === key);
    const option = matches.length === 1 ? matches[0] : null;
    const label = option?.label || (market.selected_keys.length === 1 ? market.selection : key);
    const odds = typeof option?.odds === 'number' && Number.isFinite(option.odds) && option.odds > 1
      ? option.odds.toFixed(2) : '未留存';
    return `${label} @ ${odds}`;
  }).join(' / ');
}

export function currentSelectedOdds(row, code, extra=false) {
  const forecast=(extra?row.forecast?.explorations:row.forecast?.market_assessments)?.find(m=>m.code===code);
  if(!forecast?.selected_keys?.length) return '';
  const quotes=row.current_quotes, pool=quotes?.markets?.[code];
  if(!pool?.options?.length) return '最新SP 暂未获取';
  if(code==='hhad' && (forecast.line==null || pool.line!==forecast.line)) return '让球线已变化，当前报价不可直接对比';
  if(row.forecast?.kind==='legacy' && code==='crs' && forecast.selected_keys.some(k=>pool.options.find(o=>o.key===k)?.label!==forecast.selection)) return '精确比分无对应最新报价';
  const prefix=quotes.closed?'最后SP':quotes.stale?'上次SP（待更新）':'最新SP';
  return `${prefix} ${selectedOdds({...pool,selected_keys:forecast.selected_keys,selection:forecast.selection})}`;
}
