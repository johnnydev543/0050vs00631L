"""Independent portfolio review, offline and standard-library only.

Target trading uses enumerated piecewise affine closed forms, not the engine's
bisection. Cash accrues across market-session gaps and dated payment events,
not the engine's calendar-day loop. Existing engine/results are read only.
This script writes only portfolio_independent_checks.json beside itself.
"""
from __future__ import annotations
import copy
import csv
import gzip
import hashlib
import itertools
import json
import math
from datetime import date
from pathlib import Path
from statistics import mean, stdev
import sys

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import portfolio_backtest as reviewed
import backtest as legacy

SYMBOLS=('00631L','0050')
YEAR=365.2425
CHECKS=[]

def check(name,ok,evidence):
    CHECKS.append({'id':name,'status':'PASS' if ok else 'FAIL','evidence':evidence})
    if not ok:print('FAIL',name,json.dumps(evidence,ensure_ascii=False))


def independent_targets(values,cash,locked,weights,costs):
    """Enumerate investment-funding regime and each instrument's trade side.

    In each region target_i = a_i * post_cost_NAV + b_i, and all fees are
    affine. Solve that linear equation, retaining only region-consistent roots.
    """
    symbols=list(values);total=sum(values.values())+cash+locked
    stock_weight=sum(weights.values())
    buy_fee=(1+costs.slippage)*(1+costs.commission)-1
    sell_fee=1-(1-costs.slippage)*(1-costs.commission-costs.sell_tax)
    tolerance=max(1e-7,total*1e-12)
    if stock_weight==0:
        return {s:0. for s in symbols},total-sum(values.values())*sell_fee
    for constrained in [False,True]:
        a={s:weights[s]/stock_weight if constrained else weights[s] for s in symbols}
        b={s:-a[s]*locked if constrained else 0. for s in symbols}
        for directions in itertools.product([-1,1],repeat=len(symbols)):
            fee={s:buy_fee if sign==1 else -sell_fee for s,sign in zip(symbols,directions)}
            nav=(total-sum(fee[s]*(b[s]-values[s]) for s in symbols))/(1+sum(fee[s]*a[s] for s in symbols))
            target={s:a[s]*nav+b[s] for s in symbols}
            capacity=nav-locked
            if constrained and stock_weight*nav<capacity-tolerance:continue
            if not constrained and stock_weight*nav>capacity+tolerance:continue
            if any(target[s]<-tolerance for s in symbols):continue
            if any(sign*(target[s]-values[s]) < -tolerance for s,sign in zip(symbols,directions)):continue
            return {s:max(0.,target[s]) for s in symbols},nav
    raise ArithmeticError('No piecewise-consistent post-cost target found')


