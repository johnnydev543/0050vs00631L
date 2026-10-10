"""Small synthetic accounting tests. These are not market-performance data."""

import json
import math
import tempfile
import unittest
from datetime import date
from pathlib import Path

from backtest import (Costs, DAYS_PER_YEAR, OPEN_TO_CLOSE_DAYS, Strategy,
                      drawdown_statistics, run_backtest, standard_strategies,
                      target_trade, write_run)


FREE = Costs(0, 0, 0)


def fixture(rows, splits=None, dividends=None, missing=None):
    """Rows are (date, open, close); an open of None means verified suspension."""
    calendar = [row[0] for row in rows]
    prices = {day: {"open": opening, "close": close, "Trading_Volume": 10}
              for day, opening, close in rows if opening is not None}
    absent = [day for day, opening, _ in rows if opening is None]
    return {"start": calendar[0], "end": calendar[-1], "calendar": calendar,
            "prices": {"0050": prices, "00631L": dict(prices)},
            "splits": {"0050": splits or [], "00631L": splits or []},
            "dividends": {"0050": dividends or [], "00631L": []},
            "metadata": {"verified_suspensions": {
                "0050": absent if missing is None else missing,
                "00631L": absent if missing is None else missing}}}


class AccountingTests(unittest.TestCase):
    def test_buy_and_sell_hit_target_after_costs(self):
        costs = Costs()
        for units, cash, target in [(0, 1_000_000, 0.8), (9000, 100_000, 0.5)]:
            new_units, new_cash, trade = target_trade(units, cash, 0, 100, target, costs)
            self.assertGreaterEqual(new_cash, 0)
            self.assertAlmostEqual(trade["weight_after"], target, places=13)
            self.assertAlmostEqual(trade["nav_before"] - trade["nav_after"],
                                   trade["total_cost"], places=8)
            self.assertAlmostEqual(new_units * 100 + new_cash, trade["nav_after"], places=8)
        _, remaining, trade = target_trade(5, 10, 100, 100, 1, costs)
        self.assertAlmostEqual(remaining, 0, places=10)
        self.assertLess(trade["weight_after"], 1)  # Receivables cannot fund purchases.

    def test_threshold_uses_next_open_not_trigger_close(self):
        data = fixture([("2020-01-02", 100, 150), ("2020-01-03", 120, 120),
                        ("2020-01-06", 120, 120)])
        result = run_backtest(data, Strategy("test", "00631L", .5, "threshold"),
                              cash_yield=0, initial=1000, costs=FREE)
        self.assertEqual([t["date"] for t in result["trades"]],
                         ["2020-01-02", "2020-01-03"])
        self.assertEqual(result["daily"][0]["pending_rebalance"], "threshold")
        self.assertEqual(result["trades"][1]["raw_open"], 120)
        self.assertEqual(result["trades"][1]["reason"], "threshold")
        self.assertAlmostEqual(result["daily"][0]["nav"], 1250)
        self.assertAlmostEqual(result["daily"][1]["nav"], 1100)

    def test_split_and_suspension_do_not_create_return(self):
        data = fixture([("2020-01-02", 100, 100), ("2020-01-03", None, None),
                        ("2020-01-06", 25, 25)],
                       splits=[{"date": "2020-01-03", "ratio": 4}])
        result = run_backtest(data, Strategy("test", "0050", 1, "buyhold"),
                              cash_yield=0, initial=1000, costs=FREE)
        self.assertEqual([round(r["nav"], 8) for r in result["daily"]], [1000] * 3)
        self.assertEqual(result["daily"][1]["units"], 40)
        self.assertEqual(result["daily"][1]["close"], 25)
        self.assertEqual(result["daily"][1]["return"], 0)
        self.assertTrue(result["daily"][1]["price_stale"])

    def test_dividend_receivable_payment_and_later_reinvestment(self):
        data = fixture([("2020-01-02", 100, 100), ("2020-01-03", 90, 90),
                        ("2020-01-06", 90, 90), ("2020-01-07", 90, 90)],
                       dividends=[{"ex_date": "2020-01-03", "pay_date": "2020-01-06",
                                   "amount": 10}])
        result = run_backtest(data, Strategy("test", "0050", 1, "buyhold"),
                              cash_yield=.02, initial=1000, costs=FREE)
        rows = result["daily"]
        self.assertEqual(rows[1]["receivables"], 100)
        self.assertEqual(rows[1]["cash"], 0)
        self.assertEqual(rows[1]["nav"], 1000)
        self.assertEqual(rows[2]["receivables"], 0)
        self.assertEqual(rows[2]["cash"], 100)
        self.assertEqual(rows[2]["cash_interest"], 0)  # No interest before payment.
        self.assertEqual([t["date"] for t in result["trades"]],
                         ["2020-01-02", "2020-01-07"])
        expected_interest = 100 * ((1.02) ** (1 / DAYS_PER_YEAR) - 1)
        self.assertAlmostEqual(rows[3]["cash_interest"], expected_interest, places=12)
        self.assertAlmostEqual(rows[3]["nav"], 1000 + expected_interest, places=10)

    def test_weekend_payment_gets_only_post_payment_interest(self):
        data = fixture([("2020-01-02", 100, 100), ("2020-01-03", 90, 90),
                        ("2020-01-06", 90, 90)],
                       dividends=[{"ex_date": "2020-01-03", "pay_date": "2020-01-04",
                                   "amount": 10}])
        result = run_backtest(data, Strategy("test", "0050", 1, "buyhold"),
                              cash_yield=.02, initial=1000, costs=FREE)
        self.assertAlmostEqual(result["daily"][-1]["nav"],
                               900 + 100 * 1.02 ** (2 / DAYS_PER_YEAR), places=9)
        self.assertEqual(result["trades"][-1]["date"], "2020-01-06")

    def test_ex_date_open_buyer_has_no_dividend_entitlement(self):
        data = fixture([("2020-01-02", 90, 90), ("2020-01-03", 90, 90)],
                       dividends=[{"ex_date": "2020-01-02", "pay_date": "2020-01-03",
                                   "amount": 10}])
        result = run_backtest(data, Strategy("test", "0050", 1, "buyhold"),
                              cash_yield=0, initial=1000, costs=FREE)
        self.assertEqual(result["metrics"]["dividends_paid"], 0)

    def test_quarterly_order_waits_for_suspension_to_end(self):
        data = fixture([("2020-03-31", 100, 200), ("2020-04-01", None, None),
                        ("2020-04-02", 200, 200)])
        result = run_backtest(data, Strategy("test", "00631L", .5, "quarterly"),
                              cash_yield=0, initial=1000, costs=FREE)
        self.assertEqual([t["date"] for t in result["trades"]],
                         ["2020-03-31", "2020-04-02"])
        self.assertTrue(result["daily"][1]["execution_deferred"])
        self.assertEqual(result["daily"][1]["pending_rebalance"], "quarterly")

    def test_one_price_bar_updates_nav_but_cannot_fill(self):
        data = fixture([("2020-03-31", 100, 200), ("2020-04-01", 180, 180),
                        ("2020-04-02", 170, 170)])
        data["prices"]["00631L"]["2020-04-01"].update(max=180, min=180)
        result = run_backtest(data, Strategy("test", "00631L", .5, "quarterly"),
                              cash_yield=0, initial=1000, costs=FREE)
        self.assertEqual(result["daily"][1]["nav"], 1400)
        self.assertFalse(result["daily"][1]["price_stale"])
        self.assertTrue(result["daily"][1]["one_price_bar"])
        self.assertEqual(result["trades"][-1]["date"], "2020-04-02")

    def test_unverified_missing_bar_is_an_error(self):
        data = fixture([("2020-01-02", 100, 100), ("2020-01-03", None, None)], missing=[])
        with self.assertRaisesRegex(ValueError, "Unverified missing"):
            run_backtest(data, Strategy("test", "0050", 1, "buyhold"))


