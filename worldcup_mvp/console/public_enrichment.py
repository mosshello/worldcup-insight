"""Bounded, cached public 500 pages: exact fixture identity, estimated lineups and schedules."""
import re
import time
import threading
from datetime import datetime, timezone
from urllib import request
from .sources import SourceError, parse_date, BEIJING
from .store import now_iso
from .history_audit import Document
from .competition_labels import label

_cache = {}
_lock = threading.Lock()
INDEX = 'https://live.500.com/'


def page(url):
    with _lock:
        hit = _cache.get(url)
        if hit and time.monotonic() - hit[0] < 180:
            return hit[1], hit[2]
    try:
        req = request.Request(url, headers={'User-Agent':'Mozilla/5.0 football-analysis/1.0', 'Referer':INDEX})
        with request.build_opener(request.ProxyHandler({})).open(req, timeout=8) as response:
            body = response.read(4_000_001)
        if len(body) > 4_000_000:
            raise ValueError('page too large')
        text = body.decode('gb18030')
        captured = now_iso()
        with _lock:
            if len(_cache) > 128:
                _cache.clear()
            _cache[url] = (time.monotonic(), text, captured)
        return text, captured
    except (OSError, ValueError):
        raise SourceError('500页面获取失败，保留来源缺失状态') from None


def fixture_identity(match, text):
    # Index has month/day only: never match distant historical fixtures across years.
    kickoff = parse_date(match['kickoff']).astimezone(BEIJING)
    if abs((kickoff - datetime.now(timezone.utc)).total_seconds()) > 10*86400:
        raise SourceError('500当前索引不用于匹配远期或历史比赛')
    league = {'ACL':'亚冠杯','UCL':'欧冠'}.get(match.get('league_code'), match.get('league'))
    aliases = {('FVL','ITU'):'国际图尔库', ('FVL','GNI'):'格尼斯坦'}
    names = [aliases.get((match.get('league_code'), match.get(side+'_code')), match[side]) for side in ('home','away')]
    candidates = []
    for tr in Document(text).root.find('tr'):
        if tr.attrs.get('gy') != ','.join((league, *names)):
            continue
        cells = tr.find('td')
        finished = not tr.attrs.get('fid') and re.fullmatch(r'a\d+', tr.attrs.get('id',''))
        time_index, team_indices = (2,(4,6)) if finished else (3,(5,7))
        if len(cells) < 8 or cells[time_index].text != kickoff.strftime('%m-%d %H:%M'):
            continue
        ids = []
        for cell in (cells[team_indices[0]], cells[team_indices[1]]):
            links = [re.search(r'/team/(\d+)/', a.attrs.get('href','')) for a in cell.find('a')]
            ids.append(next((m[1] for m in links if m), None))
        fid = tr.attrs['id'][1:] if finished else tr.attrs.get('fid','')
        if all(ids) and re.fullmatch(r'\d+', fid):
            candidates.append({'id':fid, 'team_ids':ids})
    if len(candidates) != 1:
        raise SourceError('500未通过赛事、双方球队与开球时间唯一匹配')
    return candidates[0]


def player(cell):
    spans = cell.find('span', **{'class':'td_sp3'})
    number = spans[0].text if spans else ''
    text = cell.text
    if number and text.startswith(number):
        text = text[len(number):].strip()
    m = re.fullmatch(r'(.+?)[（(]([^()（）]+)[）)]', text)
    return {'name':m[1], 'position':m[2], 'number':number} if m else None


