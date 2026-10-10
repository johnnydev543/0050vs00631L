"""Synthetic financial accounting checks; fixture values are not market data."""
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from backtest import Costs, Strategy, run_backtest
from portfolio_backtest import Portfolio, run_portfolio, solve_targets, write_gzip_csv

FREE = Costs(0, 0, 0)


def fixture(rows, dividends=None, splits=None, missing=None):
    dates = [r[0] for r in rows]
    prices = {s: {} for s in ['00631L', '0050']}
    for d, leveraged, ordinary in rows:
        for s, price in [('00631L', leveraged), ('0050', ordinary)]:
            if price is not None:
                opening, closing = price if isinstance(price, tuple) else (price, price)
                prices[s][d] = {'open': opening, 'close': closing, 'Trading_Volume': 10}
    absent = {s: [d for d in dates if d not in prices[s]] for s in prices}
    return {'start': dates[0], 'end': dates[-1], 'calendar': dates, 'prices': prices,
            'splits': splits or {'00631L': [], '0050': []},
            'dividends': {'00631L': [], '0050': dividends or []},
            'metadata': {'verified_suspensions': absent if missing is None else missing}}


class TargetSolverTests(unittest.TestCase):
    def test_simultaneous_cost_adjusted_stock_and_cash_targets(self):
        for values, cash, weights in [({'00631L': 0., '0050': 0.}, 1e6, {'00631L': .6, '0050': .4}),
                                     ({'00631L': 850000., '0050': 200000.}, 100000., {'00631L': .7, '0050': .2})]:
            targets, nav = solve_targets(values, cash, 0, weights, Costs())
            for s in values:
                self.assertAlmostEqual(targets[s]/nav, weights[s], places=13)
            fees = sum((targets[s]-v)*((1.0005*1.001425)-1) if targets[s] >= v else
                       (v-targets[s])*(1-.9995*(1-.001425-.001)) for s, v in values.items())
            self.assertAlmostEqual(sum(values.values())+cash-nav, fees, places=8)

    def test_unpaid_dividend_cannot_finance_stock_targets(self):
        targets, nav = solve_targets({'00631L': 600., '0050': 300.}, 0., 100.,
                                     {'00631L': .6, '0050': .4}, FREE)
        self.assertEqual(nav, 1000)
        self.assertAlmostEqual(sum(targets.values()), 900)
        self.assertAlmostEqual(targets['00631L']/sum(targets.values()), .6)