class MetricTests(unittest.TestCase):
    def test_known_cagr_drawdown_and_recovery(self):
        data = fixture([("2020-01-02", 100, 110), ("2020-01-03", 80, 80),
                        ("2020-01-06", 110, 110), ("2021-01-02", 121, 121)])
        result = run_backtest(data, Strategy("test", "0050", 1, "buyhold"),
                              cash_yield=0, initial=1000, costs=FREE)
        metric = result["metrics"]
        years = (366 + OPEN_TO_CLOSE_DAYS) / DAYS_PER_YEAR
        self.assertAlmostEqual(metric["cagr"], 1.21 ** (1 / years) - 1, places=12)
        self.assertAlmostEqual(metric["max_drawdown"], 800 / 1100 - 1, places=12)
        episode = metric["longest_recovery"]
        self.assertEqual(episode["peak_date"], "2020-01-02")
        self.assertEqual(episode["trough_date"], "2020-01-03")
        self.assertEqual(episode["recovery_date"], "2020-01-06")
        self.assertEqual(episode["duration_calendar_days"], 4)
        self.assertEqual(episode["duration_sessions"], 2)
        self.assertFalse(episode["censored"])

    def test_initial_day_costs_count_and_unrecovered_is_censored(self):
        data = fixture([("2020-01-02", 100, 100), ("2020-01-03", 100, 100)])
        result = run_backtest(data, Strategy("test", "0050", 1, "buyhold"),
                              cash_yield=0, initial=1000, costs=Costs())
        loss = result["trades"][0]["total_cost"] / 1000
        self.assertAlmostEqual(result["daily"][0]["return"], -loss, places=12)
        self.assertAlmostEqual(result["metrics"]["max_drawdown"], -loss, places=12)
        self.assertTrue(result["metrics"]["longest_recovery"]["censored"])
        self.assertIsNone(result["metrics"]["longest_completed_recovery"])
        self.assertIsNone(result["metrics"]["longest_recovery"]["recovery_date"])
        self.assertEqual(result["metrics"]["longest_recovery"]["duration_sessions"], 2)

    def test_sortino_includes_zero_downside_days_and_sharpe_uses_sample_std(self):
        data = fixture([("2020-01-02", 100, 110), ("2020-01-03", 99, 99),
                        ("2020-01-06", 99, 99)])
        result = run_backtest(data, Strategy("test", "0050", 1, "buyhold"),
                              cash_yield=0, initial=1000, costs=FREE)
        # Mean is zero up to rounding, and the downside RMS uses three sessions.
        self.assertAlmostEqual(result["metrics"]["sortino"], 0, places=12)
        self.assertAlmostEqual(result["metrics"]["sharpe"], 0, places=12)
        data["prices"]["0050"]["2020-01-06"]["close"] = 108.9
        result = run_backtest(data, Strategy("test", "0050", 1, "buyhold"),
                              cash_yield=0, initial=1000, costs=FREE)
        self.assertAlmostEqual(result["metrics"]["sortino"],
                               (.1 / 3) / math.sqrt(.01 / 3) * math.sqrt(252), places=10)
        self.assertAlmostEqual(result["metrics"]["sharpe"],
                               (.1 / 3) / math.sqrt((2 * (.1 - .1 / 3)**2 +
                                                      (-.1 - .1 / 3)**2) / 2) * math.sqrt(252), places=10)

    def test_cash_actual_calendar_gap_and_rf(self):
        data = fixture([("2020-01-03", 100, 100), ("2020-01-06", 100, 100)])
        result = run_backtest(data, Strategy("cash", "0050", 0, "buyhold"),
                              cash_yield=.02, initial=1000, costs=FREE)
        expected = .0 + 1.02 ** (3 / DAYS_PER_YEAR) - 1
        self.assertAlmostEqual(result["daily"][1]["return"], expected, places=13)
        self.assertAlmostEqual(result["daily"][1]["rf_return"], expected, places=13)
        self.assertEqual(result["daily"][0]["rf_return"], 0)

    def test_repeat_runs_and_files_are_deterministic(self):
        data = fixture([("2020-01-02", 100, 100), ("2020-01-03", 101, 101)])
        strategy = Strategy("test", "0050", 1, "buyhold")
        first = run_backtest(data, strategy)
        second = run_backtest(data, strategy)
        self.assertEqual(first, second)
        self.assertEqual(len(standard_strategies()), 8)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_run(root / "a", first)
            write_run(root / "b", second)
            for file in (root / "a").iterdir():
                self.assertEqual(file.read_bytes(), (root / "b" / file.name).read_bytes())
            self.assertEqual(json.loads((root / "a" / "config.json").read_text()), first["config"])


if __name__ == "__main__":
    unittest.main()
