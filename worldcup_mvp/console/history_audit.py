"""Bounded public-source feasibility audit; never imports or licenses training data.

Run: python -m worldcup_mvp.console.history_audit
Raw responses and normalized samples stay in ignored data/training/source_audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib import request, parse, error

from .store import ROOT

J = "https://data.j-league.or.jp"
K = "https://www.kleague.com"
YEARS = (2023, 2024, 2025)
# Explicit identities for this bounded sample, never fuzzy name matching.
NAMES_500 = {
    "jleague:kawasakif": {"川崎前锋"}, "jleague:yokohamafm": {"横滨水手"},
    "jleague:nagoya": {"名古屋鲸八", "名古屋鲸鱼"}, "jleague:kashiwa": {"柏太阳神"},
    "jleague:hiroshima": {"广岛三箭"}, "jleague:urawa": {"浦和红钻"},
    "jleague:niigata": {"新泻天鹅", "新潟天鹅"}, "jleague:gosaka": {"大阪钢巴"},
    "jleague:cosaka": {"大阪樱花"}, "jleague:machida": {"町田泽维亚"},
    "kleague:K18": {"仁川联", "仁川联队"}, "kleague:K10": {"大田市民"},
    "kleague:K21": {"江原FC"}, "kleague:K01": {"蔚山现代", "蔚山HD"},
    "kleague:K03": {"浦项制铁", "浦项铁人"}, "kleague:K05": {"全北现代"},
}


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    @property
    def text(self):
        return " ".join(" ".join(c.text if isinstance(c, Node) else c for c in self.children
                                 if not isinstance(c, Node) or c.tag not in {"script", "style"}).split())

    def find(self, tag=None, **attrs):
        found = []
        for c in self.children:
            if isinstance(c, Node):
                if (tag is None or c.tag == tag) and all(c.attrs.get(k) == v for k, v in attrs.items()):
                    found.append(c)
                found.extend(c.find(tag, **attrs))
        return found


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in {"br", "img", "input", "meta", "link", "hr", "area", "source", "wbr", "base", "embed", "param", "col"}:
            self.stack.append(n)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def score(text):
    if not re.fullmatch(r"\d{1,2}-\d{1,2}", text):
        raise ValueError("not a completed regular-time score")
    values = [int(x) for x in text.split("-")]
    if max(values) > 30:
        raise ValueError("implausible score")
    return values


def j_team(cell):
    links = cell.find("a")
    if len(links) != 1:
        raise ValueError("ambiguous J club identity")
    slug = re.search(r"/club/([a-z0-9_-]+)/", links[0].attrs.get("href", ""))
    if not slug:
        raise ValueError("missing J club identity")
    return "jleague:" + slug[1]


def parse_j_schedule(html, year):
    rows = []
    for tr in Document(html).root.find("tr"):
        cells = tr.find("td")
        if len(cells) != 11 or cells[0].text != str(year) or cells[1].text != "Ｊ１":
            continue
        links = cells[6].find("a")
        if len(links) != 1 or "match_card_id=" not in links[0].attrs.get("href", ""):
            continue
        date = re.match(r"(\d{2})/(\d{2})/(\d{2})", cells[3].text)
        if not date or int(date[1]) != year % 100:
            raise ValueError("J season/date mismatch")
        goals = score(cells[6].text)
        kickoff = datetime.fromisoformat(f"{year}-{date[2]}-{date[3]}T{cells[4].text}:00+09:00")
        url = parse.urljoin(J, links[0].attrs["href"])
        event_id = parse.parse_qs(parse.urlsplit(url).query)["match_card_id"][0]
        rows.append(dict(id="jleague:" + event_id, competition_id="jp.j1", season=str(year),
                         stage="regular", round=int(re.search(r"第(\d+)節", cells[2].text)[1]),
                         home_id=j_team(cells[5]), away_id=j_team(cells[7]),
                         home=cells[5].text, away=cells[7].text, kickoff=kickoff.isoformat(),
                         kickoff_basis="official_actual", home_goals=goals[0], away_goals=goals[1],
                         period="regular_time", source_url=url))
    if not rows or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("empty or duplicate J schedule")
    return rows


def enrich_j(row, html):
    root = Document(html).root
    home, away = root.find(id="team-name-l"), root.find(id="team-name-r")
    if len(home) != 1 or len(away) != 1 or [j_team(home[0]), j_team(away[0])] != [row["home_id"], row["away_id"]]:
        raise ValueError("J detail team mismatch")
    full = [int(n.text) for n in root.find("td", **{"class": "score"})]
    halves = {}
    for dl in root.find("dl"):
        dt, dd = dl.find("dt"), dl.find("dd")
        if len(dt) == 1 and dt[0].text in ("前半", "後半") and len(dd) == 2:
            halves[dt[0].text] = [int(n.text) for n in dd]
    if full != [row["home_goals"], row["away_goals"]] or set(halves) != {"前半", "後半"}:
        raise ValueError("J detail score mismatch or missing halves")
    if [a + b for a, b in zip(halves["前半"], halves["後半"])] != full:
        raise ValueError("J extra time or inconsistent period totals")
    return dict(row, home_half_goals=halves["前半"][0], away_half_goals=halves["前半"][1],
                half_score_basis="official_period_score", consistency="schedule_detail_agree")


def parse_k_schedule(payload, year, month):
    if str(payload.get("resultCode")) != "200":
        raise ValueError("K schedule error")
    rows = []
    for r in payload["data"]["scheduleList"]:
        if str(r["leagueId"]) != "1" or str(r["meetSeq"]) != "1" or str(r["year"]) != str(year):
            continue
        if r.get("gameStatus") != "FE" or r.get("endYn") != "Y":
            continue
        if not r["gameDate"].startswith(f"{year}.{month}."):
            raise ValueError("K requested month mismatch")
        goals = score(f"{r['homeGoal']}-{r['awayGoal']}")
        rows.append(dict(id=f"kleague:{year}:1:{r['gameId']}", competition_id="kr.k1", season=str(year),
                         stage="regular" if int(r["roundId"]) <= 33 else {"파이널A": "final_a", "파이널B": "final_b"}.get(r.get("codeName"), "final_unresolved"),
                         source_stage=r.get("codeName"), round=r["roundId"],
                         home_id="kleague:" + r["homeTeam"], away_id="kleague:" + r["awayTeam"],
                         home=r["homeTeamName"], away=r["awayTeamName"],
                         kickoff=r["gameDate"].replace(".", "-") + "T" + r["gameTime"] + ":00+09:00",
                         kickoff_basis="official_schedule", home_goals=goals[0], away_goals=goals[1],
                         period="regular_time", source_url=f"{K}/match.do?year={year}&leagueId=1&gameId={r['gameId']}&meetSeq=1"))
    if not rows or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("empty or duplicate K schedule")
    return sorted(rows, key=lambda r: (r["kickoff"], r["id"]))


def enrich_k(row, payload):
    if str(payload.get("resultCode")) != "200":
        raise ValueError("K detail error")
    d = payload["data"]
    if d.get("gameStatus") != "FE" or [d.get("homeGoal"), d.get("awayGoal")] != [row["home_goals"], row["away_goals"]]:
        raise ValueError("K detail score mismatch")
    if d.get("EfirstHalf") or d.get("EsecondHalf"):
        raise ValueError("extra time not eligible")
    result = dict(row, home_half_goals=None, away_half_goals=None, half_score_basis="unverified",
                  consistency="schedule_detail_agree")
    periods = [d.get("firstHalf") or [], d.get("secondHalf") or []]
    for event in periods[0] + periods[1]:
        if str(event.get("year")) != row["season"] or str(event.get("gameId")) != row["id"].split(":")[-1] or str(event.get("leagueId")) != "1":
            raise ValueError("K detail event identity mismatch")
        if event.get("teamId") and "kleague:" + event["teamId"] not in (row["home_id"], row["away_id"]):
            raise ValueError("K detail team identity mismatch")
    # Do not infer 0-0 from missing events, or guess own-goal attribution.
    if not all(any(e.get("eventName") in ends for e in events) for events, ends in zip(periods, ({"전반종료"}, {"후반종료", "경기종료"}))):
        return result
    if any(s.get("isOwnGoal") for side in ("homeScorer", "awayScorer") for s in d.get(side, [])) or "자책" in json.dumps(periods, ensure_ascii=False):
        return result
    counts = []
    for events in periods:
        goals = [e for e in events if e.get("eventName") == "득점"]
        if any(e.get("homeOrAway") not in ("HOME", "AWAY") for e in goals):
            return result
        counts.append([sum(e["homeOrAway"] == side for e in goals) for side in ("HOME", "AWAY")])
    if [a + b for a, b in zip(*counts)] == [row["home_goals"], row["away_goals"]]:
        result.update(home_half_goals=counts[0][0], away_half_goals=counts[0][1], half_score_basis="official_events_derived")
    return result


def parse_500_odds(html):
    root = Document(html).root
    rows = []
    for tr in root.find("tr"):
        cells = tr.find("td")
        if len(cells) != 4 or not re.fullmatch(r"20\d\d-\d\d-\d\d \d\d:\d\d", cells[3].text):
            continue
        odds = [float(c.text) for c in cells[:3]]
        if not all(1 < x < 10000 for x in odds):
            raise ValueError("invalid odds")
        dt = datetime.fromisoformat(cells[3].text + ":00+08:00")
        rows.append({"odds": dict(zip(("h", "d", "a"), odds)), "source_updated_at": dt.isoformat()})
    if not rows:
        raise ValueError("no timestamped odds rows")
    return sorted(rows, key=lambda r: r["source_updated_at"])


def compare_500(row, candidates, url):
    """Cross-check identity first; a matching score is never a matching criterion."""
    hits = []
    for c in candidates:
        if int(c.get("status", 0)) != 5:
            continue
        if not {c.get("hname"), c.get("hsxname")} & NAMES_500.get(row["home_id"], set()):
            continue
        if not {c.get("gname"), c.get("gsxname")} & NAMES_500.get(row["away_id"], set()):
            continue
        scheduled = datetime.fromisoformat(c["stime"] + ":00+08:00")
        actual = datetime.fromisoformat(row["kickoff"])
        if scheduled.astimezone(actual.tzinfo).date() == actual.date():
            hits.append((c, scheduled, actual))
    if len(hits) != 1:
        return {"status": "unmatched", "candidate_count": len(hits), "source_url": url}
    c, scheduled, actual = hits[0]
    full = score(f"{c['hscore']}-{c['gscore']}")
    half = None
    if c.get("hhalfscore") is not None and c.get("ghalfscore") is not None:
        half = score(f"{c['hhalfscore']}-{c['ghalfscore']}")
        if any(a > b for a, b in zip(half, full)):
            raise ValueError("500 half exceeds full score")
    official_half = [row["home_half_goals"], row["away_half_goals"]]
    ft_ok = full == [row["home_goals"], row["away_goals"]]
    ht_ok = half == official_half if half is not None and None not in official_half else None
    delta = round((actual - scheduled).total_seconds() / 60, 1)
    return dict(status="agree" if ft_ok and ht_ok is not False else "conflict", source_url=url,
                provider_event_id=str(c["fid"]), provider_home_id=str(c["hid"]), provider_away_id=str(c["gid"]),
                home=c["hname"], away=c["gname"], full_score=full, half_score=half,
                full_score_agrees=ft_ok, half_score_agrees=ht_ok, scheduled_kickoff=scheduled.isoformat(),
                kickoff_difference_minutes=delta, kickoff_review_required=abs(delta) > 15,
                odds_usable_for_backtest=False, odds_exclusion="aggregate odds lack observation timestamp")


def cross_check(archive, matches):
    # Season links and stage IDs are discovered from public navigation, not guessed.
    for competition, index in (("jp.j1", "19895"), ("kr.k1", "19554")):
        try:
            root = Document(archive.fetch(f"https://liansai.500.com/zuqiu-{index}/")).root
            for year in YEARS:
                season_paths = {a.attrs.get("href") for a in root.find("a")
                                if a.text == f"{year}赛季" and a.attrs.get("href", "").startswith("/zuqiu-")}
                if len(season_paths) != 1:
                    raise ValueError("ambiguous 500 season navigation")
                season_url = parse.urljoin("https://liansai.500.com", season_paths.pop())
                season_root = Document(archive.fetch(season_url)).root
                schedule_paths = {a.attrs.get("href") for a in season_root.find("a") if "完整赛程" in a.text}
                if len(schedule_paths) != 1:
                    raise ValueError("missing 500 schedule navigation")
                schedule_url = parse.urljoin(season_url, schedule_paths.pop())
                schedule_html = archive.fetch(schedule_url)
                schedule_root = Document(schedule_html).root
                stages = {a.text: a.attrs["data-id"] for a in schedule_root.find("a")
                          if "data-id" in a.attrs and "/jifen-" in a.attrs.get("href", "")}
                default_stage = re.search(r"stageId\s*=\s*(\d+)", schedule_html)
                for row in matches:
                    if row["competition_id"] != competition or row["season"] != str(year):
                        continue
                    try:
                        stage_id = default_stage[1] if competition == "jp.j1" else k_stage_id(stages, row["stage"])
                        provider_round = row["round"]
                        if competition == "kr.k1" and row["stage"] != "regular":
                            stage_path = next(a.attrs["href"] for a in schedule_root.find("a")
                                              if a.attrs.get("data-id") == stage_id)
                            stage_html = archive.fetch(parse.urljoin(season_url, stage_path))
                            rounds_match = re.search(r"allRounds\s*=\s*(\[[\d,\s]+\])", stage_html)
                            if not rounds_match:
                                raise ValueError("missing stage round metadata")
                            provider_round = k_provider_round(row["round"], json.loads(rounds_match[1]))
                        url = f"https://liansai.500.com/index.php?c=score&a=getmatch&stid={stage_id}&round={provider_round}"
                        candidates = json.loads(archive.fetch(url))
                        if not isinstance(candidates, list):
                            raise ValueError("500 round schema changed")
                        row["cross_check"] = compare_500(row, candidates, url)
                        row["independent_verification"] = row["cross_check"]["status"]
                    except Exception as e:
                        row["cross_check"] = {"status": "failed", "error": str(e)}
        except Exception as e:
            for row in matches:
                if row["competition_id"] == competition and "cross_check" not in row:
                    row["cross_check"] = {"status": "failed", "error": str(e)}


def k_stage_id(stages, stage):
    names = {"regular": ("联赛", "联赛赛程"), "final_a": ("冠军赛", "冠军附加"),
             "final_b": ("降级赛", "降级附加")}[stage]
    ids = {stages[name] for name in names if name in stages}
    if len(ids) != 1:
        raise ValueError("ambiguous or missing K competition stage")
    return ids.pop()


def k_provider_round(official_round, available):
    if official_round in available:
        return official_round
    if set(available) == {1, 2, 3, 4, 5} and 34 <= official_round <= 38:
        return official_round - 33
    raise ValueError("unverified K stage round convention")


def write_readable_report(output, report):
    matches = report["matches"]
    checks = [m.get("cross_check", {}) for m in matches]
    summary = {
        "official_samples": len(matches),
        "official_half_scores": sum(m["home_half_goals"] is not None for m in matches),
        "cross_source_full_scores_agree": sum(c.get("full_score_agrees") is True for c in checks),
        "cross_source_half_scores_agree": sum(c.get("half_score_agrees") is True for c in checks),
        "cross_source_unmatched_or_failed": sum(c.get("status") in ("unmatched", "failed") for c in checks),
        "cross_source_conflicts": sum(c.get("status") == "conflict" for c in checks),
        "kickoff_reviews": sum(c.get("kickoff_review_required", False) for c in checks),
    }
    report["summary"] = summary
    lines = ["# 日韩历史数据小样本核验", "", f"生成时间：{report['generated_at']}", "",
             "用途：数据源可行性核验。不是模型准确率测试，也不代表全赛季覆盖或商用授权。", "",
             "## 结果", "", f"- 官方比赛样本：{summary['official_samples']} 场；取得半场比分 {summary['official_half_scores']} 场。",
             f"- 与 500 交叉核对：全场一致 {summary['cross_source_full_scores_agree']} 场；半场一致 {summary['cross_source_half_scores_agree']} 场。",
             f"- 比分冲突 {summary['cross_source_conflicts']} 场；未匹配或失败 {summary['cross_source_unmatched_or_failed']} 场。",
             f"- 开赛时间差异超过 15 分钟：{summary['kickoff_reviews']} 场，需核查。", "",
             "## 样本", "", "| 赛季 | 联赛 | 比赛 | 全场 | 官方半场 | 500 半场 | 核对 | 时间差/分钟 |",
             "|---|---|---|---|---|---|---|---|"]
    for m in matches:
        c = m.get("cross_check", {})
        half = "缺失" if m["home_half_goals"] is None else f"{m['home_half_goals']}-{m['away_half_goals']}"
        other_half = "-" if c.get("half_score") is None else "-".join(map(str, c["half_score"]))
        names = c.get("home", m["home"]) + " vs " + c.get("away", m["away"])
        status = {"agree": "比分一致", "conflict": "冲突", "failed": "失败", "unmatched": "未匹配"}.get(c.get("status"), "待核对")
        lines.append(f"| {m['season']} | {m['competition_id']} | [{names}]({m['source_url']}) | {m['home_goals']}-{m['away_goals']} | {half} | {other_half} | {status} | {c.get('kickoff_difference_minutes', '-')} |")
    lines += ["", "## 覆盖范围", "", "| 联赛 | 赛季 | 读取范围 | 赛程记录数 |", "|---|---|---|---|"]
    for c in report["coverage"]:
        lines.append(f"| {c['competition_id']} | {c['season']} | {c['scope']} | {c['index_rows']} |")
    lines += ["", "上述为赛程索引记录数，不是已核验或已导入训练的比赛数。样本选择固定，并非随机抽样。", "",
              "## 数据边界", "",
              "- 日职官方时间标为实际开球时间；500 为赛程时间。两个字段分开保留，不能直接互相覆盖。",
              "- 韩职常规赛、争冠组和保级组分别映射；升降级附加赛不混入 K1 联赛训练。",
              "- 韩职半场比分从完整比赛事件推导，并与全场进球数校验；缺少终场标记或涉及未确认的乌龙球归属时留空。",
              "- 500 的轮次接口有半全场比分与汇总赔率；汇总赔率没有观察时间，不用于 T−60 分钟或收益回测。",
              "- 500 单场奖金走势是单独的可行性样本，尚未完成比赛 ID 和让球数联合核验，不进入模型。",
              "- 每份响应保存 URL、请求参数、采集时间、原始内容和 SHA-256。缓存复用保留最初采集时间。",
              "- result_available_at 留空，不将采集时间伪装成历史赛果发布时间；尚未导入训练。",
              "- 公开访问已验证；训练和收费产品用途的使用依据未确认，rights_status=unverified。", "",
              "## 补充来源探测", "", "| 来源 | 结果 |", "|---|---|"]
    for p in report["probes"]:
        detail = p.get("error") or (f"取得 {len(p['odds_history'])} 条带时间的赔率记录" if "odds_history" in p else "取得页面；不代表全历史数据已验证")
        lines.append(f"| [{p['source']}]({p['url']}) | {detail} |")
    lines += ["", "## 后续验收", "",
              "1. 确认来源的训练和产品使用依据，补齐球队 ID 对照表；采集程序不能代替授权判断。",
              "2. 核查时间差异和缺失半场记录，明确历史赛果可用时间的来源或保守假设。",
              "3. 扩展为三赛季全量赛果，核对总场次、球队场次、重复、缺失及阶段；仍先在隔离区验收。",
              "4. 通过后导入现有训练入口，按时间切分回测；赔率历史单独验收。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf8")


class Archive:
    def __init__(self, path, offline=False):
        self.path = Path(path)
        self.offline = offline
        self.path.mkdir(parents=True, exist_ok=True)
        self.entries = []
        self.opener = request.build_opener(request.ProxyHandler({}))
        self.last_request = 0
        self.transient_failures = 0

    def fetch(self, url, body=None):
        encoded = json.dumps(body, sort_keys=True).encode() if body is not None else None
        key = hashlib.sha256(url.encode() + (encoded or b"")).hexdigest()
        raw_path, meta_path = self.path / (key + ".body"), self.path / (key + ".json")
        denied_path = self.path / (key + ".denied.json")
        if denied_path.exists():
            meta = json.loads(denied_path.read_text(encoding="utf8"))
            self.entries.append(meta)
            raise ValueError(meta["error"] + " (cached; no automatic retry)")
        if raw_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf8"))
            raw = raw_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
                raise ValueError("cached response checksum mismatch")
            self.entries.append(meta)
            return raw.decode(meta["encoding"])
        if self.offline:
            raise ValueError("response not present in offline archive")
        if self.transient_failures >= 3:
            raise ValueError("来源连续失败，已暂停本轮网络请求")
        time.sleep(max(0, 1 - (time.monotonic() - self.last_request)))
        meta = {"url": url, "method": "POST" if body is not None else "GET", "body": body,
                "collected_at": datetime.now(timezone.utc).isoformat()}
        try:
            req = request.Request(url, data=encoded, headers={"User-Agent": "FootballHistoryAudit/1.0", "Content-Type": "application/json"})
            with self.opener.open(req, timeout=25) as response:
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise ValueError("response exceeds audit size limit")
                encoding = response.headers.get_content_charset() or "utf-8"
                try:
                    raw.decode(encoding)
                except UnicodeDecodeError:
                    encoding = "gb18030"
                meta.update(status=response.status, final_url=response.url, encoding=encoding,
                            sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), raw_file=str(raw_path))
            raw_path.write_bytes(raw)
            self.transient_failures = 0
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf8")
            return raw.decode(encoding)
        except Exception as e:
            meta.update(error=f"{type(e).__name__}: {e}")
            if getattr(e, "code", 0) >= 500 or isinstance(e, (TimeoutError, error.URLError)):
                self.transient_failures += 1
            if getattr(e, "code", None) in (403, 429):
                denied_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf8")
            if isinstance(e, error.HTTPError):
                e.close()
            raise
        finally:
            self.last_request = time.monotonic()
            self.entries.append(meta)


def run(output, offline=False):
    output = Path(output)
    archive = Archive(output / "raw", offline=offline)
    matches, coverage, errors = [], [], []

    def save_match(row):
        matches.append(dict(row, collected_at=archive.entries[-1]["collected_at"],
                            result_available_at=None, training_eligible=False,
                            independent_verification="pending"))

    for year in YEARS:
        try:
            url = f"{J}/SFMS01/search?competition_years={year}&competition_frame_ids=1"
            rows = parse_j_schedule(archive.fetch(url), year)
            coverage.append({"competition_id": "jp.j1", "season": str(year), "scope": "season_index",
                             "index_rows": len(rows), "source_url": url})
            for row in (rows[0], rows[-1]):
                try:
                    save_match(enrich_j(row, archive.fetch(row["source_url"])))
                except Exception as e:
                    errors.append({"source": row["source_url"], "error": str(e)})
        except Exception as e:
            errors.append({"source": url, "error": str(e)})
        for month in ("03", "11"):
            url = K + "/getScheduleList.do"
            try:
                body = dict(leagueId="1", teamId="", ticketStatus="", year=str(year), month=month, ticketYn="")
                rows = parse_k_schedule(json.loads(archive.fetch(url, body)), year, month)
                coverage.append({"competition_id": "kr.k1", "season": str(year), "scope": "month:" + month,
                                 "index_rows": len(rows), "source_url": url})
                row = rows[0]
                detail = f"{K}/api/ddf/match/matchInfo.do?year={year}&meetSeq=1&gameId={row['id'].split(':')[-1]}"
                save_match(dict(enrich_k(row, json.loads(archive.fetch(detail))), detail_url=detail))
            except Exception as e:
                errors.append({"source": url, "year": year, "month": month, "error": str(e)})
        print(f"{year}: {len(matches)} samples, {len(errors)} errors", flush=True)

    probes = []
    for name, url in [
        ("500_odds", "https://zx.500star.com/common/jjzs_tubiao.php?gg=2&id=166420&lhash=e34131e3&lot=1&play=1"),
        ("500_j1_index", "https://liansai.500.com/zuqiu-19895/"),
        ("dongqiudi_report", "https://n.dongqiudi.com/webapp/news.html?articleId=4278514"),
    ]:
        try:
            html = archive.fetch(url)
            item = {"source": name, "url": url, "status": "response_received"}
            if name == "500_odds":
                item.update(odds_history=parse_500_odds(html), match_mapping="pending",
                            market="hhad", handicap=None)
            else:
                item["visible_text_preview"] = Document(html).root.text[:180]
                item["structured_history_verified"] = False
            probes.append(item)
        except Exception as e:
            probes.append({"source": name, "url": url, "status": "failed", "error": str(e)})
    cross_check(archive, matches)
    report = dict(schema="club-source-audit-v1", generated_at=datetime.now(timezone.utc).isoformat(),
                  purpose="feasibility_only", rights_status="unverified", training_imported=False,
                  sampling="J first/last completed match per season; K first completed March/November match",
                  matches=matches, coverage=coverage, probes=probes, errors=errors, requests=archive.entries)
    write_readable_report(output, report)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"samples": len(matches), "half_scores": sum(r["home_half_goals"] is not None for r in matches),
                      "errors": len(errors), "report": str(output / "report.json")}, ensure_ascii=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/training/source_audit")
    parser.add_argument("--offline", action="store_true", help="Reparse archived responses without network access")
    args = parser.parse_args()
    run(args.output, offline=args.offline)
