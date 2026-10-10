"""Offline independent audit of annual/stage/crisis analysis.

Reads saved daily ledgers directly. Real-output expectations use compounded
daily returns, log-space drawdowns and ordered peak/trough candidate sets,
without calling the reviewed analysis functions. Calls those functions only
for explicitly separate synthetic tests. Writes only period_independent_checks.json.
"""
from __future__ import annotations
import csv
import gzip
import hashlib
import itertools
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import analyze_portfolios as reviewed

YEAR = 365.2425
STAGES = [
    ('2014–2019', '2014-10-31', '2019-12-31'),
    ('2020 疫情年', '2020-01-01', '2020-12-31'),
    ('2021', '2021-01-01', '2021-12-31'),
    ('2022 熊市年', '2022-01-01', '2022-12-31'),
    ('2023 至樣本末', '2023-01-01', '2026-10-08'),
]
CHECKS = []
FINDINGS = []


def close(a, b):
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-10, abs_tol=2e-10)


def record(n, title, failures, count, severity='Major', extra=None):
    row = {'id': f'RVW-PER-{n:03d}', 'title': title,
           'status': 'FAIL' if failures else 'PASS', 'failure_severity': severity,
           'comparisons': count, 'mismatches': failures[:8]}
    if extra:
        row['evidence'] = extra
    CHECKS.append(row)
    if failures:
        print(row['id'], title, len(failures), 'mismatch(es)')


def read_rows(path):
    reader = gzip.open if path.suffix == '.gz' else open
    with reader(path, 'rt', newline='') as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for key in ('nav', 'return', 'rf_return', 'excess_return', 'nominal_market_exposure',
                    'holdings_value', 'cash', 'receivables'):
            if key in row:
                row[key] = float(row[key])
    return rows


def independent_window(rows, start, end, initial=1e6):
    ix = [i for i, r in enumerate(rows) if start <= r['date'] <= end]
    if not ix:
        return {'included': False}
    a, z = ix[0], ix[-1]
    section = rows[a:z+1]
    nav0 = rows[a-1]['nav'] if a else initial
    date0 = rows[a-1]['date'] if a else rows[0]['date']
    vals = [nav0] + [r['nav'] for r in section]
    # Telescoping product is evaluated from the independent daily return ledger.
    ret = math.expm1(math.fsum(math.log1p(r['return']) for r in section))
    logvals = [math.log(x) for x in vals]
    highs = list(itertools.accumulate(logvals, max))
    dd = math.expm1(min(v-h for v, h in zip(logvals, highs)))
    elapsed = (date.fromisoformat(section[-1]['date']) - date.fromisoformat(date0)).days
    if not a:
        elapsed += 3/16
    cagr = math.expm1(math.log(vals[-1]/nav0)*YEAR/elapsed) if elapsed else None
    excess = [r['return']-r['rf_return'] for r in section]
    avg = math.fsum(excess)/len(excess)
    variance = math.fsum((x-avg)**2 for x in excess)/(len(excess)-1) if len(excess)>1 else 0.
    downside = math.fsum(x*x for x in excess if x<0)/len(excess)
    return {'included': True, 'start': section[0]['date'], 'end': section[-1]['date'],
            'anchor_date': date0, 'anchor_nav': nav0, 'ending_nav': vals[-1],
            'sessions': len(section), 'period_return': ret, 'period_cagr': cagr,
            'max_drawdown': dd, 'sharpe': avg*math.sqrt(252/variance) if variance else None,
            'sortino': avg*math.sqrt(252/downside) if downside else None,
            'mean_nominal_exposure': math.fsum(r['nominal_market_exposure'] for r in section)/len(section)}