def independent_metrics(rows,trades,initial,rate,costs):
    navs=[r['nav'] for r in rows];days=[date.fromisoformat(r['date']) for r in rows]
    returns=[navs[0]/initial-1]+[b/a-1 for a,b in zip(navs,navs[1:])]
    gaps=[0]+[(b-a).days for a,b in zip(days,days[1:])]
    excess=[r-((1+rate)**(gap/YEAR)-1) for r,gap in zip(returns,gaps)]
    peak=initial;drawdowns=[]
    # Identify contiguous below-running-maximum intervals using endpoint indices.
    vals=[initial]+navs;dates=[days[0]]+days;peak_idx=0;under=[];intervals=[]
    for i,nav in enumerate(navs,1):
        peak=max(peak,nav);drawdowns.append(nav/peak-1)
        if nav>=vals[peak_idx]:
            if under:intervals.append((peak_idx,under[:],i));under=[]
            peak_idx=i
        else:under.append(i)
    if under:intervals.append((peak_idx,under,None))
    recovery=[]
    for peak_idx,under,end_idx in intervals:
        trough=min(under,key=lambda j:vals[j]);end=end_idx if end_idx is not None else len(vals)-1
        recovery.append({'peak_date':dates[peak_idx].isoformat(),'trough_date':dates[trough].isoformat(),
                         'recovery_date':dates[end_idx].isoformat() if end_idx is not None else None,
                         'duration_calendar_days':(dates[end]-dates[peak_idx]).days,
                         'duration_sessions':end-peak_idx,'censored':end_idx is None})
    long=max(recovery,key=lambda r:(r['duration_calendar_days'],r['duration_sessions'])) if recovery else None
    sd=stdev(excess) if len(excess)>1 else 0
    downside=math.sqrt(mean(min(0.,x)**2 for x in excess))
    exposure=[r['nominal_market_exposure'] for r in rows]
    terminal_cost=rows[-1]['holdings_value']*(costs.slippage+(1-costs.slippage)*(costs.commission+costs.sell_tax))
    return {'ending_nav':navs[-1],'ending_liquidation_nav':navs[-1]-terminal_cost,
            'cagr':(navs[-1]/initial)**(YEAR/((days[-1]-days[0]).days+4.5/24))-1,
            'max_drawdown':min(drawdowns),'sharpe':mean(excess)/sd*math.sqrt(252) if sd>0 else None,
            'sortino':mean(excess)/downside*math.sqrt(252) if downside>0 else None,
            'total_trading_cost':sum(t['total_cost'] for t in trades),'number_of_trades':len(trades),
            'rebalance_count':len({t['event_id'] for t in trades if t['reason'] not in ['initial','dividend_reinvestment']}),
            'mean_nominal_exposure':mean(exposure),'min_nominal_exposure':min(exposure),'max_nominal_exposure':max(exposure),
            'longest_recovery':long}


