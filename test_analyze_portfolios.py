"""Period endpoint and recovery semantics, using synthetic account values."""
import unittest
from analyze_portfolios import window_stats, crisis_event


def rows(values):
    result=[];previous=1000
    for day,nav in values:
        result.append({'date':day,'nav':float(nav),'return':nav/previous-1,
                       'excess_return':nav/previous-1,'nominal_market_exposure':1.6})
        previous=nav
    return result


class PeriodTests(unittest.TestCase):
    def test_annual_return_uses_prior_year_close(self):
        data=rows([('2019-12-31',1000),('2020-01-02',800),('2020-12-31',900)])
        stat=window_stats(data,'2020-01-01','2020-12-31')
        self.assertAlmostEqual(stat['period_return'],-.1)
        self.assertEqual(stat['anchor_date'],'2019-12-31')

    def test_year_drawdown_resets_prior_year_end_anchor(self):
        data=rows([('2019-06-30',1500),('2019-12-31',1000),('2020-01-02',800),('2020-12-31',900)])
        stat=window_stats(data,'2020-01-01','2020-12-31')
        self.assertAlmostEqual(stat['max_drawdown'],-.2)

    def test_event_recovers_after_event_year(self):
        data=rows([('2019-12-31',1000),('2020-01-02',800),('2020-12-31',900),('2021-01-04',1000)])
        event=crisis_event(data,2020)
        self.assertEqual(event['peak_date'],'2019-12-31')
        self.assertEqual(event['trough_date'],'2020-01-02')
        self.assertEqual(event['recovery_date'],'2021-01-04')
        self.assertFalse(event['censored'])

    def test_ongoing_event_is_censored(self):
        data=rows([('2019-12-31',1000),('2020-01-02',800),('2020-12-31',900),('2021-01-04',950)])
        event=crisis_event(data,2020)
        self.assertIsNone(event['recovery_date'])
        self.assertTrue(event['censored'])

    def test_cash_excess_roundoff_has_no_risk_ratio(self):
        data=rows([('2020-01-02',1000),('2020-01-03',1001)])
        data[0]['excess_return']=1e-16;data[1]['excess_return']=-1e-16
        stat=window_stats(data,'2020-01-01','2020-12-31')
        self.assertIsNone(stat['sharpe']);self.assertIsNone(stat['sortino'])


if __name__=='__main__':unittest.main()