def parse_page(match, identity, text, captured):
    root = Document(text).root
    headers = root.find('div', **{'class':'odds_header'})
    header = headers[0] if headers else None
    ids = []
    for a in header.find('a') if header else []:
        found = re.search(r'/team/(\d+)/', a.attrs.get('href',''))
        if found and found[1] not in ids: ids.append(found[1])
    stamp = parse_date(match['kickoff']).astimezone(BEIJING).strftime('%Y-%m-%d %H:%M')
    if not header or ids[:2] != identity['team_ids'] or stamp not in header.text:
        raise SourceError('500详情的球队ID或完整开球时间冲突')
    url = f"https://odds.500.com/fenxi/shuju-{identity['id']}.shtml"
    data = {'estimated_lineups':[], 'injuries':[], 'upcoming':[[],[]], 'recent':[[],[]],
            'source':'500', 'source_url':url, 'captured_at':captured, 'provider_event_id':identity['id']}
    for box in root.find('div', **{'class':'M_box starting'}):
        for i, side in enumerate(('home','away')):
            blocks = box.find('div', **{'class':'team_a' if i==0 else 'team_b'})
            if not blocks: continue
            roster = {'side':side, 'status':'estimated', 'players':[], 'source':'500', 'source_url':url,
                      'captured_at':captured, 'notice':'来源预计阵容，非本场正式首发'}
            section = 'players'
            for tr in blocks[0].find('tr'):
                if tr.find('th'):
                    section = 'injuries' if '伤病' in tr.text else 'players'
                    continue
                cells = tr.find('td')
                if not cells: continue
                if section == 'players':
                    value = player(cells[0])
                    if value: roster['players'].append(value)
                else:
                    for j, cell in enumerate(cells[:2]):
                        value = player(cell)
                        if value: data['injuries'].append({**value, 'side':side, 'status':'伤病' if j==0 else '停赛',
                            'source':'500', 'source_url':url, 'captured_at':captured, 'scope':'本场来源名单'})
            if len(roster['players']) == len({(p['name'], p['number']) for p in roster['players']}) == 11:
                data['estimated_lineups'].append(roster)
    for box in root.find('div'):
        titles = box.find('h4')
        if box.attrs.get('class','').startswith('M_box') and titles and titles[0].text == '未来赛事':
            for i in range(2):
                blocks = box.find('div', **{'class':'team_a' if i==0 else 'team_b'})
                for tr in blocks[0].find('tr') if blocks else []:
                    cells = tr.find('td')
                    if len(cells) != 4: continue
                    teams = cells[2].find('a')
                    if len(teams) != 2 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',cells[1].text): continue
                    hrefs = [a.attrs.get('href','') for a in teams]
                    own = [j for j,h in enumerate(hrefs) if f"/team/{identity['team_ids'][i]}/" in h]
                    if len(own)!=1 or cells[1].text <= match['kickoff'][:10]: continue
                    data['upcoming'][i].append({'id':f"500:{identity['id']}:{i}:{cells[1].text}",
                        'date':cells[1].text, 'date_precision':'date', 'opponent':teams[1-own[0]].text,
                        'venue':'主' if own[0]==0 else '客', **label(cells[0].text), 'source':'500', 'source_url':url})
    # Recent completed rows have a focal-team class; deduplicate repeated handicap tables.
    for i, side in enumerate(('home','away')):
        seen = set()
        own_names = {match[side]} | {a.text for a in header.find('a')
                                   if f"/team/{identity['team_ids'][i]}/" in a.attrs.get('href','')}
        if identity['team_ids'][i] == '1327': own_names.add('图尔库国际')
        for tr in root.find('tr'):
            cells = tr.find('td')
            if len(cells)<6 or not re.fullmatch(r'(?:\d{2}|\d{4})-\d{2}-\d{2}',cells[1].text): continue
            team_spans = [s for s in cells[2].find('span') if 'dz-l' in s.attrs.get('class','') or 'dz-r' in s.attrs.get('class','')]
            if len(team_spans)!=2: continue
            names = [re.sub(r'\[\d+\]', '', s.text).strip() for s in team_spans]
            own = [j for j,s in enumerate(team_spans) if 'zhu' in s.attrs.get('class','').split() and names[j] in own_names]
            scores = cells[2].find('em'); links = cells[2].find('a')
            score = re.fullmatch(r'(\d+)\s*:\s*(\d+)',scores[0].text) if scores else None
            fid = re.search(r'shuju-(\d+)', links[0].attrs.get('href','')) if links else None
            date = cells[1].text if len(cells[1].text)==10 else '20'+cells[1].text
            if len(own)!=1 or not score or not fid or fid[1] in seen or date>=match['kickoff'][:10]: continue
            seen.add(fid[1]); values=[int(score[1]),int(score[2])]; gf,ga=values[own[0]],values[1-own[0]]
            data['recent'][i].append({'id':'500:'+fid[1], 'date':date, 'date_precision':'date',
                'opponent':names[1-own[0]], 'venue':'主' if own[0]==0 else '客',
                'gf':gf, 'ga':ga, 'score':f'{gf}-{ga}', **label(cells[0].text), 'source':'500',
                'score_period':'unknown', 'source_url':f'https://odds.500.com/fenxi/shuju-{fid[1]}.shtml'})
        data['recent'][i]=sorted(data['recent'][i], key=lambda r:r['date'], reverse=True)[:5]
    return data