def independent(data,p,rate=0.01,initial=1_000_000.,costs=None):
    costs=costs or legacy.Costs();weights=p.weights
    active=[s for s in SYMBOLS if weights[s]>0]
    q={s:0. for s in SYMBOLS};marks={s:None for s in SYMBOLS};cash=initial
    claims=[];pools=[];rows=[];trades=[];events=[];pending='initial';event_id=0
    days=[date.fromisoformat(d) for d in data['calendar']];prev=days[0]
    split_events={s:data['splits'].get(s,[]) for s in active}
    divs={s:data['dividends'].get(s,[]) for s in active}
    buy_factor=(1+costs.slippage)*(1+costs.commission)
    sell_factor=(1-costs.slippage)*(1-costs.commission-costs.sell_tax)
    def transact(symbol,delta,px,d,reason,event):
        nonlocal cash
        if abs(delta)<1e-7:return
        buy=delta>0;quantity=abs(delta)/px;gross=abs(delta)*(1+costs.slippage if buy else 1-costs.slippage)
        fee=gross*costs.commission;tax=0. if buy else gross*costs.sell_tax
        cash-=delta*(buy_factor if buy else sell_factor)
        q[symbol]+=delta/px
        if abs(cash)<1e-7:cash=0.
        trades.append({'date':d,'symbol':symbol,'reason':reason,'event_id':event,'side':'buy' if buy else 'sell',
                       'quantity':quantity,'raw_open':px,'total_cost':fee+tax+abs(delta)*costs.slippage})
    for i,(d,now) in enumerate(zip(data['calendar'],days)):
        previous_cash=cash;compound=(1+rate)**((now-prev).days/YEAR)
        cash*=compound;interest=cash-previous_cash;paid=0.;accrued=0.
        for pool in pools:pool['amount']*=compound
        for claim in list(claims):
            pay=date.fromisoformat(claim['pay_date'])
            if pay<=now:
                grown=claim['amount']*(1+rate)**((now-pay).days/YEAR)
                interest+=grown-claim['amount'];cash+=grown;paid+=claim['amount']
                pools.append(dict(claim,amount=grown));claims.remove(claim)
        for s in active:
            for ev in split_events[s]:
                sd=date.fromisoformat(ev['date'])
                if (prev<sd<=now) or (i==0 and sd==now):
                    q[s]*=ev['ratio']
                    if marks[s] is not None:marks[s]/=ev['ratio']
            for ev in divs[s]:
                if ev['ex_date']==d and q[s]>0:
                    amount=q[s]*ev['amount'];accrued+=amount
                    claim={'symbol':s,'amount':amount,'pay_date':ev['pay_date']}
                    if ev['pay_date']==d:
                        cash+=amount;paid+=amount;pools.append(claim)
                    else:claims.append(claim)
        owed=sum(c['amount'] for c in claims)
        bars={s:data['prices'][s].get(d) for s in active}
        present={s:b is not None and b.get('Trading_Volume',1)>0 for s,b in bars.items()}
        can={s:present[s] and not ('min' in b and 'max' in b and b['min']==b['max']) for s,b in bars.items()}
        if p.rule=='quarterly' and i and (prev.year,(prev.month-1)//3)!=(now.year,(now.month-1)//3):
            pending='quarterly' if pending!='initial' else 'initial'
        if pending and all(can.values()):
            locked_today=sum(pool['amount'] for pool in pools if pool['pay_date']>=d)
            values={s:q[s]*bars[s]['open'] for s in active}
            before=sum(values.values())+cash+owed
            target,post=independent_targets(values,cash-locked_today,owed+locked_today,weights,costs)
            delta={s:target[s]-values[s] for s in active};event_id+=1
            event_cost_before=sum(t['total_cost'] for t in trades)
            for s in sorted(active,key=lambda k:(delta[k]>=0,k)):
                transact(s,delta[s],bars[s]['open'],d,pending,event_id)
            events.append({'date':d,'reason':pending,'event_id':event_id,'nav_before':before,'nav_after':post,
                           'locked':owed+locked_today,'cash_after':cash,'paid_today':locked_today,
                           'post_open_values':{s:q[s]*bars[s]['open'] for s in active},
                           'cost':sum(t['total_cost'] for t in trades)-event_cost_before})
            pools=[pool for pool in pools if pool['pay_date']>=d];pending=None
        for s in active:
            eligible=[pool for pool in pools if pool['symbol']==s and pool['pay_date']<d]
            if eligible and can[s]:
                locked_today=sum(pool['amount'] for pool in pools if pool['pay_date']>=d)
                budget=min(sum(pool['amount'] for pool in eligible),cash-locked_today)
                if budget>1e-7:
                    event_id+=1;transact(s,budget/buy_factor,bars[s]['open'],d,'dividend_reinvestment',event_id)
                pools=[pool for pool in pools if pool not in eligible]
        for s in active:
            if present[s]:marks[s]=bars[s]['close']
        values={s:q[s]*(marks[s] or 0.) for s in SYMBOLS}
        nav=sum(values.values())+cash+owed
        w={s:values[s]/nav for s in SYMBOLS};cashlike=(cash+owed)/nav;exposure=2*w['00631L']+w['0050']
        if pending is None:
            if p.rule=='threshold' and max([abs(w[s]-weights[s]) for s in SYMBOLS]+[abs(cashlike-p.cash_weight)])>=p.weight_threshold-1e-12:
                pending='threshold'
            elif p.rule=='exposure_threshold' and abs(exposure-p.target_exposure)>=p.exposure_threshold-1e-12:
                pending='exposure_threshold'
        rows.append({'date':d,'nav':nav,'cash':cash,'receivables':owed,'holdings_value':sum(values.values()),
                     'units_00631L':q['00631L'],'units_0050':q['0050'],
                     'weight_00631L':w['00631L'],'weight_0050':w['0050'],'weight_cash':cash/nav,'weight_receivables':owed/nav,
                     'nominal_market_exposure':exposure,'cash_interest':interest,'dividends_accrued':accrued,'dividends_paid':paid,
                     'pending_rebalance':pending or ''})
        prev=now
    return {'daily':rows,'trades':trades,'events':events,'metrics':independent_metrics(rows,trades,initial,rate,costs)}


def read_csv(path):
    opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))


