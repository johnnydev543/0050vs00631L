"""Read-only independent review: bisection execution and session/event accrual.
Does not import execution, portfolio or metric helpers for independent calculation.
"""
import copy
import csv
import hashlib
import importlib.util
import json
import math
from datetime import date
from pathlib import Path
from statistics import mean, stdev

OUT = Path(__file__).resolve().parents[1]
VALIDATION = Path(__file__).resolve().parent
data = json.loads((OUT/'normalized.json').read_text())
spec = importlib.util.spec_from_file_location('reviewed_engine', OUT/'backtest.py')
engine = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)

checks = []
def check(name, ok, evidence):
    checks.append({'id': name, 'status': 'PASS' if ok else 'FAIL', 'evidence': evidence})
    if not ok:
        print('FAIL', name, evidence)

# Bisection chooses signed change to holdings by post-cost residual weight.
# This deliberately does not use the closed-form target equation from engine.
def exchange(shares, cash, owed, px, target):
    buy_unit = px * 1.0005 * 1.001425
    sell_unit = px * .9995 * (1-.001425-.001)
    def state(delta):
        new_cash = cash - delta * (buy_unit if delta >= 0 else sell_unit)
        return new_cash, (shares+delta)*px/(new_cash+(shares+delta)*px+owed)
    lo, hi = -shares, cash/buy_unit
    for _ in range(75):
        mid = (lo+hi)/2
        if state(mid)[1] < target: lo = mid
        else: hi = mid
    delta = (lo+hi)/2
    new_cash = state(delta)[0]
    if abs(delta)*px < 1e-6:
        return shares, cash, None
    return shares+delta, max(0.0,new_cash), (delta, px, cash-new_cash)


