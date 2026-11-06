"""Freeze official Sporttery results, HHAD histories and ESPN schedules for this research batch."""
import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import build_opener, ProxyHandler, Request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/research/big-five/2026-11-06/challenger"
LEAGUES = {"eng.premier": ("eng.1", 25, "E0"), "esp.laliga": ("esp.1", 62, "SP1"),
           "ger.bundesliga": ("ger.1", 37, "D1"), "ita.serie_a": ("ita.1", 40, "I1"),
           "fra.ligue1": ("fra.1", 32, "F1")}
SPORT = "https://webapi.sporttery.cn/gateway/uniform/football/"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fetch(key, url):
    root = OUT / "sources"
    root.mkdir(exist_ok=True, parents=True)
    receipt = root / (key + ".json")
    if receipt.exists():
        meta = json.loads(receipt.read_bytes())
        raw = gzip.decompress((root / (meta["sha256"] + ".gz")).read_bytes())
        if sha(raw) != meta["sha256"] or url != meta["url"]:
            raise ValueError("Source receipt mismatch")
        return json.loads(raw)
    for attempt in range(4):
        try:
            headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sporttery.cn/"}
            with build_opener(ProxyHandler({})).open(Request(url, headers=headers), timeout=16) as resp:
                raw = resp.read()
            data = json.loads(raw)
            if data.get("success") is False or ("sporttery.cn" in url and not isinstance(data.get("value"), dict)):
                raise ValueError(str(data.get("errorMessage")))
            digest = sha(raw)
            (root / (digest + ".gz")).write_bytes(gzip.compress(raw, mtime=0))
            meta = {"url": url, "sha256": digest, "observed_at": datetime.now(timezone.utc).isoformat()}
            tmp = receipt.with_suffix(".tmp")
            tmp.write_text(json.dumps(meta), encoding="utf8")
            tmp.replace(receipt)
            return data
        except (OSError, ValueError):
            if attempt == 3:
                raise
            time.sleep(attempt + 1)


def month_rows(period):
    year, month = period
    end = min(date(2026, 11, 5), date(year, month, calendar.monthrange(year, month)[1]))
    combined = []
    page = 1
    while True:
        query = dict(matchBeginDate=f"{year:04}-{month:02}-01", matchEndDate=end.isoformat(),
                     matchPage=1, pcOrWap=0, leagueId="", pageNo=page, pageSize=1000)
        data = fetch(f"list-{year}-{month:02}-{page}", SPORT + "getUniformMatchResultV1.qry?" + urlencode(query))["value"]
        combined.extend(data.get("matchResult", []))
        if page >= int(data["pages"]):
            if len(combined) != int(data["total"]) or len({r["matchId"] for r in combined}) != len(combined):
                raise ValueError(f"Pagination mismatch {year}-{month}")
            return combined
        page += 1


def detail(row):
    mid = str(row["matchId"])
    try:
        value = fetch("detail-" + mid, SPORT + "getFixedBonusV1.qry?" +
                      urlencode(dict(clientCode=3001, matchId=mid)))["value"]
        if str(value.get("oddsHistory", {}).get("matchId")) != mid:
            return mid, "identity_missing"
        return mid, "ok"
    except (OSError, ValueError) as exc:
        return mid, str(exc)


def schedules():
    for cid, (slug, _, _) in LEAGUES.items():
        for day in range(6, 10):
            fetch(f"schedule-{cid}-{day}", f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates=202611{day:02}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--schedule", action="store_true")
    if ap.parse_args().schedule:
        schedules()
    else:
        periods = [(y, m) for y in range(2024, 2027) for m in range(1, 13) if (2024, 7) <= (y, m) <= (2026, 11)]
        with ThreadPoolExecutor(4) as pool:
            rows = {r["matchId"]: r for chunk in pool.map(month_rows, periods) for r in chunk
                    if r["leagueId"] in {league for _, league, _ in LEAGUES.values()}}
        print("Official list", len(rows), flush=True)
        with ThreadPoolExecutor(6) as pool:
            status = dict(pool.map(detail, rows.values()))
        (OUT / "official-index.json").write_text(json.dumps({"matches": rows, "detail_status": status},
                                              ensure_ascii=False, sort_keys=True), encoding="utf8")
        print("Verified detail", sum(v == "ok" for v in status.values()), "of", len(rows), flush=True)