def independent_crisis(rows, year, initial=1e6):
    indices = [i for i, r in enumerate(rows) if date.fromisoformat(r['date']).year == year]
    if not indices:
        return {'year': year, 'included': False}
    first = indices[0]
    anchor = first-1
    vals = {i: rows[i]['nav'] for i in indices}
    vals[anchor] = rows[anchor]['nav'] if first else initial
    day = lambda i: rows[max(i, 0)]['date']
    candidates = []
    for trough in indices:
        eligible = [j for j in vals if j <= trough]
        # Highest preceding NAV; for identical highs the most recent occurrence.
        peak = max(eligible, key=lambda j: (vals[j], j))
        candidates.append((vals[trough]/vals[peak]-1, trough, peak))
    depth, trough, peak = min(candidates, key=lambda c: (c[0], c[1]))
    later = [i for i in range(trough, len(rows)) if rows[i]['nav'] >= vals[peak]*(1-1e-12)]
    recovery = min(later) if later else None
    end = recovery if recovery is not None else len(rows)-1
    return {'year': year, 'included': True, 'max_drawdown': depth,
            'peak_date': day(peak), 'peak_nav': vals[peak], 'trough_date': day(trough),
            'trough_nav': vals[trough], 'recovery_date': day(recovery) if recovery is not None else None,
            'censored': recovery is None,
            'peak_to_recovery_days': (date.fromisoformat(day(end))-date.fromisoformat(day(peak))).days,
            'trough_to_recovery_days': (date.fromisoformat(day(end))-date.fromisoformat(day(trough))).days,
            'peak_to_recovery_sessions': end-peak}


def mismatches(got, want, keys, label):
    bad = []
    for k in keys:
        g, w = got.get(k), want.get(k)
        equal = close(g, w) if isinstance(w, (int, float)) and not isinstance(w, bool) else g == w
        if not equal:
            bad.append({'row': label, 'field': k, 'actual': g, 'expected': w})
    return bad


def synthetic(points, initial=100.):
    previous = initial
    rows = []
    for day, nav in points:
        ret = nav/previous-1
        rows.append({'date': day, 'nav': nav, 'return': ret, 'rf_return': 0.,
                     'excess_return': ret, 'nominal_market_exposure': 1.})
        previous = nav
    return rows


