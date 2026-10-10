"""Input and same-engine parity checks for the interactive calculator."""
import unittest
from portfolio_backtest import Portfolio, run_portfolio
from web_calculator import run_request, run_comparison
from test_portfolio_backtest import fixture


class CalculatorTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture([('2020-01-02', 100, 100), ('2020-01-03', 110, 102), ('2020-01-06', 95, 99)])

    def test_160_percent_presets_match_python_portfolios(self):
        for name, ordinary, weights in [('A', 0, (.8, 0, .2)), ('B', 40, (.6, .4, 0)), ('C', 20, (.7, .2, .1))]:
            got = run_request(self.data, {'exposure': 160, 'ordinary': ordinary})
            expected = run_portfolio(self.data, Portfolio(name, *weights, 'quarterly'))
            for key in ['ending_nav', 'cagr', 'max_drawdown', 'sharpe', 'sortino']:
                self.assertAlmostEqual(got['metrics'][key], expected['metrics'][key],
                                       delta=abs(expected['metrics'][key])*1e-12+1e-10)

    def test_infeasible_cash_and_nonfinite_inputs_rejected(self):
        for params in [{'exposure': 201}, {'exposure': 160, 'ordinary': 41},
                       {'exposure': 50, 'ordinary': 51}, {'principal': 0},
                       {'exposure': float('nan')}, {'cash_rate': -1}, {'threshold': 0}]:
            with self.assertRaises(ValueError):
                run_request(self.data, params)

    def test_zero_and_200_boundaries(self):
        cash = run_request(self.data, {'exposure': 0, 'ordinary': 0})
        self.assertEqual(cash['params']['weights'], {'00631L': 0, '0050': 0, 'cash': 1})
        self.assertIsNone(cash['metrics']['sharpe'])
        full = run_request(self.data, {'exposure': 200, 'ordinary': 0})
        self.assertEqual(full['params']['weights']['00631L'], 1)

    def test_principal_scaling_changes_money_not_return(self):
        a = run_request(self.data, {'principal': 100})
        b = run_request(self.data, {'principal': 200})
        self.assertAlmostEqual(b['metrics']['ending_nav'], a['metrics']['ending_nav']*2, places=7)
        self.assertAlmostEqual(a['metrics']['cagr'], b['metrics']['cagr'], places=12)

    def test_config_records_custom_threshold(self):
        got = run_request(self.data, {'rule': 'threshold', 'threshold': 7})
        self.assertIn('>=7 percentage points', got['config']['threshold_rule'])

    def test_comparison_all_uses_same_exposure_cash_and_principal(self):
        got = run_comparison(self.data, {'exposure': 160, 'ordinary': 10, 'principal': 150, 'cash_rate': 2})
        self.assertEqual([x['result']['params']['ordinary'] for x in got['comparisons']], [0, 40, 20])
        for item in got['comparisons']:
            self.assertEqual(item['result']['params']['exposure'], 160)
            self.assertEqual(item['result']['params']['principal'], 150)
            self.assertEqual(item['result']['params']['cash_rate'], 2)


if __name__ == '__main__':
    unittest.main()