def fetch(match):
    from .readiness import fixture_key
    saved = match.get('_source_identity') or {}
    identity = saved.get('identity') if saved.get('fixture') == fixture_key(match) else None
    if identity and (len(identity.get('team_ids', [])) != 2 or
                     not all(re.fullmatch(r'\d+', str(x)) for x in [identity.get('id'), *identity.get('team_ids', [])])):
        raise SourceError('已保存的比赛来源身份无效')
    if identity is None and match.get('league_code') in ('JD1', 'KD1'):
        from .league_data import load_history, match_fixture
        from .catalog import classify
        try:
            fixture = match_fixture(match, load_history(competition_id=classify(match)['id']))
            identity = {'id': fixture['id'].removeprefix('500:'),
                        'team_ids': [fixture[s+'_id'].removeprefix('500:') for s in ('home', 'away')]}
            if not all(re.fullmatch(r'\d+', x) for x in [identity['id'], *identity['team_ids']]):
                raise SourceError('历史来源身份无效')
        except SourceError:
            identity = None
    if identity is None:
        index, _ = page(INDEX)
        try:
            identity = fixture_identity(match,index)
        except SourceError:
            if parse_date(match['kickoff']) >= datetime.now(timezone.utc): raise
            finished, _ = page('https://live.500.com/wanchang.php')
            identity = fixture_identity(match,finished)
    text, captured = page(f"https://odds.500.com/fenxi/shuju-{identity['id']}.shtml")
    result = parse_page(match, identity, text, captured)
    result['source_identity'] = {'fixture': fixture_key(match), 'identity': identity}
    for roster in result['estimated_lineups']:
        roster['observed_before_kickoff'] = parse_date(captured) < parse_date(match['kickoff'])
        roster['notice'] = ('来源预计阵容，非正式首发；赛前采集' if roster['observed_before_kickoff']
                            else '来源预计阵容；赛后补采，不能作为当时赛前已知名单')
    try:
        odds_text, odds_captured = page(f"https://odds.500.com/fenxi/ouzhi-{identity['id']}.shtml")
        result['external_odds'] = european_odds(match, identity, odds_text, odds_captured)
    except SourceError as exc:
        result['external_odds'] = {'rows': [], 'reason': str(exc)}
    return result


def european_odds(match, identity, text, captured):
    # Validate the same fixture header before reading a separate price page.
    parse_page(match, identity, text, captured)
    rows, seen = [], set()
    now = datetime.now(timezone.utc)
    for tr in Document(text).root.find('tr'):
        company = tr.attrs.get('id')
        if tr.attrs.get('xls') != 'row' or not company or company == '1' or company in seen: continue
        cells = [node for node in tr.children if getattr(node, 'tag', None) == 'td']
        if len(cells) < 3: continue
        try:
            stamp = datetime.strptime(tr.attrs['data-time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=BEIJING)
            if stamp > now or stamp >= parse_date(match['kickoff']): continue
            values = [float(x) for x in cells[2].text.split()]
            if len(values) != 6 or any(not 1 < v < 10000 for v in values): continue
        except (KeyError, ValueError): continue
        seen.add(company)
        latest = values[3:]
        weight = sum(1/v for v in latest)
        rows.append({'id':company, 'company':cells[1].attrs.get('title') or '来源未标名',
                     'initial':values[:3], 'latest':latest, 'updated_at':stamp.isoformat(),
                     'probabilities':[round(100/v/weight,2) for v in latest]})
    return {'rows': rows[:30], 'source':'500公开欧赔', 'captured_at':captured,
            'source_url':f"https://odds.500.com/fenxi/ouzhi-{identity['id']}.shtml",
            'scope':'胜平负欧赔；来源公司名称保留脱敏形式；非亚洲让球盘口，非完整调价序列'}