def main():
    source_paths = [ROOT/'analyze_portfolios.py', ROOT/'backtest.py', ROOT/'portfolio_backtest.py',
                    ROOT/'portfolio_results/manifest.json', ROOT/'results/manifest.json']
    series = {}
    for kind, folder in [('legacy', 'results'), ('portfolio', 'portfolio_results')]:
        summary_path = ROOT/folder/'summary.json'
        source_paths.append(summary_path)
        for m in json.loads(summary_path.read_text()):
            rate, strategy = m['cash_yield_assumption'], m['strategy']
            scenario = ('cash_' + format(rate, '.6f')).replace('.', 'p')
            path = ROOT/folder/scenario/strategy/('daily.csv' if kind=='legacy' else 'daily.csv.gz')
            rows = read_rows(path)
            if kind == 'legacy':
                leverage = 1 if strategy=='0050_buyhold' else 2
                for row in rows:
                    row['nominal_market_exposure'] = leverage*row['holdings_value']/row['nav']
            series[(kind+':'+strategy, rate)] = rows
            source_paths.append(path)
    data = {}
    for name in ('annual_performance', 'market_stages', 'crisis_recovery'):
        path = ROOT/'analysis'/(name+'.json')
        data[name] = json.loads(path.read_text())
        source_paths.extend([path, path.with_suffix('.csv')])
    config_path = ROOT/'analysis/analysis_config.json'
    source_paths.append(config_path)
    config = json.loads(config_path.read_text())
    annual, stages, crises = data.values()
    errors = []
    if len(series) != 48:
        errors.append({'series_count':len(series),'expected':48})
    for name, got, count, field, expected in [
        ('annual', annual, 13, 'year', list(range(2014, 2027))),
        ('stages', stages, 5, 'stage', [s[0] for s in STAGES]),
        ('crises', crises, 2, 'year', [2020, 2022])]:
        expected_keys = {(sid, rate, value) for sid, rate in series for value in expected}
        actual_keys = [(r['series_id'], r['cash_yield_assumption'], r[field]) for r in got]
        if len(actual_keys) != len(expected_keys) or set(actual_keys) != expected_keys:
            errors.append({'table': name, 'rows':len(actual_keys), 'expected':len(expected_keys)})
    record(1, 'Complete unique 48-series annual/stage/crisis catalog', errors, len(annual)+len(stages)+len(crises)+1)
    annual_errors, annual_returns, annual_dd, partial_errors, partial_cagr = [], [], [], [], []
    risk_errors = []
    stage_errors, stage_returns, stage_dd = [], [], []
    endpoints = ('included', 'start', 'end', 'anchor_date', 'anchor_nav', 'ending_nav', 'sessions')
    for r in annual:
        rows = series[r['series_id'], r['cash_yield_assumption']]
        year = r['year']; label = [r['series_id'], r['cash_yield_assumption'], year]
        expected = independent_window(rows, f'{year}-01-01', f'{year}-12-31')
        annual_errors += mismatches(r, expected, endpoints, label)
        annual_returns += mismatches(r, expected, ['period_return'], label)
        annual_dd += mismatches(r, expected, ['max_drawdown'], label)
        risk_errors += mismatches(r, expected, ['sharpe', 'sortino', 'mean_nominal_exposure'], label)
        expected_partial = year in {date.fromisoformat(rows[0]['date']).year, date.fromisoformat(rows[-1]['date']).year}
        if r['partial_year'] != expected_partial:
            partial_errors.append({'row': label, 'actual': r['partial_year'], 'expected': expected_partial})
        if expected_partial and r.get('period_cagr') is not None:
            partial_cagr.append({'row': label, 'period_return': r['period_return'], 'period_cagr': r['period_cagr']})
        elif not expected_partial:
            annual_returns += mismatches(r, expected, ['period_cagr'], label)
    record(2, 'Annual endpoints and preceding-year close anchors', annual_errors, len(annual)*len(endpoints))
    record(3, 'Annual nonannualized compounded returns and full-year CAGR', annual_returns, len(annual)*2-len(series)*2)
    record(4, 'Annual drawdown independently recomputed from log NAV maxima', annual_dd, len(annual))
    record(5, 'Partial-year output does not publish annualized CAGR', partial_cagr, len(series)*2, 'Minor')
    if partial_cagr:
        FINDINGS.append({'id':'RVW-PER-005','severity':'Minor','status':'OPEN',
                        'description':'Partial-year period_return and flags are correct, but annual output also exports annualized period_cagr despite the stated partial-years-not-annualized convention.',
                        'recommendation':'Set annual period_cagr to null for partial years, retaining period_return; retain period_cagr for fixed multi-year stages.',
                        'affected_rows':len(partial_cagr)})
    record(6, 'Only boundary years 2014 and 2026 are marked partial for this sample', partial_errors, len(annual))
    for r in stages:
        _, start, end = next(s for s in STAGES if s[0]==r['stage'])
        rows = series[r['series_id'], r['cash_yield_assumption']]
        expected = independent_window(rows, start, end)
        label = [r['series_id'], r['cash_yield_assumption'], r['stage']]
        stage_errors += mismatches(r, expected, endpoints, label)
        stage_returns += mismatches(r, expected, ['period_return','period_cagr'], label)
        stage_dd += mismatches(r, expected, ['max_drawdown'], label)
        risk_errors += mismatches(r, expected, ['sharpe','sortino','mean_nominal_exposure'], label)
    record(7, 'Fixed-stage endpoints and immediately preceding close anchors', stage_errors, len(stages)*len(endpoints))
    record(8, 'Fixed-stage compounded returns and ACT/365.2425 CAGR', stage_returns, len(stages)*2)
    record(9, 'Fixed-stage drawdown resets at anchor rather than lifetime peak', stage_dd, len(stages))
    record(10, 'Annual/stage Sharpe, Sortino, and nominal exposure', risk_errors, (len(annual)+len(stages))*3)
    depths, recoveries, future_recoveries = [], [], []
    for r in crises:
        label=[r['series_id'],r['cash_yield_assumption'],r['year']]
        expected=independent_crisis(series[r['series_id'],r['cash_yield_assumption']],r['year'])
        depths += mismatches(r, expected, ['year','included','peak_date','peak_nav','trough_date','trough_nav','max_drawdown'], label)
        recoveries += mismatches(r, expected, ['recovery_date','censored','peak_to_recovery_days','trough_to_recovery_days','peak_to_recovery_sessions'], label)
        if expected['recovery_date'] and expected['recovery_date'][:4]>str(r['year']):
            future_recoveries.append({'series':label,'recovery_date':expected['recovery_date']})
    record(11, 'Fixed 2020/2022 in-year worst peak-to-trough event', depths, len(crises)*7)
    record(12, 'Crisis first recovery through sample end and day/session durations', recoveries, len(crises)*5,
           extra={'cross_year_recovery_count':len(future_recoveries),'examples':future_recoveries[:4]})
    record(13, 'Predeclared stage dates and crisis years match approved fixed windows',
           [] if config['stages']==[list(s) for s in STAGES] and config['crisis_years']==[2020,2022] else ['configuration mismatch'], 7)
    parity=[]; parity_count=0
    for name, records in data.items():
        with (ROOT/'analysis'/(name+'.csv')).open(newline='') as f:
            csv_records=list(csv.DictReader(f))
        if len(csv_records)!=len(records):parity.append({'table':name,'reason':'row count'});continue
        for i,(j,c) in enumerate(zip(records,csv_records)):
            for key,value in j.items():
                parity_count+=1
                csv_value=c.get(key)
                expected='' if value is None else str(value)
                equal=csv_value==expected
                if isinstance(value,(float,int)) and not isinstance(value,bool):
                    try:equal=close(float(csv_value),value)
                    except (ValueError,TypeError):equal=False
                if not equal:parity.append({'table':name,'row':i,'field':key,'json':value,'csv':csv_value})
    record(14, 'Published CSV tables match corresponding JSON values', parity, parity_count)

    # Each synthetic oracle below fixes an explicit result, independently of the implementation.
    rows=synthetic([('2019-12-30',200),('2019-12-31',100),('2020-01-02',80),('2020-12-31',120),('2021-01-04',60)])
    got=reviewed.window_stats(rows,'2020-01-01','2020-12-31',100)
    record(15,'Synthetic annual anchor includes first-session loss and excludes neighboring years',
           mismatches(got,{'anchor_date':'2019-12-31','anchor_nav':100.,'ending_nav':120.,'period_return':.2,'max_drawdown':-.2,'sessions':2},
                      ['anchor_date','anchor_nav','ending_nav','period_return','max_drawdown','sessions'],'prior-close'),6)
    rows=synthetic([('2014-10-31',90),('2014-12-31',95)])
    got=reviewed.window_stats(rows,'2014-01-01','2014-12-31',100)
    record(16,'Synthetic initial-capital anchor preserves initial entry loss',
           mismatches(got,{'anchor_nav':100.,'period_return':-.05,'max_drawdown':-.1},['anchor_nav','period_return','max_drawdown'],'initial-capital'),3)
    rows=synthetic([('2019-12-30',200),('2019-12-31',100),('2020-01-02',110),('2020-12-31',90),('2021-01-04',300)])
    got=reviewed.window_stats(rows,'2020-01-01','2020-12-31',100)
    record(17,'Synthetic stage MDD resets to 100 anchor instead of historical 200 high',
           mismatches(got,{'max_drawdown':90/110-1,'period_return':-.1},['max_drawdown','period_return'],'stage-reset'),2)
    rows=synthetic([('2019-12-31',100),('2020-01-02',120),('2020-03-19',60),('2020-12-31',80),('2021-01-04',110),('2021-02-01',120),('2022-01-03',20)])
    want={'max_drawdown':-.5,'peak_date':'2020-01-02','trough_date':'2020-03-19','recovery_date':'2021-02-01','censored':False,
          'peak_to_recovery_days':396,'trough_to_recovery_days':319,'peak_to_recovery_sessions':4}
    record(18,'Synthetic crisis recovery crosses calendar year at first recovery only',
           mismatches(reviewed.crisis_event(rows,2020,100),want,list(want),'cross-year'),len(want))
    rows=synthetic([('2021-12-30',100),('2022-01-03',90),('2022-10-25',50),('2022-12-30',80),('2023-01-03',99)])
    want={'max_drawdown':-.5,'peak_date':'2021-12-30','trough_date':'2022-10-25','recovery_date':None,'censored':True,
          'peak_to_recovery_days':369,'trough_to_recovery_days':70,'peak_to_recovery_sessions':4}
    record(19,'Synthetic unrecovered crisis reports elapsed censored duration',
           mismatches(reviewed.crisis_event(rows,2022,100),want,list(want),'censored'),len(want))
    rows=synthetic([('2019-12-31',100),('2020-01-02',120),('2020-03-19',90),('2020-12-31',110),('2021-01-04',20),('2021-06-01',120)])
    want={'max_drawdown':-.25,'trough_date':'2020-03-19','recovery_date':'2021-06-01'}
    record(20,'Synthetic later larger drawdown cannot replace in-year crisis trough',
           mismatches(reviewed.crisis_event(rows,2020,100),want,list(want),'later-trough'),len(want))
    rows=synthetic([('2021-12-30',100),('2022-01-03',120),('2022-02-01',120),('2022-03-01',60),('2023-01-03',120)])
    want={'peak_date':'2022-02-01','trough_date':'2022-03-01','recovery_date':'2023-01-03','peak_to_recovery_sessions':2}
    record(21,'Synthetic equal prior highs use most recent peak before worst trough',
           mismatches(reviewed.crisis_event(rows,2022,100),want,list(want),'equal-peaks'),len(want))
    rows=synthetic([('2020-01-02',100),('2020-01-03',110)])
    empty=[reviewed.window_stats(rows,'2022-01-01','2022-12-31',100),reviewed.crisis_event(rows,2022,100)]
    record(22,'Synthetic empty analysis windows are excluded',[] if empty==[{'included':False},{'year':2022,'included':False}] else empty,2)
    want={'max_drawdown':0.,'peak_date':'2020-01-02','trough_date':'2020-01-02','recovery_date':'2020-01-02','censored':False,'peak_to_recovery_sessions':0}
    record(23,'Synthetic rising series has zero drawdown and immediate recovery',
           mismatches(reviewed.crisis_event(rows,2020,100),want,list(want),'no-drawdown'),len(want))
    rows=synthetic([('2020-01-02',90),('2020-06-01',50),('2021-01-04',100)])
    want={'peak_date':'2020-01-02','peak_nav':100.,'max_drawdown':-.5,'recovery_date':'2021-01-04','peak_to_recovery_sessions':3}
    record(24,'Synthetic crisis at sample start uses initial capital and opening anchor session',
           mismatches(reviewed.crisis_event(rows,2020,100),want,list(want),'first-year-crisis'),len(want))
    rows=synthetic([('2021-12-30',100),('2022-01-03',80),('2022-10-25',50),('2023-01-03',99.999),('2023-01-04',100)])
    want={'recovery_date':'2023-01-04','peak_to_recovery_sessions':4,'censored':False}
    record(25,'Synthetic recovery threshold does not accept a genuinely below-peak NAV',
           mismatches(reviewed.crisis_event(rows,2022,100),want,list(want),'threshold'),len(want))
    rows=synthetic([('2020-06-01',90)])
    want=independent_window(rows,'2020-01-01','2020-12-31',100)
    record(26,'Synthetic one-session window null risk ratios and opening-to-close CAGR',
           mismatches(reviewed.window_stats(rows,'2020-01-01','2020-12-31',100),want,list(want),'one-session'),len(want))

    for c in CHECKS:
        if c['status']=='FAIL' and c['id']!='RVW-PER-005':
            FINDINGS.append({'id':c['id'],'severity':c['failure_severity'],'status':'OPEN','description':c['title'],'evidence':c['mismatches']})
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    hashes[str(Path(__file__).relative_to(ROOT))]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest=json.loads((ROOT/'portfolio_results/manifest.json').read_text())
    result={'status':'PASS' if not FINDINGS else 'PASS_WITH_MINOR' if all(f['severity']=='Minor' for f in FINDINGS) else 'FAIL',
            'check_count':len(CHECKS),'pass_count':sum(c['status']=='PASS' for c in CHECKS),
            'comparison_count':sum(c['comparisons'] for c in CHECKS),'series_count':len(series),
            'checks':CHECKS,'findings':FINDINGS,'source_sha256':hashes,
            'reviewed_at_utc':datetime.now(timezone.utc).isoformat(),
            'generation_consistency':{
                'portfolio_manifest_engine_matches':manifest['engine_sha256']==hashes['portfolio_backtest.py'],
                'portfolio_manifest_legacy_engine_matches':manifest['legacy_engine_sha256']==hashes['backtest.py']},
            'source_mtime_utc':{str(p.relative_to(ROOT)):datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat() for p in source_paths},
            'limitations':[
                'Offline review of saved ledgers and analyses; no independent upstream market-data or point-in-time version verification.',
                'Real-output numerical independence is from analysis formulas, not a second trading engine; portfolio accounting is reviewed separately.',
                'Approved windows and partial-year expectations are for the fixed 2014-10-31 to 2026-10-08 sample.',
                'Recovery uses close NAV with a 1e-12 relative floating-point tolerance; censored duration means elapsed time, not completed recovery.',
                'Annual and stage risk ratios retain 252-session annualization, distinct from the nonannualized period-return column.'
            ]}
    (ROOT/'validation/period_independent_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['status','check_count','pass_count','comparison_count','series_count']},ensure_ascii=False))


if __name__=='__main__':
    main()
