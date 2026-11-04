"""Bounded public-source research facts; never training input or commercial authorization."""
import json
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from .history_audit import Archive, Document
from .official_context import statistic
from .sources import SourceError, parse_date
from .store import ROOT

J_TEAMS = {'KAH': '柏', 'URA': '浦和', 'NGV': '長崎', 'TOK': '東京V', 'MCZ': '町田', 'GAB': 'G大阪'}
SUSPENSION_ARCHIVES = ['https://www.jleague.jp/news/article/35233/']
_archive_lock = threading.Lock()


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def archive_page(url):
    archive = Archive(ROOT / 'data/console/source_facts' / datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    try:
        with _archive_lock:
            html = archive.fetch(url)
        return html, archive.entries[-1]['collected_at']
    except (OSError, ValueError) as exc:
        raise SourceError('补充来源获取失败：' + str(exc)) from None


def parse_xg(html, match, mapping, captured):
    root = Document(html).root
    headers = root.find('div', **{'class': 'vsHeader'})
    names = [x.text for x in headers[0].find('td') if x.attrs.get('class') in ('tName r', 'tName l')] if len(headers) == 1 else []
    selected = [x.text for x in root.find('div', **{'class': 'swiper-slide sel'})]
    times = re.findall(r'(\d{4})\.(\d{1,2})\.(\d{1,2})\s+(\d{2}:\d{2})\s+Kick Off', root.text)
    expected = [mapping['season'], 'J1.']
    if ([normalized(n) for n in names] != [normalized(n) for n in mapping['teams']]
            or not any(all(s in text for s in expected) for text in selected) or len(times) != 1):
        raise SourceError('xG来源赛事、赛季或主客身份不符')
    y, m, d, clock = times[0]
    if parse_date(f'{y}-{int(m):02}-{int(d):02}T{clock}:00+09:00') != parse_date(match['kickoff']):
        raise SourceError('xG来源开球时间冲突')
    rows = []
    for tr in root.find('tr'):
        cells = tr.find('td')
        if len(cells) == 7 and cells[3].text == 'ゴール期待値':
            row = statistic('expectedGoals', [cells[2].text, cells[4].text])
            if row: rows.append(row)
    if len(rows) != 1:
        raise SourceError('xG单场有效数值缺失或不唯一')
    return {**rows[0], 'source': 'Football LAB', 'source_url': mapping['source_url'], 'captured_at': captured,
            'scope': '来源比赛单场xG；提供方模型口径', 'purpose': 'local_research',
            'commercial_use_permitted': False, 'training_eligible': False,
            'usage_terms_url': 'https://www.football-lab.jp/contact'}


def fetch_xg(match):
    if match.get('league_code') != 'JD1' or parse_date(match['kickoff']) >= datetime.now(timezone.utc):
        return None
    mappings = json.loads((ROOT / 'config/football_lab_matches.json').read_text(encoding='utf-8'))
    mapping = next((m for m in mappings if m['match_id'] == match['id']
                    and m['codes'] == [match.get('home_code'), match.get('away_code')]
                    and parse_date(m['kickoff']) == parse_date(match['kickoff'])), None)
    if not mapping:
        return None
    html, captured = archive_page(mapping['source_url'])
    return parse_xg(html, match, mapping, captured)


def parse_suspensions(html, match, url, captured):
    root = Document(html).root
    plain = unicodedata.normalize('NFKC', root.text)
    dates = re.findall(r'(20\d{2})/(\d{1,2})/(\d{1,2})', plain)
    if not dates or '出場停止' not in plain:
        raise SourceError('停赛公告结构待核实')
    # Date-only bulletins are conservatively considered published at the end of that day.
    y, m, d = dates[0]
    published = f'{y}-{int(m):02}-{int(d):02}T23:59:59+09:00'
    if parse_date(published) >= parse_date(match['kickoff']):
        return []
    target_day = parse_date(match['kickoff']).astimezone(timezone(timedelta(hours=9)))
    from .catalog import classify
    season = classify(match)['season'].replace('-', '/')
    result = []
    for tr in root.find('tr'):
        cells = tr.find('td')
        if len(cells) != 5: continue  # Reject staff rows and implicit rowspan carry-forward.
        competition = normalized(cells[4].text)
        found = re.fullmatch(r'(\d{4}/\d{2})明治安田J1リーグ第(\d+)節第\d+日\((\d{2})/(\d{2})\)', competition)
        if not found or found[1] != season or found[3] + '/' + found[4] != target_day.strftime('%m/%d'):
            continue
        for side in ('home', 'away'):
            if normalized(cells[1].text) != J_TEAMS.get(match.get(side + '_code')): continue
            result.append({'side': side, 'name': cells[0].text, 'status': '官方确认停赛', 'kind': 'suspension',
                'reason': 'J1第' + found[2] + '轮；处罚类别' + cells[3].text,
                'competition_id': 'jp.j1', 'season': season, 'round': int(found[2]),
                'scope': '仅适用本场日期与赛事', 'source': 'J.League 官网', 'source_url': url,
                'published_at': published, 'published_precision': 'date', 'captured_at': captured,
                'verified_at': captured, 'official': True, 'observed_before_kickoff': parse_date(captured) < parse_date(match['kickoff'])})
    return result


def parse_reysol_injury(html, match, side, url, captured):
    root = Document(html).root
    sections = root.find('section', id='news')
    if len(sections) != 1: raise SourceError('俱乐部伤情公告结构待核实')
    text = sections[0].text
    date = re.search(r'(20\d{2})年(\d{2})月(\d{2})日', text)
    name = re.fullmatch(r'([^<>。]{1,30}?)選手の負傷について', ' '.join(n.text for n in sections[0].find('h3')))
    injury = re.search(r'[「]([^」]+)[」]と診断', text)
    if not date or not name or not injury: raise SourceError('伤情姓名、日期或诊断缺失')
    published = f'{date[1]}-{date[2]}-{date[3]}T23:59:59+09:00'
    age = (parse_date(match['kickoff']) - parse_date(published)).total_seconds() / 86400
    if not 0 <= age <= 45: return None
    return {'side': side, 'name': name[1].strip(), 'status': '伤情已公告，本场状态待核实', 'kind': 'injury',
            'reason': injury[1], 'scope': '伤情消息；复出和本场资格未确认', 'source': '柏太阳神俱乐部官方',
            'source_url': url, 'published_at': published, 'published_precision': 'date', 'captured_at': captured,
            'verified_at': captured, 'official': True, 'observed_before_kickoff': parse_date(captured) < parse_date(match['kickoff'])}


def fetch_personnel(match):
    result = {'rows': [], 'errors': []}
    if match.get('league_code') != 'JD1' or not all(match.get(s + '_code') in J_TEAMS for s in ('home', 'away')): return result
    for url in ['https://data.j-league.or.jp/SFIN03/', *SUSPENSION_ARCHIVES]:
        try:
            html, captured = archive_page(url)
            result['rows'].extend(parse_suspensions(html, match, url, captured))
        except (SourceError, ValueError, KeyError) as exc: result['errors'].append(str(exc))
    for side in ('home', 'away'):
        if match.get(side + '_code') != 'KAH': continue
        try:
            index, _ = archive_page('https://www.reysol.co.jp/news/topteam/')
            urls = list(dict.fromkeys(urljoin('https://www.reysol.co.jp', a.attrs['href'])
                for a in Document(index).root.find('a') if '選手の負傷について' in a.text
                and urlsplit(urljoin('https://www.reysol.co.jp', a.attrs.get('href', ''))).netloc == 'www.reysol.co.jp'
                and re.fullmatch(r'/news/topteam/post-\d+\.html', urlsplit(a.attrs.get('href', '')).path)))[:5]
            for url in urls:
                html, captured = archive_page(url)
                row = parse_reysol_injury(html, match, side, url, captured)
                if row: result['rows'].append(row)
        except (SourceError, ValueError, KeyError) as exc: result['errors'].append(str(exc))
    result['rows'] = list({(r['side'], normalized(r['name']), r['kind'], r.get('round')): r for r in result['rows']}.values())
    return result


def fetch(match):
    result = fetch_personnel(match)
    result['xg'] = None
    from . import public_match_facts
    try:
        result['public_facts'] = public_match_facts.fetch(match)
    except (SourceError, ValueError, KeyError, TypeError, OSError) as exc:
        result['errors'].append(str(exc))
    try:
        result['xg'] = fetch_xg(match)
    except (SourceError, ValueError, KeyError, TypeError, OSError) as exc:
        result['errors'].append(str(exc))
    return result
