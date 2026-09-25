import assert from "node:assert/strict";
import test from "node:test";

import { normalizeForRange } from "../js/charts.js";
import {
  compareCards,
  getAvailableStrategyEntries,
  getReturnSummary,
  STRATEGY_META,
  summarizeLots,
} from "../js/strategies.js";

function card(sid, halfKelly, sharpe12m = null, sharpeInception = null) {
  return {
    sid,
    ret12m: null,
    stratSharpe12m: sharpe12m,
    stratSharpeInception: sharpeInception,
    tradeStats: { half_kelly_pct: halfKelly },
  };
}

test("Size sorts descending by half-Kelly and puts missing estimates last", () => {
  const cards = [card("zero", 0), card("missing", null), card("large", 19.66), card("small", 6.01)];
  cards.sort((a, b) => compareCards(a, b, "size"));
  assert.deepEqual(cards.map(item => item.sid), ["large", "small", "zero", "missing"]);
});

test("Sharpe prefers 12M and falls back to the full-period value", () => {
  const cards = [
    card("low", 0, null, -0.2), card("missing", 0), card("high", 0, null, 1.4), card("year", 0, 0.5, 3.0),
  ];
  cards.sort((a, b) => compareCards(a, b, "sharpe"));
  assert.deepEqual(cards.map(item => item.sid), ["high", "year", "low", "missing"]);
});

test("since-inception return uses the original 100 NAV baseline", () => {
  const summary = getReturnSummary([
    { date: "2026-01-02", value: 99, return_12m: null },
    { date: "2026-01-05", value: 110, return_12m: null },
  ]);
  assert.ok(Math.abs(summary.value - 10) < 1e-12);
  assert.equal(summary.label, "Since 2026-01-02");
});

test("12M return uses a short card label", () => {
  const summary = getReturnSummary([
    { date: "2025-01-02", value: 100, return_12m: null },
    { date: "2026-01-05", value: 110, return_12m: 12.5 },
  ]);
  assert.equal(summary.value, 12.5);
  assert.equal(summary.label, "12M Price");
});

test("all-history chart preserves NAV while shorter ranges rebase", () => {
  const series = [{ value: 99 }, { value: 108 }];
  assert.equal(normalizeForRange(series, "all"), series);
  const rebased = normalizeForRange(series, "3m").map(point => point.value);
  assert.equal(rebased[0], 100);
  assert.ok(Math.abs(rebased[1] - 109.0909090909091) < 1e-12);
});

test("industry rank-change cards appear only after their series exists", () => {
  assert.equal(STRATEGY_META.industry_up5.label, "Rank Gains Top 5 · Below SMA10");
  assert.equal(STRATEGY_META.industry_down5.label, "Rank Losses Top 5 · Above SMA10");

  const before = getAvailableStrategyEntries({ sp500_top5: [] });
  assert.equal(before.some(([sid]) => sid.startsWith("industry_")), false);

  const after = getAvailableStrategyEntries({
    sp500_top5: [],
    industry_up5: [],
    industry_down5: [],
  });
  assert.deepEqual(
    after.map(([sid]) => sid).filter(sid => sid.startsWith("industry_")),
    ["industry_up5", "industry_down5"],
  );
});

test("Munger400L and Munger400R wait for processed series", () => {
  assert.equal(STRATEGY_META.munger400l.label, "Munger400L EMA21");
  assert.equal(STRATEGY_META.munger400r.label, "Munger400R EMA21");

  const beforeSection = getAvailableStrategyEntries({ munger: [] });
  assert.deepEqual(beforeSection.map(([sid]) => sid), ["munger"]);

  const afterSection = getAvailableStrategyEntries({
    munger: [],
    munger400l: [],
    munger400r: [],
  });
  assert.deepEqual(afterSection.map(([sid]) => sid), ["munger", "munger400l", "munger400r"]);
});

test("Mega Laggards 2 holds 21 sessions and groups open lots by ticker", () => {
  assert.equal(STRATEGY_META.megalaggards2.label, "Mega Laggards 2 · Hold 21");
  assert.equal(
    Object.keys(STRATEGY_META).some(sid => sid.endsWith("_sma10")),
    false,
  );

  const lots = summarizeLots([
    { ticker: "META" }, { ticker: "TSLA" }, { ticker: "TSLA" }, { ticker: "AVGO" }, { ticker: "TSLA" },
  ]);
  assert.deepEqual(lots, [
    { ticker: "TSLA", lots: 3 },
    { ticker: "AVGO", lots: 1 },
    { ticker: "META", lots: 1 },
  ]);
});

test("Rank Momentum cards cover top and next five of each index", () => {
  assert.equal(STRATEGY_META.rankmom500_top5.label, "S&P 500 Rank Momentum Top 5");
  assert.equal(STRATEGY_META.rankmom500_next5.label, "S&P 500 Rank Momentum Next 5");
  assert.equal(STRATEGY_META.rankmom400_top5.label, "S&P 400 Rank Momentum Top 5");
  assert.equal(STRATEGY_META.rankmom400_next5.label, "S&P 400 Rank Momentum Next 5");

  const entries = getAvailableStrategyEntries({ sp500_top5: [], rankmom500_top5: [] });
  assert.deepEqual(entries.map(([sid]) => sid), ["sp500_top5", "rankmom500_top5"]);
});
