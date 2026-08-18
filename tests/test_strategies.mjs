import assert from "node:assert/strict";
import test from "node:test";

import { normalizeForRange } from "../js/charts.js";
import {
  compareCards,
  getAvailableStrategyEntries,
  getReturnSummary,
  STRATEGY_META,
} from "../js/strategies.js";

function card(sid, halfKelly, sharpe12m = null, sharpe3m = null) {
  return {
    sid,
    ret12m: null,
    stratSharpe12m: sharpe12m,
    stratSharpe3m: sharpe3m,
    tradeStats: { half_kelly_pct: halfKelly },
  };
}

test("Size sorts descending by half-Kelly and puts missing estimates last", () => {
  const cards = [card("zero", 0), card("missing", null), card("large", 19.66), card("small", 6.01)];
  cards.sort((a, b) => compareCards(a, b, "size"));
  assert.deepEqual(cards.map(item => item.sid), ["large", "small", "zero", "missing"]);
});

test("Sharpe falls back to the available 3M value", () => {
  const cards = [card("low", 0, null, -0.2), card("missing", 0), card("high", 0, null, 1.4)];
  cards.sort((a, b) => compareCards(a, b, "sharpe"));
  assert.deepEqual(cards.map(item => item.sid), ["high", "low", "missing"]);
});

test("since-inception return uses the original 100 NAV baseline", () => {
  const summary = getReturnSummary([
    { date: "2026-01-02", value: 99, return_12m: null },
    { date: "2026-01-05", value: 110, return_12m: null },
  ]);
  assert.ok(Math.abs(summary.value - 10) < 1e-12);
});

test("all-history chart preserves NAV while shorter ranges rebase", () => {
  const series = [{ value: 99 }, { value: 108 }];
  assert.equal(normalizeForRange(series, "all"), series);
  const rebased = normalizeForRange(series, "3m").map(point => point.value);
  assert.equal(rebased[0], 100);
  assert.ok(Math.abs(rebased[1] - 109.0909090909091) < 1e-12);
});

test("Munger400L and Munger400R each have two cards and wait for processed series", () => {
  assert.equal(STRATEGY_META.munger400l.label, "Munger400L EMA21");
  assert.equal(STRATEGY_META.munger400l_sma10.label, "Munger400L SMA10");
  assert.equal(STRATEGY_META.munger400r.label, "Munger400R EMA21");
  assert.equal(STRATEGY_META.munger400r_sma10.label, "Munger400R SMA10");

  const beforeSection = getAvailableStrategyEntries({ munger: [] });
  assert.deepEqual(beforeSection.map(([sid]) => sid), ["munger"]);

  const afterSection = getAvailableStrategyEntries({
    munger: [],
    munger400l: [],
    munger400l_sma10: [],
    munger400r: [],
    munger400r_sma10: [],
  });
  assert.deepEqual(
    afterSection.map(([sid]) => sid),
    ["munger", "munger400l", "munger400r", "munger400l_sma10", "munger400r_sma10"],
  );
});