def synthetic(rows,dividends=None):
    return {'calendar':[r[0] for r in rows],'start':rows[0][0],'end':rows[-1][0],
            'prices':{s:{d:{'open':prices[i],'close':prices[i],'Trading_Volume':1} for d,*prices in rows} for i,s in enumerate(SYMBOLS)},
            'splits':{s:[] for s in SYMBOLS},'dividends':{'00631L':[],'0050':dividends or []},
            'metadata':{'verified_suspensions':{s:[] for s in SYMBOLS}}}


def main():
    data=json.loads((BASE/'normalized.json').read_text())
    cached={}
    for rate in [0.0,0.01,0.02]:
        for p in reviewed.portfolios():
            name=p.name+' cash='+str(rate)
            own=independent(data,p,rate);got=reviewed.run_portfolio(data,p,rate);cached[p.name,rate]=(own,got)
            fields=['nav','cash','receivables','holdings_value','units_00631L','units_0050','weight_00631L','weight_0050',
                    'weight_cash','weight_receivables','nominal_market_exposure','cash_interest','dividends_accrued','dividends_paid']
            delta={f:max(abs(a[f]-b[f]) for a,b in zip(own['daily'],got['daily'])) for f in fields}
            good=all(v<(1e-9 if f.startswith('weight') or f=='nominal_market_exposure' else .01) for f,v in delta.items())
            check('PORT-PATH '+name,good and len(own['daily'])==len(got['daily']),delta)
            tx=lambda x:[(t['date'],t['symbol'],t['side'],t['reason'],t['event_id']) for t in x]
            qdiff=max((abs(a['quantity']-b['quantity']) for a,b in zip(own['trades'],got['trades'])),default=0)
            check('PORT-TRADES '+name,tx(own['trades'])==tx(got['trades']) and qdiff<1e-4,
                  {'transactions_match':tx(own['trades'])==tx(got['trades']),'max_quantity_difference':qdiff,'trade_count':len(own['trades'])})
            mdiff={};mismatch=[]
            for f,v in own['metrics'].items():
                actual=got['metrics'][f]
                if f=='longest_recovery':
                    if not all(actual[k]==x for k,x in v.items()):mismatch.append(f)
                elif v is None:
                    if actual is not None:mismatch.append(f)
                else:
                    mdiff[f]=abs(actual-v)
                    if mdiff[f]>(.01 if f in ['ending_nav','ending_liquidation_nav','total_trading_cost'] else 1e-9):mismatch.append(f)
            check('PORT-METRICS '+name,not mismatch,{'differences':mdiff,'mismatched_fields':mismatch,'longest_recovery':own['metrics']['longest_recovery']})
            cost_error=max(abs(e['nav_before']-e['nav_after']-e['cost']) for e in own['events'])
            funding_ok=all(e['cash_after']>=e['paid_today']-1e-5 and sum(e['post_open_values'].values())<=e['nav_after']-e['locked']+1e-5 for e in own['events'])
            no_negative=all(r['cash']>=-1e-5 and r['units_00631L']>=0 and r['units_0050']>=0 for r in own['daily'])
            check('PORT-FUNDING '+name,cost_error<1e-5 and funding_ok and no_negative,
                  {'max_event_nav_cost_error':cost_error,'locked_funds_preserved':funding_ok,'no_negative_cash_or_units':no_negative,
                   'events_with_locked_assets':sum(e['locked']>0 for e in own['events'])})
            check('PORT-EXPOSURE '+name,all(r['actual_internal_exposure'] is None for r in got['daily']) and all(abs(2*r['weight_00631L']+r['weight_0050']-r['nominal_market_exposure'])<1e-12 for r in got['daily']),
                  {'actual_internal_exposure':'null for all observations','formula':'2 * weight_00631L + weight_0050'})
            scenario=f'cash_{rate:.6f}'.replace('.','p');saved_dir=BASE/'portfolio_results'/scenario/p.name
            metrics=json.loads((saved_dir/'metrics.json').read_text());config=json.loads((saved_dir/'config.json').read_text());saved=read_csv(saved_dir/'daily.csv.gz')
            saved_trades=read_csv(saved_dir/'trades.csv')
            trade_file_ok=len(saved_trades)==len(got['trades']) and all(
                all((a[k]=='' if v is None else float(a[k])==v if isinstance(v,(int,float)) else a[k]==v)
                    for k,v in b.items()) for a,b in zip(saved_trades,got['trades']))
            daily_file_ok=len(saved)==len(got['daily']) and all(
                all(float(a[f])==b[f] for f in fields) and a['actual_internal_exposure']==''
                for a,b in zip(saved,got['daily']))
            check('PORT-SAVED '+name,metrics==got['metrics'] and config==got['config'] and daily_file_ok and trade_file_ok,
                  {'metrics_match':metrics==got['metrics'],'config_match':config==got['config'],'daily_rows':len(saved),
                   'daily_account_exposure_fields_exact':daily_file_ok,'all_saved_trade_fields_exact':trade_file_ok})
            if p.label=='A':
                old=legacy.run_backtest(data,legacy.Strategy('control','00631L',.8,p.rule),rate)
                control=max(abs(a['nav']-b['nav']) for a,b in zip(got['daily'],old['daily']))
                trade_match=[(t['date'],t['side'],t['reason']) for t in got['trades']]==[(t['date'],t['side'],t['reason']) for t in old['trades']]
                check('PORT-A-CONTROL '+name,control<.01 and trade_match,{'max_legacy_nav_difference_twd':control,'legacy_trade_dates_sides_reasons_match':trade_match})
    # Future mutations affect neither earlier portfolio states nor earlier orders.
    future=copy.deepcopy(data);cutoff='2022-12-30'
    for bars in future['prices'].values():
        for d,bar in bars.items():
            if d>cutoff:
                for k in ['open','close','min','max']:bar[k]*=1.13
    for p in reviewed.portfolios():
        original=cached[p.name,.01][1];changed=reviewed.run_portfolio(future,p,.01)
        pre=lambda r,k:[x for x in r[k] if x['date']<=cutoff]
        check('PORT-FUTURE '+p.name,pre(original,'daily')==pre(changed,'daily') and pre(original,'trades')==pre(changed,'trades'),{'cutoff':cutoff,'future_price_multiplier':1.13})
    # Hand-computable binding case: full B rebalance on dividend pay date may
    # not spend that payment. Subsequent DRIP invests only that locked pool.
    special=synthetic([('2020-06-29',100,100),('2020-06-30',100,10),('2020-07-01',100,10),('2020-07-02',100,10)],
                       [{'ex_date':'2020-06-30','pay_date':'2020-07-01','amount':90}])
    p=reviewed.Portfolio('B',.6,.4,0.,'quarterly')
    got=reviewed.run_portfolio(special,p,0.,1000.,legacy.Costs(0,0,0));own=independent(special,p,0.,1000.,legacy.Costs(0,0,0))
    pay=got['daily'][2];after=got['daily'][3]
    check('PORT-LOCKED-PAYMENT-SYNTHETIC',abs(pay['cash']-360)<1e-8 and abs(pay['units_00631L']-3.84)<1e-8 and abs(pay['units_0050']-25.6)<1e-8 and abs(after['cash'])<1e-8 and abs(after['units_0050']-61.6)<1e-8 and all(abs(a['nav']-b['nav'])<1e-8 for a,b in zip(own['daily'],got['daily'])),
          {'payday_cash':pay['cash'],'payday_units_00631L':pay['units_00631L'],'payday_units_0050':pay['units_0050'],'post_payment_units_0050':after['units_0050'],'post_payment_cash':after['cash']})
    # Compare the direct piecewise solver to bisection over cash/locked regimes.
    max_target_error=0.;solver_cases=0
    for p in reviewed.portfolios():
        for cash,locked in [(0.,0.),(0.,100.),(200.,800.),(300.,0.),(1e6,0.)]:
            values={'00631L':600.,'0050':300.} if p.label!='A' else {'00631L':900.}
            expected,nav=independent_targets(values,cash,locked,p.weights,legacy.Costs())
            actual,anav=reviewed.solve_targets(values,cash,locked,p.weights,legacy.Costs())
            max_target_error=max(max_target_error,abs(nav-anav),*(abs(expected[s]-actual[s]) for s in values));solver_cases+=1
    check('PORT-PIECEWISE-SOLVER',max_target_error<1e-7,{'cases':solver_cases,'maximum_target_or_nav_difference':max_target_error})
    # Weight bands and nominal-exposure bands are intentionally different for B/C.
    contrast=[]
    for label in ['B','C']:
        a=cached[label+'_threshold',.01][1];b=cached[label+'_exposure_threshold',.01][1]
        contrast.append({'portfolio':label,'weight_band_rebalances':a['metrics']['rebalance_count'],'exposure_band_rebalances':b['metrics']['rebalance_count'],
                         'weight_band_ending_nav':a['metrics']['ending_nav'],'exposure_band_ending_nav':b['metrics']['ending_nav']})
    check('PORT-BAND-DISTINCTION',all(c['weight_band_rebalances']!=c['exposure_band_rebalances'] for c in contrast),contrast)
    # User-defined exposure includes all-cash and both single-instrument edges.
    for rate in [0.0,.01,.02]:
        for rule in ['quarterly','threshold','exposure_threshold']:
            p=reviewed.Portfolio('cash',0.,0.,1.,rule,.075,.15)
            result=reviewed.run_portfolio(data,p,rate,250_000.)
            elapsed=(date.fromisoformat(data['calendar'][-1])-date.fromisoformat(data['calendar'][0])).days
            expected=250_000.*(1+rate)**(elapsed/YEAR)
            check('PORT-CUSTOM-CASH '+rule+' '+str(rate),
                  abs(result['metrics']['ending_nav']-expected)<1e-5 and result['metrics']['sharpe'] is None
                  and result['metrics']['sortino'] is None and result['metrics']['number_of_trades']==0
                  and all(r['nominal_market_exposure']==0 and r['actual_internal_exposure'] is None for r in result['daily']),
                  {'analytic_final_cash':expected,'engine_final_cash':result['metrics']['ending_nav'],
                   'sharpe':result['metrics']['sharpe'],'sortino':result['metrics']['sortino'],
                   'number_of_trades':result['metrics']['number_of_trades']})
    for symbol,weights in [('0050',(0.,1.,0.)),('00631L',(1.,0.,0.))]:
        for rule in ['quarterly','threshold']:
            p=reviewed.Portfolio('single',*weights,rule)
            result=reviewed.run_portfolio(data,p,.01)
            old=legacy.run_backtest(data,legacy.Strategy('single',symbol,1.,'buyhold'),.01)
            difference=max(abs(a['nav']-b['nav']) for a,b in zip(result['daily'],old['daily']))
            check('PORT-CUSTOM-SINGLE '+symbol+' '+rule,difference<.01,
                  {'max_legacy_buyhold_nav_difference_twd':difference})
    for rule in ['quarterly','threshold','exposure_threshold']:
        p=reviewed.Portfolio('custom',.3,.4,.3,rule,.075,.15)
        own=independent(data,p,.015,250_000.);got=reviewed.run_portfolio(data,p,.015,250_000.)
        difference=max(abs(a['nav']-b['nav']) for a,b in zip(own['daily'],got['daily']))
        tx=lambda result:[(t['date'],t['symbol'],t['side'],t['reason']) for t in result['trades']]
        check('PORT-CUSTOM-MIX '+rule,difference<.01 and tx(own)==tx(got),
              {'max_independent_nav_difference_twd':difference,'principal':250000,'weights':[.3,.4,.3],
               'cash_yield':.015,'weight_threshold':.075,'exposure_threshold':.15,'transactions_match':tx(own)==tx(got)})
    invalid_rejected=0
    for weights in [(-.1,.5,.6),(.6,.5,-.1),(float('nan'),0.,1.),(.4,.4,.4)]:
        try:reviewed.Portfolio('invalid',*weights,'threshold')
        except ValueError:invalid_rejected+=1
    for rate in [float('nan'),float('inf'),-1.]:
        try:reviewed.run_portfolio(data,reviewed.Portfolio('cash',0.,0.,1.,'threshold'),rate)
        except ValueError:invalid_rejected+=1
    for threshold in [0.,-.1,float('nan'),float('inf')]:
        try:reviewed.Portfolio('invalid',.3,.4,.3,'threshold',threshold,.2)
        except ValueError:invalid_rejected+=1
    check('PORT-CUSTOM-INVALID-INPUTS',invalid_rejected==11,{'invalid_cases_rejected':invalid_rejected,'cases':11})
    # Independently validate the combined 48-series exposure export, including
    # legacy one-instrument books converted into a common multi-asset schema.
    fields=['nav','weight_00631L','weight_0050','weight_cash','weight_receivables','nominal_market_exposure']
    errors={f:0. for f in fields};last_key=None;source_rows={};export_rows=0;groups=set();unknown_preserved=True
    with gzip.open(BASE/'analysis/daily_nominal_exposure.csv.gz','rt',encoding='utf-8',newline='') as handle:
        for row in csv.DictReader(handle):
            key=(row['kind'],row['strategy'],float(row['cash_yield_assumption']))
            if key!=last_key:
                kind,strategy,rate=key;directory='results' if kind=='legacy' else 'portfolio_results'
                scenario=f'cash_{rate:.6f}'.replace('.','p')
                filename='daily.csv' if kind=='legacy' else 'daily.csv.gz'
                source_rows={r['date']:r for r in read_csv(BASE/directory/scenario/strategy/filename)}
                last_key=key
            source=source_rows[row['date']];expected={'nav':float(source['nav'])}
            if key[0]=='legacy':
                held=float(source['holdings_value'])/expected['nav'];ordinary=key[1]=='0050_buyhold'
                expected.update(weight_00631L=0. if ordinary else held,weight_0050=held if ordinary else 0.,
                                weight_cash=float(source['cash'])/expected['nav'],weight_receivables=float(source['receivables'])/expected['nav'],
                                nominal_market_exposure=(1. if ordinary else 2.)*held)
            else:
                expected.update({f:float(source[f]) for f in fields if f!='nav'})
            for f in fields:errors[f]=max(errors[f],abs(float(row[f])-expected[f]))
            unknown_preserved &= row['actual_internal_exposure']==''
            groups.add(key);export_rows+=1
    check('PORT-DAILY-EXPOSURE-EXPORT',len(groups)==48 and export_rows==48*len(data['calendar']) and max(errors.values())<1e-12 and unknown_preserved,
          {'series_groups':len(groups),'rows':export_rows,'maximum_field_differences':errors,'unknown_internal_exposure_empty':unknown_preserved})
    manifest=json.loads((BASE/'portfolio_results/manifest.json').read_text())
    expected={'engine_sha256':hashlib.sha256((BASE/'portfolio_backtest.py').read_bytes()).hexdigest(),
              'legacy_engine_sha256':hashlib.sha256((BASE/'backtest.py').read_bytes()).hexdigest(),
              'input_sha256':hashlib.sha256((BASE/'normalized.json').read_bytes()).hexdigest()}
    check('PORT-MANIFEST',all(manifest[k]==v for k,v in expected.items()) and manifest['runs']==24,expected)
    result={'status':'PASS' if all(c['status']=='PASS' for c in CHECKS) else 'FAIL','count':len(CHECKS),'checks':CHECKS,
            'scope':'24 portfolio paths; independent target algebra, accounting, metrics, preserved legacy A control, funding constraints, actual-exposure null semantics, persisted outputs and future-price invariance.'}
    path=BASE/'validation/portfolio_independent_checks.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'checks':result['count'],'failed':[c['id'] for c in CHECKS if c['status']=='FAIL']}))
    if result['status']!='PASS':raise SystemExit(1)

if __name__=='__main__':main()
