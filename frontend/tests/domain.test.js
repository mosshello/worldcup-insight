import test from "node:test";
import assert from "node:assert/strict";
import {
  filterMatches,
  normalizeLiveMatch,
  validTrainingRange,
  toCsv,
  price,
} from "../src/domain.js";
test("official pending match cannot appear in on-sale filter", () => {
  const m = normalizeLiveMatch({
    match_id: 1,
    sale_status: "pending",
    pools: {},
    home: "主队",
    away: "客队",
  });
  assert.equal(filterMatches([m], {}).length, 0);
  assert.equal(filterMatches([m], { sale: "" }).length, 1);
  assert.deepEqual(m.odds, []);
  assert.equal(m.stats, null);
});
test("live prices and pool availability use source data; no sample fill", () => {
  const m = normalizeLiveMatch({
    match_id: 2,
    sale_status: "selling",
    pools: { had: { home: 2, draw: 3, away: 4 } },
    betting_single: false,
  });
  assert.deepEqual(m.odds, [2, 3, 4]);
  assert.deepEqual(m.markets, ["had"]);
  assert.equal(m.single, false);
  assert.equal(price(null), "—");
});
test("date league market favorites and single filters intersect", () => {
  const m = {
    id: "a",
    date: "2026-10-28",
    league: "欧冠",
    markets: ["had"],
    home: "阿森纳",
    away: "国际米兰",
    num: "001",
    sale: "selling",
    single: true,
  };
  assert.equal(
    filterMatches(
      [m],
      {
        date: m.date,
        league: m.league,
        market: "had",
        query: " 国 ",
        single: true,
        followed: true,
      },
      ["a"],
    ).length,
    1,
  );
  assert.equal(filterMatches([m], { market: "crs" }).length, 0);
  assert.equal(filterMatches([m], { followed: true }, []).length, 0);
});
test("training periods reject overlapping dates and invalid calendar dates", () => {
  assert.equal(
    validTrainingRange("2022-07-01", "2025-06-30", "2026-06-30"),
    true,
  );
  assert.equal(
    validTrainingRange("2025-01-01", "2025-01-01", "2026-01-01"),
    false,
  );
  assert.equal(
    validTrainingRange("2025-02-30", "2025-03-01", "2026-01-01"),
    false,
  );
  assert.equal(validTrainingRange("", "", ""), false);
});
test("CSV handles quotes line breaks and formula injection in notes", () => {
  const result = toCsv(
    [{ note: "=SUM(1,2)" }, { note: 'a"b\nc' }],
    [{ title: "笔记", key: "note" }],
  );
  assert.ok(result.startsWith("\uFEFF"));
  assert.ok(result.includes('"\'=SUM(1,2)"'));
  assert.ok(result.includes('"a""b\nc"'));
});
