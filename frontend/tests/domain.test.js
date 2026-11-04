import test from "node:test";
import assert from "node:assert/strict";
import { adaptMatch, beijingDateTime } from "../src/api.js";
import { price, toCsv } from "../src/domain.js";

test('kickoff display and date filter share Beijing time across UTC midnight and source offsets', () => {
  const utc = adaptMatch({ kickoff: '2026-11-04T16:30:00Z', sale_date: '2026-11-04', markets: {} });
  assert.equal(utc.kickoffLabel, '2026-11-05 00:30');
  assert.equal(utc.kickoffDate, '2026-11-05');
  assert.equal(utc.date, '2026-11-04');
  assert.equal(beijingDateTime('2026-11-05T01:30:00+09:00'), utc.kickoffLabel);
  assert.equal(beijingDateTime('2026-11-04T16:00:00Z'), '2026-11-05 00:00');
  assert.equal(beijingDateTime(null), '待定');
});
test("missing market odds remain missing instead of becoming sample prices", () => {
  const m = adaptMatch({
    id: "sporttery:1",
    kickoff: "2099-01-02T12:00:00+08:00",
    sale_date: "2099-01-01",
    sale_status: "unknown",
    markets: {},
  });
  assert.deepEqual(m.odds, [undefined, undefined, undefined]);
  assert.equal(m.sale, "unknown");
  assert.equal(m.date, "2099-01-01");
  assert.equal(price(null), "—");
  assert.equal(price(Infinity), "—");
});
test("market-specific singles survive adaptation independently", () => {
  const m = adaptMatch({
    id: "sporttery:1",
    kickoff: "2099-01-02T12:00:00+08:00",
    markets: {
      had: {
        single: false,
        options: [
          { key: "h", odds: 2 },
          { key: "d", odds: 3 },
          { key: "a", odds: 4 },
        ],
      },
      ttg: { single: true, options: [{ key: "s2", odds: 3 }] },
    },
  });
  assert.deepEqual(m.odds, [2, 3, 4]);
  assert.equal(m.single, true);
  assert.equal(m.pools.had.single, false);
  assert.equal(m.pools.ttg.single, true);
});
test("CSV preserves Unicode newlines and quotes while blocking formula evaluation", () => {
  const csv = toCsv(
    [{ note: "=SUM(1,2)" }, { note: '中文"备注\n第二行' }],
    [{ key: "note", title: "笔记" }],
  );
  assert.ok(csv.startsWith("\uFEFF"));
  assert.ok(csv.includes('"\'=SUM(1,2)"'));
  assert.ok(csv.includes('"中文""备注\n第二行"'));
});