class PortfolioAccountingTests(unittest.TestCase):
    def test_initial_160_exposure_all_three_portfolios(self):
        data = fixture([('2020-01-02', 100, 100), ('2020-01-03', 100, 100)])
        for label, weights in [('A', (.8, 0, .2)), ('B', (.6, .4, 0)), ('C', (.7, .2, .1))]:
            result = run_portfolio(data, Portfolio(label, *weights, 'threshold'), 0, 1000, Costs())
            row = result['daily'][0]
            self.assertAlmostEqual(row['nominal_market_exposure'], 1.6, places=12)
            self.assertAlmostEqual(row['weight_00631L'], weights[0], places=12)
            self.assertAlmostEqual(row['weight_0050'], weights[1], places=12)
            self.assertAlmostEqual(row['weight_cash'], weights[2], places=12)
            self.assertIsNone(row['actual_internal_exposure'])

    def test_a_matches_legacy_rules_and_cash_interest(self):
        data = fixture([('2020-03-31', (100, 190), 100), ('2020-04-01', 160, 100),
                        ('2020-04-02', 140, 100), ('2020-04-06', 180, 100)])
        for rule in ['quarterly', 'threshold']:
            old = run_backtest(data, Strategy('old', '00631L', .8, rule), .01, 1000)
            new = run_portfolio(data, Portfolio('A', .8, 0, .2, rule), .01, 1000)
            for a, b in zip(old['daily'], new['daily']):
                self.assertAlmostEqual(a['nav'], b['nav'], places=9)
            self.assertEqual([(x['date'], x['side']) for x in old['trades']],
                             [(x['date'], x['side']) for x in new['trades']])

    def test_threshold_fills_next_open_and_sells_before_buys(self):
        data = fixture([('2020-01-02', (100, 200), 100), ('2020-01-03', 150, 100)])
        result = run_portfolio(data, Portfolio('B', .6, .4, 0, 'threshold'), 0, 1000, FREE)
        self.assertEqual(result['daily'][0]['pending_rebalance'], 'threshold')
        trades = [t for t in result['trades'] if t['reason'] == 'threshold']
        self.assertEqual([t['date'] for t in trades], ['2020-01-03']*2)
        self.assertEqual([t['side'] for t in trades], ['sell', 'buy'])
        self.assertEqual(result['metrics']['rebalance_count'], 1)

    def test_joint_rebalance_waits_for_suspended_asset(self):
        data = fixture([('2020-03-31', (100, 150), 100), ('2020-04-01', 150, None),
                        ('2020-04-02', 150, 100)])
        result = run_portfolio(data, Portfolio('B', .6, .4, 0, 'quarterly'), 0, 1000, FREE)
        self.assertEqual({t['date'] for t in result['trades'] if t['reason'] == 'quarterly'}, {'2020-04-02'})
        self.assertTrue(result['daily'][1]['execution_deferred'])

    def test_one_price_updates_mark_and_defers_entire_rebalance(self):
        data = fixture([('2020-03-31', (100, 150), 100), ('2020-04-01', 140, 100),
                        ('2020-04-02', 130, 100)])
        data['prices']['0050']['2020-04-01'].update(max=100, min=100)
        result = run_portfolio(data, Portfolio('B', .6, .4, 0, 'quarterly'), 0, 1000, FREE)
        self.assertFalse(result['daily'][1]['price_stale'])
        self.assertAlmostEqual(result['daily'][1]['nav'], 1240)
        self.assertEqual({t['date'] for t in result['trades'] if t['reason'] == 'quarterly'}, {'2020-04-02'})

    def test_both_splits_create_no_gain(self):
        data = fixture([('2020-01-02', 100, 100), ('2020-01-03', 5, 25)],
                       splits={'00631L': [{'date': '2020-01-03', 'ratio': 20}],
                               '0050': [{'date': '2020-01-03', 'ratio': 4}]})
        result = run_portfolio(data, Portfolio('B', .6, .4, 0, 'threshold'), 0, 1000, FREE)
        self.assertEqual([r['nav'] for r in result['daily']], [1000, 1000])
        self.assertAlmostEqual(result['daily'][-1]['nominal_market_exposure'], 1.6)

    def test_dividend_entitlement_payment_and_reinvestment(self):
        data = fixture([('2020-01-02', 100, 100), ('2020-01-03', 100, 90),
                        ('2020-01-06', 100, 90), ('2020-01-07', 100, 90)],
                       dividends=[{'ex_date': '2020-01-03', 'pay_date': '2020-01-06', 'amount': 10}])
        result = run_portfolio(data, Portfolio('B', .6, .4, 0, 'threshold'), 0, 1000, FREE)
        self.assertEqual(result['daily'][1]['receivables'], 40)
        self.assertEqual(result['daily'][1]['cash'], 0)
        self.assertEqual(result['daily'][1]['nav'], 1000)
        reinvest = [t for t in result['trades'] if t['reason'] == 'dividend_reinvestment']
        self.assertEqual([(t['date'], t['symbol']) for t in reinvest], [('2020-01-07', '0050')])
        self.assertAlmostEqual(result['daily'][-1]['cash'], 0)

    def test_full_rebalance_absorbs_paid_dividend_pool_once(self):
        data = fixture([('2020-03-27', 100, 100), ('2020-03-30', 100, 90),
                        ('2020-03-31', 100, 90), ('2020-04-01', 100, 90), ('2020-04-02', 100, 90)],
                       dividends=[{'ex_date': '2020-03-30', 'pay_date': '2020-03-31', 'amount': 10}])
        result = run_portfolio(data, Portfolio('B', .6, .4, 0, 'quarterly'), 0, 1000, FREE)
        self.assertFalse(any(t['reason'] == 'dividend_reinvestment' for t in result['trades']))
        self.assertEqual(result['metrics']['dividends_paid'], 40)
        self.assertAlmostEqual(result['daily'][-1]['nav'], 1000)

    def test_cash_pool_earns_only_after_payment(self):
        data = fixture([('2020-01-02', 100, 100), ('2020-01-03', 100, 90), ('2020-01-06', 100, 90)],
                       dividends=[{'ex_date': '2020-01-03', 'pay_date': '2020-01-04', 'amount': 10}])
        result = run_portfolio(data, Portfolio('B', .6, .4, 0, 'threshold'), .02, 1000, FREE)
        self.assertAlmostEqual(result['daily'][-1]['nav'], 960+40*1.02**(2/365.2425), places=9)

    def test_exposure_threshold_uses_common_20_point_band(self):
        data = fixture([('2020-01-02', (100, 300), 100), ('2020-01-03', 250, 100)])
        result = run_portfolio(data, Portfolio('B', .6, .4, 0, 'exposure_threshold'), 0, 1000, FREE)
        self.assertEqual(result['daily'][0]['pending_rebalance'], 'exposure_threshold')
        self.assertEqual(result['metrics']['rebalance_count'], 1)

    def test_future_changes_do_not_change_earlier_accounts(self):
        data = fixture([('2020-01-02', 100, 100), ('2020-01-03', 110, 102), ('2020-01-06', 90, 95)])
        strategy = Portfolio('C', .7, .2, .1, 'threshold')
        first = run_portfolio(data, strategy)
        data['prices']['0050']['2020-01-06']['close'] *= 2
        second = run_portfolio(data, strategy)
        self.assertEqual(first['daily'][:2], second['daily'][:2])

    def test_compressed_csv_is_byte_reproducible(self):
        rows = [{'date': '2020-01-02', 'nav': 1000., 'nominal_market_exposure': 1.6}]
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a.gz', Path(directory)/'b.gz'
            write_gzip_csv(a, rows); write_gzip_csv(b, rows)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertIn('nominal_market_exposure', gzip.decompress(a.read_bytes()).decode())

    def test_cash_only_has_no_artificial_risk_ratio(self):
        data = fixture([('2020-01-02', 100, 100), ('2020-01-03', 110, 105), ('2020-01-06', 120, 110)])
        result = run_portfolio(data, Portfolio('cash', 0, 0, 1, 'threshold'), .01, 1000)
        self.assertIsNone(result['metrics']['sharpe'])
        self.assertIsNone(result['metrics']['sortino'])
        self.assertEqual(result['metrics']['number_of_trades'], 0)
        self.assertEqual(result['daily'][-1]['nominal_market_exposure'], 0)


if __name__ == '__main__':
    unittest.main()
