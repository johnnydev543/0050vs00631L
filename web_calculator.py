"""Pure Python request adapter shared by the browser and automated validation."""
from __future__ import annotations
import math
from analyze_portfolios import STAGES, window_stats, crisis_event
from portfolio_backtest import Portfolio, run_portfolio


def run_request(data: dict, request: dict) -> dict:
    exposure_percent = float(request.get('exposure', 160))
    ordinary_percent = float(request.get('ordinary', 0))
    principal_wan = float(request.get('principal', 100))
    rate_percent = float(request.get('cash_rate', 1))
    threshold_percent = float(request.get('threshold', 10))
    values = [exposure_percent, ordinary_percent, principal_wan, rate_percent, threshold_percent]
    if not all(math.isfinite(x) for x in values):
        raise ValueError('請輸入有限的數值。')
    if not 0 <= exposure_percent <= 200:
        raise ValueError('此模型的目標曝險範圍為0%至200%。')
    maximum_ordinary = min(exposure_percent, 200-exposure_percent)
    if not 0 <= ordinary_percent <= maximum_ordinary+1e-10:
        raise ValueError(f'此曝險下，0050比例須介於0%與{maximum_ordinary:g}%之間。')
    if principal_wan <= 0 or not 0 <= rate_percent <= 10:
        raise ValueError('本金須大於0；現金年收益假設須介於0%與10%。')
    rule = request.get('rule', 'quarterly')
    if rule not in {'quarterly', 'threshold', 'exposure_threshold'}:
        raise ValueError('請選擇有效的再平衡方式。')
    maximum_threshold = 200 if rule == 'exposure_threshold' else 100
    if not 0 < threshold_percent <= maximum_threshold:
        raise ValueError(f'再平衡門檻须大於0、且不超過{maximum_threshold}個百分點。')
    ordinary = ordinary_percent/100
    leveraged = (exposure_percent/100-ordinary)/2
    cash = 1-leveraged-ordinary
    if cash < -1e-10:
        raise ValueError('此配置的現金比例不足，請調整0050比例或目標曝險。')
    weights = {'00631L': leveraged, '0050': ordinary, 'cash': max(0., cash)}
    portfolio = Portfolio('custom', leveraged, ordinary, max(0., cash), rule,
                          weight_threshold=threshold_percent/100 if rule == 'threshold' else .10,
                          exposure_threshold=threshold_percent/100 if rule == 'exposure_threshold' else .20)
    result = run_portfolio(data, portfolio, rate_percent/100, principal_wan*10000)
    rows = result['daily']
    annual = []
    for year in range(2014, 2027):
        stat = window_stats(rows, f'{year}-01-01', f'{year}-12-31', principal_wan*10000)
        partial = year in (2014, 2026)
        if partial:
            stat['period_cagr'] = None
        annual.append({'year': year, 'partial_year': partial, **stat})
    return {'params': {'exposure': exposure_percent, 'ordinary': ordinary_percent,
                      'principal': principal_wan, 'cash_rate': rate_percent, 'rule': rule,
                      'threshold': threshold_percent, 'weights': weights},
            'config': result['config'], 'metrics': result['metrics'],
            'daily': rows, 'trades': result['trades'], 'annual': annual,
            'crises': [crisis_event(rows, year, principal_wan*10000) for year in [2020, 2022]],
            'stages': [{'stage': name, **window_stats(rows, start, end, principal_wan*10000)}
                       for name, start, end in STAGES]}


def run_comparison(data: dict, request: dict) -> dict:
    selected = run_request(data, request)
    maximum_ordinary = min(selected['params']['exposure'], 200-selected['params']['exposure'])
    cache = {selected['params']['ordinary']: selected}
    comparisons = []
    for label, name, ordinary in [('A', '正二＋現金', 0.),
                                  ('B', '0050優先', maximum_ordinary),
                                  ('C', '中間配置', maximum_ordinary/2)]:
        if ordinary not in cache:
            params = dict(request, ordinary=ordinary)
            cache[ordinary] = run_request(data, params)
        comparisons.append({'id': label, 'name': name, 'result': cache[ordinary]})
    return {'params': selected['params'], 'selected': selected, 'comparisons': comparisons}
