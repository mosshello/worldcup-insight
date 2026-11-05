import { test } from "node:test";
import assert from "node:assert/strict";
import {
  movement,
  optionValue,
  trendRows,
  quoteTime,
  quoteInterval,
  relativeMovement,
  kickoffDistance,
} from "../src/oddsTrend.js";
import { currentSelectedOdds } from "../src/dailyOdds.js";

test("official odds and normalized implied probability stay separate", () => {
  const pool = {
    options: [
      { key: "h", odds: 2 },
      { key: "d", odds: 4 },
      { key: "a", odds: 4 },
    ],
  };
  assert.equal(optionValue(pool, "h"), 2);
  assert.equal(optionValue(pool, "h", "probability"), 50);
  assert.equal(
    optionValue(
      {
        ...pool,
        options: [
          { key: "h", odds: 2 },
          { key: "a", odds: null },
        ],
      },
      "h",
      "probability",
    ),
    null,
  );
  assert.equal(optionValue(pool, "missing"), null);
  assert.equal(movement(1.9, 2).text, "↓ -0.10");
  assert.equal(movement(2, 2).text, "持平");
});
test("history excludes future points and different handicap lines", () => {
  const rows = [
    { line: -1, updated_at: "2026-11-05T00:00:00Z" },
    { line: -2, updated_at: "2026-11-05T01:00:00Z" },
    { line: -2, updated_at: "2026-11-06T01:00:00Z" },
  ];
  assert.equal(
    trendRows(
      rows,
      { line: -2 },
      "hhad",
      "all",
      Date.parse("2026-11-05T02:00:00Z"),
    ).length,
    1,
  );
});
test("live selection quotes update independently and cannot cross handicap lines", () => {
  const forecast = {
    code: "hhad",
    line: -1,
    selected_keys: ["h"],
    selection: "让胜",
    options: [{ key: "h", label: "让胜", odds: 2 }],
  };
  const row = {
    forecast: { market_assessments: [forecast] },
    current_quotes: {
      markets: {
        hhad: { ...forecast, options: [{ key: "h", label: "让胜", odds: 3 }] },
      },
    },
  };
  assert.equal(currentSelectedOdds(row, "hhad"), "最新SP 让胜 @ 3.00");
  assert.equal(forecast.options[0].odds, 2);
  row.current_quotes.stale = true;
  assert.match(currentSelectedOdds(row, "hhad"), /待更新/);
  row.current_quotes.markets.hhad.line = -2;
  assert.match(currentSelectedOdds(row, "hhad"), /让球线已变化/);
});

test("interval comparisons use the previous actual record, including outside a display window", () => {
  const rows = [
    { updated_at: "2026-11-04T23:50:00Z" },
    { updated_at: "2026-11-05T01:10:00Z" },
    { updated_at: "2026-11-05T02:00:00Z" },
  ];
  assert.equal(quoteInterval(rows, rows[0]).previous, null);
  assert.equal(quoteInterval(rows, rows[1]).previous, rows[0]);
  assert.equal(
    quoteInterval(rows, rows[1]).label,
    "11/05 07:50:00 → 11/05 09:10:00",
  );
  assert.equal(quoteTime("bad"), "时间未知");
  assert.equal(relativeMovement(2.2, 2), "+10.00%");
  assert.equal(relativeMovement(1.9, 2), "-5.00%");
  assert.equal(relativeMovement(49, 50, "probability"), "-1.00个百分点");
  assert.equal(relativeMovement(2, null), "无可比记录");
});

test("kickoff distance is measured at the historical quote and handles day boundaries", () => {
  assert.equal(
    kickoffDistance("2026-11-06T10:00:00Z", "2026-11-05T08:29:00Z"),
    "当时距开赛 1天1小时31分",
  );
  assert.equal(
    kickoffDistance("2026-11-06T10:00:00Z", "2026-11-06T09:59:40Z"),
    "当时距开赛不足1分钟",
  );
  assert.equal(
    kickoffDistance("2026-11-06T10:00:00Z", "2026-11-06T10:00:00Z"),
    "已到开赛时间",
  );
  assert.equal(kickoffDistance(null, "2026-11-06T10:00:00Z"), "开赛时间待确认");
});