def independent(symbol, target, rule, rate, reject_one_price=True):
    sessions = data['calendar']; days = [date.fromisoformat(d) for d in sessions]
    splits = {x['date']:x['ratio'] for x in data['splits'][symbol]}
    exs = {x['ex_date']:x for x in data['dividends'][symbol]}
    shares, cash, mark = 0., 1_000_000., None
    owed = []
    navs = []; tx = []; pending = True; reinvest_after = []
    prevday = days[0]
    for i,(dt,d) in enumerate(zip(days,sessions)):
        # Cash already credited on previous market date compounds to today's
        # date; new intervening payments compound only from their payment date.
        cash *= (1+rate)**((dt-prevday).days/365.2425)
        for claim in list(owed):
            pay = date.fromisoformat(claim[0])
            if pay <= dt:
                cash += claim[1]*(1+rate)**((dt-pay).days/365.2425)
                reinvest_after.append(claim[0]); owed.remove(claim)
        if d in splits:
            shares *= splits[d]
            if mark is not None: mark /= splits[d]
        if d in exs:
            event = exs[d]; amount = shares*event['amount']
            if amount:
                if event['pay_date'] == d:
                    cash += amount; reinvest_after.append(d)
                else: owed.append((event['pay_date'], amount))
        owed_value = sum(x[1] for x in owed)
        if rule == 'quarterly' and i:
            quarter = lambda v:(v.year,(v.month-1)//3)
            pending |= quarter(dt)!=quarter(prevday)
        bar = data['prices'][symbol].get(d)
        if bar is not None:
            executable = not reject_one_price or bar.get('max') != bar.get('min') or 'max' not in bar or 'min' not in bar
            if executable and (pending or (rule == 'buyhold' and any(p<d for p in reinvest_after))):
                shares,cash,trade=exchange(shares,cash,owed_value,bar['open'],target)
                if trade: tx.append((d,trade[0],trade[1],trade[2]))
                pending=False
                reinvest_after=[p for p in reinvest_after if p>=d]
            mark=bar['close']
        nav=cash+shares*(mark or 0)+owed_value
        navs.append(nav)
        if rule=='threshold' and abs(shares*(mark or 0)/nav-target)>=.10-1e-12:
            pending=True
        prevday=dt
    returns=[navs[0]/1_000_000-1]+[b/a-1 for a,b in zip(navs,navs[1:])]
    gaps=[0]+[(b-a).days for a,b in zip(days,days[1:])]
    excess=[r-((1+rate)**(gap/365.2425)-1) for r,gap in zip(returns,gaps)]
    peaks=[]; peak=1_000_000
    for nav in navs:
        peak=max(peak,nav);peaks.append(peak)
    mdd=min(n/p-1 for n,p in zip(navs,peaks))
    metrics={'ending_nav':navs[-1], 'cagr':(navs[-1]/1e6)**(365.2425/((days[-1]-days[0]).days+4.5/24))-1,
             'max_drawdown':mdd,'sharpe':mean(excess)/stdev(excess)*math.sqrt(252),
             'sortino':mean(excess)/math.sqrt(mean(min(0,x)**2 for x in excess))*math.sqrt(252)}
    return navs,tx,metrics

def recovery_by_intervals(navs):
    """Find contiguous runs below the running peak, then measure endpoints."""
    vals=[1_000_000.]+navs
    dates=[data['calendar'][0]]+data['calendar']
    peak_idx=0;underwater=[]
    intervals=[]
    for i in range(1,len(vals)):
        if vals[i] >= vals[peak_idx]:
            if underwater:
                intervals.append((peak_idx,underwater[:],i))
                underwater=[]
            peak_idx=i
        else:
            underwater.append(i)
    if underwater:intervals.append((peak_idx,underwater,None))
    result=[]
    for peak,under,recovered in intervals:
        trough=min(under,key=lambda k:vals[k]);end=recovered or len(vals)-1
        result.append({'peak_date':dates[peak], 'trough_date':dates[trough],
                       'recovery_date':dates[recovered] if recovered is not None else None,
                       'duration_calendar_days':(date.fromisoformat(dates[end])-date.fromisoformat(dates[peak])).days,
                       'duration_sessions':end-peak,'censored':recovered is None})
    return max(result,key=lambda e:(e['duration_calendar_days'],e['duration_sessions'])) if result else None

for rate in [0.0,.01,.02]:
    for strategy in engine.standard_strategies():
        own_nav,own_tx,own_metrics = independent(strategy.symbol,strategy.weight,strategy.rebalance,rate)
        got=engine.run_backtest(data,strategy,rate)
        name=strategy.name+' cash='+str(rate)
        max_nav_error=max(abs(a-b['nav']) for a,b in zip(own_nav,got['daily']))
        tx_days=[t[0] for t in own_tx]
        got_tx_days=[t['date'] for t in got['trades']]
        differences={key:abs(val-got['metrics'][key]) for key,val in own_metrics.items()}
        check('independent_'+name,max_nav_error<.01 and tx_days==got_tx_days and all(v<(0.01 if k=='ending_nav' else 1e-10) for k,v in differences.items()),
              {'max_daily_nav_difference_twd':max_nav_error,'trade_dates_equal':tx_days==got_tx_days,'metric_absolute_differences':differences,'trades':len(own_tx)})
        # Accounting loss must equal all costs at the raw opening mark.
        cost_error=max((abs(t['nav_before']-t['nav_after']-t['total_cost']) for t in got['trades']),default=0)
        target_error=max((abs(t['weight_after']-strategy.weight) for t in got['trades']),default=0)
        permitted_cash = all(r['cash']>=-1e-8 and r['units']>=0 for r in got['daily'])
        suspension=set(data['metadata']['verified_suspensions'][strategy.symbol])
        check('ledger_'+name,cost_error<1e-7 and target_error<1e-10 and permitted_cash and not suspension.intersection(got_tx_days),
              {'max_cost_reconciliation_twd':cost_error,'max_target_weight_difference':target_error,'no_negative_cash_or_units':permitted_cash,'no_trades_on_suspension':not suspension.intersection(got_tx_days)})
        recovery=recovery_by_intervals(own_nav)
        actual_recovery=got['metrics']['longest_recovery']
        check('recovery_'+name,all(actual_recovery[k]==v for k,v in recovery.items()),recovery)
        scenario=f'cash_{rate:.6f}'.replace('.','p').replace('-','m')
        saved_dir=OUT/'results'/scenario/strategy.name
        saved_metrics=json.loads((saved_dir/'metrics.json').read_text())
        saved_config=json.loads((saved_dir/'config.json').read_text())
        with (saved_dir/'daily.csv').open() as handle:
            saved_daily=list(csv.DictReader(handle))
        saved_nav_error=max(abs(float(a['nav'])-b['nav']) for a,b in zip(saved_daily,got['daily']))
        check('persisted_'+name,saved_metrics==got['metrics'] and saved_config==got['config'] and len(saved_daily)==len(got['daily']) and saved_nav_error==0,
              {'metrics_identical':saved_metrics==got['metrics'],'config_identical':saved_config==got['config'],'max_saved_daily_nav_difference_twd':saved_nav_error})

# Closed-form 00631L 100% path; the final outcome requires only initial fill,
# 22:1 split and final close, independent of all intermediate values.
expected=1e6/(19.99*1.0005*1.001425)*22*40.9
r=engine.run_backtest(data,engine.Strategy('closed','00631L',1,'buyhold'),0)
check('closed_form_100_buyhold',abs(r['metrics']['ending_nav']-expected)<1e-7,{'expected_twd':expected,'engine_twd':r['metrics']['ending_nav']})

# Verify all immutable snapshot checksums recorded by normalizer.
for item in data['metadata']['data_manifest']:
    digest=hashlib.sha256((OUT/item['path']).read_bytes()).hexdigest()
    check('source_hash_'+item['path'],digest==item['sha256'],{'sha256':digest})

manifest=json.loads((OUT/'results/manifest.json').read_text())
check('result_manifest_current_engine_and_data',
      manifest['engine_sha256']==hashlib.sha256((OUT/'backtest.py').read_bytes()).hexdigest()
      and manifest['input_file_sha256']==hashlib.sha256((OUT/'normalized.json').read_bytes()).hexdigest(),
      {'engine_sha256':hashlib.sha256((OUT/'backtest.py').read_bytes()).hexdigest(),
       'normalized_sha256':hashlib.sha256((OUT/'normalized.json').read_bytes()).hexdigest()})

raw_queries={}
for name in ['download.jsonl','parvalue.jsonl']:
    for line in (OUT/'raw'/name).read_text().splitlines():
        envelope=json.loads(line)
        if envelope.get('http_status')!=200:continue
        response=json.loads(envelope['response_text'])
        if response.get('status')!=200:continue
        params=envelope['params'];raw_queries[(params['dataset'],params.get('data_id',''))]=response['data']
for symbol in ['0050','00631L']:
    source={r['date']:r for r in raw_queries[('TaiwanStockPrice',symbol)] if data['start']<=r['date']<=data['end']}
    check('all_normalized_prices_identical_to_raw_'+symbol,source==data['prices'][symbol],{'rows':len(source)})

# Read and independently compare official exchange sample raw data.
official=json.loads((OUT/'twse_spotcheck.json').read_text())
for sample in official['checks']:
    raw_path=OUT/sample['raw_file'];raw=json.loads(raw_path.read_text())
    fieldidx={name:i for i,name in enumerate(raw['fields'])};n=0;issues=[]
    for row in raw['data']:
        roc=row[fieldidx['日期']].split('/')
        day=f'{int(roc[0])+1911:04d}-{int(roc[1]):02d}-{int(roc[2]):02d}'
        if day<data['start'] or day>data['end']:continue
        normalized=data['prices'][sample['stock_id']].get(day)
        if normalized is None:issues.append(day+' missing normalized row');continue
        for official_field,key in [('開盤價','open'),('最高價','max'),('最低價','min'),('收盤價','close')]:
            if float(str(row[fieldidx[official_field]]).replace(',',''))!=normalized[key]:issues.append(day+' '+key)
            n+=1
    check('twse_independent_'+sample['stock_id']+'_'+sample['month'],not issues and n==sample['compared_fields'] and hashlib.sha256(raw_path.read_bytes()).hexdigest()==sample['sha256'],{'fields_compared':n,'issues':issues})

# Independent coverage, OHLC, split date/no double-adjustment and dividend checks.
for symbol in ['0050','00631L']:
    bars=data['prices'][symbol]
    missing=set(data['calendar'])-set(bars)
    check('coverage_'+symbol,missing==set(data['metadata']['verified_suspensions'][symbol]),{'market_sessions':len(data['calendar']),'observed_bars':len(bars),'missing_dates':sorted(missing)})
    for split in data['splits'][symbol]:
        d=split['date'];i=data['calendar'].index(d);prev=data['calendar'][i-1]
        while prev not in bars:
            i-=1;prev=data['calendar'][i-1]
        corrected_return=bars[d]['close']*split['ratio']/bars[prev]['close']-1
        check('split_'+symbol,abs(corrected_return)<.3,{'date':d,'previous_tradable_date':prev,'split_neutral_return':corrected_return})

# No-lookahead empirical check: mutate only prices AFTER a fixed cutoff and
# verify that earlier daily account values and trades remain exactly identical.
cutoff='2022-12-30'; altered=copy.deepcopy(data)
for bars in altered['prices'].values():
    for d,bar in bars.items():
        if d>cutoff:
            for key in ['open','close','min','max']:bar[key]*=1.19
for strategy in engine.standard_strategies():
    original=engine.run_backtest(data,strategy,.01)
    mutated=engine.run_backtest(altered,strategy,.01)
    pre=lambda result,kind:[r for r in result[kind] if r['date']<=cutoff]
    check('future_price_invariance_'+strategy.name,pre(original,'daily')==pre(mutated,'daily') and pre(original,'trades')==pre(mutated,'trades'),{'cutoff':cutoff,'future_price_multiplier':1.19})

# Every dividend entitlement uses holdings from the prior session; no same-day
# ex-date purchased units earn that dividend. First reinvestment is after pay.
base=engine.run_backtest(data,engine.Strategy('0050','0050',1,'buyhold'),.01)
byday={r['date']:r for r in base['daily']}
for event in data['dividends']['0050']:
    i=data['calendar'].index(event['ex_date']);prev=data['calendar'][i-1]
    expected_entitlement=byday[prev]['units']*event['amount']
    first_after=next(d for d in data['calendar'] if d>event['pay_date'] and d in data['prices']['0050'])
    matching=[t for t in base['trades'] if t['date']==first_after and t['reason']=='dividend_reinvestment']
    check('dividend_'+event['ex_date'],abs(byday[event['ex_date']]['dividends_accrued']-expected_entitlement)<1e-7 and len(matching)==1,{'pay_date':event['pay_date'],'reinvestment_date':first_after,'entitlement_twd':expected_entitlement})

payload={'checks':checks,'status':'PASS' if all(x['status']=='PASS' for x in checks) else 'FAIL','count':len(checks)}
(VALIDATION/'independent_review_checks.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
print(json.dumps({'status':payload['status'],'checks':len(checks),'failures':[x['id'] for x in checks if x['status']=='FAIL']},ensure_ascii=False))

# Execution-model sensitivity. Raw input files remain unchanged. Both paths
# are calculated with the independent bisection/session implementation.
sensitivity=[]
for rate in [0.0,.01,.02]:
    for strategy in engine.standard_strategies():
        _, rejected_tx, rejected = independent(strategy.symbol,strategy.weight,strategy.rebalance,rate,True)
        _, allowed_tx, allowed = independent(strategy.symbol,strategy.weight,strategy.rebalance,rate,False)
        delta=rejected['ending_nav']-allowed['ending_nav']
        if abs(delta)>.01:
            sensitivity.append({'strategy':strategy.name,'cash_rate':rate,
               'reject_minus_allow_final_nav':delta,'reject_final_nav':rejected['ending_nav'],
               'allow_final_nav':allowed['ending_nav'],
               'reject_minus_allow_cagr':rejected['cagr']-allowed['cagr'],
               'allowed_one_price_trades':[{'date':t[0],'side':'buy' if t[1]>0 else 'sell'} for t in allowed_tx
                    if data['prices'][strategy.symbol][t[0]]['min']==data['prices'][strategy.symbol][t[0]]['max']]})
(VALIDATION/'one_price_execution_sensitivity.json').write_text(json.dumps(sensitivity,ensure_ascii=False,indent=2))
if any(item['status']=='FAIL' for item in checks):
    raise SystemExit(1)
