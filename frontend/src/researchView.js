export const researchFields = { home:'主客场',gf:'近期进球',ga:'近期失球',opp_gf:'对手近期进球',opp_ga:'对手近期失球',xgf:'近期xG',xga:'近期失球xG',opp_xgf:'对手近期xG',opp_xga:'对手近期失球xG',rest:'休息天数',opp_rest:'对手休息天数',recent_goals:'近期比分',recent_xg:'近期xG',league_rest:'联赛休息间隔',injuries:'伤停',lineup:'首发',replacement_quality:'替补能力',tactics:'战术',cross_competition_load:'跨赛事负荷',personnel:'人员',expected_lineup:'预计首发',coach:'教练',new_season:'新赛季',promoted_team:'升班马',coach_change:'换帅',workload:'比赛负荷'};
export const researchLabel = key => researchFields[key] || (key?.startsWith('opp_')?'对手'+researchLabel(key.slice(4)):key);
Object.assign(researchFields,{historical_goals:'历史比分',home_advantage:'主场优势',time_decay:'时间衰减',half_time_goals:'半场比分'});
Object.assign(researchFields,{starter_retention:'历史首发延续率',starter_retention_missing:'首发对照缺失',last_rotation:'上一场轮换比例',bench_minute_share:'历史替补分钟占比',bench_players_per_match:'场均替补出场人数',distinct_starters_ratio:'历史首发人员变动率',minutes_top11_share:'前11人分钟集中度',everpresent_starter_share:'连续首发人员占比',known_league_minutes_14d:'已知近14天联赛分钟',observed_coach_change:'已观察教练变更',coach_comparison_missing:'教练对照缺失',observed_same_coach_games:'已观察同教练场次',h2h_win_streak:'交锋连胜场次',h2h_draw_share:'交锋平局占比',h2h_count:'交锋样本数',h2h_same_venue_win_share:'同主客场交锋胜率',target_starting_xi:'本场确认首发',expected_minutes:'本场预计分钟',injury_eligibility:'本场伤停及参赛资格',complete_cross_competition_load:'完整跨赛事负荷',current_FPL_report_not_confirmed_target_lineup_or_registration:'当前伤情报告，未确认本场首发及注册资格'});
export const researchVariants={DC:'低比分修正基准',dc:'低比分修正基准',xg_base:'xG基准',xg_personnel:'xG＋历史人员',xg_coach:'xG＋教练',xg_personnel_coach:'xG＋历史人员与教练',xg_personnel_coach_h2h:'xG＋历史人员、教练与交锋'};
Object.assign(researchFields,{observed_club_games_14d:'已观察近14天俱乐部比赛',observed_club_games_30d:'已观察近30天俱乐部比赛',observed_nonleague_games_30d:'已观察近30天非联赛比赛',days_since_known_club_game_upper_bound:'距上次已知俱乐部比赛天数上界',club_load_missing:'球队赛程负荷缺失'});
Object.assign(researchFields,{national_team_player_load:'国家队球员负荷',source_reported:'来源已报告',injury_reported:'已报告伤情',unavailable_reported:'已报告缺席',doubtful:'出场存疑',confirmed:'已确认',suspended:'停赛',unregistered:'未注册',ineligible:'无参赛资格'});
researchVariants.xg_personnel_coach_h2h_clubload='xG＋历史人员、教练、交锋与球队赛程';
export const researchState = row => row.selected ? '生产已选用' : ({unavailable:'未就绪',invalid_artifact:'版本校验失败',research_baseline:'研究基准'}[row.status] || '研究候选');
export function researchCoverage(row) {
  const c=row.coverage||{}, train=c.training_samples??c.train_count, total=c.history_samples??c.history_count;
  return train==null?'未记录':total==null?`${train} 场`:`${train} / ${total} 场`;
}
export const researchList = value => Array.isArray(value) ? value : [];
export function aiUsage(record) {
  const result = record?.result || record || {};
  const call = result.ai_call || result;
  return {tokens:call.usage?.total_tokens ?? null, input:call.usage?.prompt_tokens ?? null,
    output:call.usage?.completion_tokens ?? null, duration:call.duration_ms ?? null,
    cost:call.cost ?? null, model:call.response_model ?? call.model ?? result.model_id ?? null};
}
export function aiCostEstimate(record) {
  const result=record?.result||record||{}, estimate=(result.ai_call||result).cost_estimate;
  if(!estimate||!Number.isFinite(estimate.minimum)||!Number.isFinite(estimate.maximum))return null;
  return {...estimate,text:`${estimate.currency||'USD'} ${estimate.minimum.toFixed(4)}${estimate.minimum===estimate.maximum?'':` ～ ${estimate.maximum.toFixed(4)}`}`};
}
