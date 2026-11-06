"""Render a browsable per-club/season inventory from the verified catalogs."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.audit_big_five_clubs import OUT,CODES,SPECS,read,digest


def run():
    index=['# 五大联赛俱乐部与球员核查索引','',
        '历史范围为2021/22至2026/27已完场比赛。每队链接包含各赛季来源名单、实际首发或替补登场记录及差异。球员ID按来源保留。','']
    directory=OUT/'club-reviews';directory.mkdir(exist_ok=True)
    for slug in CODES:
        folder=OUT/slug;p=read(folder/'latest.json');m=read(folder/p['file']);f=m['club_file']
        if digest((folder/f['file']).read_bytes())!=f['sha256']:raise ValueError('club_catalog_integrity')
        clubs=read(folder/f['file'])
        index.extend(['## '+SPECS[slug][1],'','| 俱乐部 | 2026/27有样本 | 历史赛季数 | 比赛数 | 名单漏列的出场关联数 |',
            '|---|---|---:|---:|---:|'])
        for tid,years in sorted(clubs.items(),key=lambda v:v[1][-1]['name']):
            name=years[-1]['name'];file=slug+'-'+tid.split(':')[1]+'.md'
            n=sum(c['counts']['matches'] for c in years);omitted=sum(len(c['comparison']['played_but_not_listed_ids']) for c in years)
            index.append(f"| [{name}](club-reviews/{file}) | {'是' if years[-1]['season']=='2026' else '否'} | {len(years)} | {n} | {omitted} |")
            lines=['# '+name,'',f"{SPECS[slug][1]} · {tid} · {n}场。赛季名单按复核日取回；出场依据为逐场历史记录。",'']
            for c in years:
                a=c['comparison'];counters=c['counts']
                lines.extend([f"## {c['season']}/{str(int(c['season'])+1)[2:]}",'',
                    f"名单列出{a['listed_players']}人，实际出场的ESPN球员中有{len(a['played_but_not_listed_ids'])}人未列在名单。"
                    f"该队{counters['matches']}场中，{counters['validated_minutes']}场有分钟记录，"
                    f"{counters['match_manager']}场有赛后教练记录，{counters['player_stats_reported']}场有可关联的球员统计。",'',
                    '| 球员 | 来源ID | 名单位置 | 在赛季名单 | 首发场次 | 替补上场场次 |','|---|---|---|---|---:|---:|'])
                for pid,person in sorted(c['players'].items(),key=lambda v:(-v[1]['starts'],-v[1]['sub_appearances'],v[0])):
                    lines.append(f"| {person['name']} | {pid} | {person['listed_position'] or '未知'} | {'是' if person['listed'] else '未列'} | {person['starts']} | {person['sub_appearances']} |")
                lines.append('')
            (directory/file).write_text('\n'.join(lines)+'\n',encoding='utf8')
        index.append('')
    (OUT/'club-index.md').write_text('\n'.join(index)+'\n',encoding='utf8')


if __name__=='__main__':run()
