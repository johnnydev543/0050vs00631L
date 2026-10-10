"""Offline source/bundle review. Does not open browsers or change app sources."""
from __future__ import annotations
import base64
import copy
import gzip
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime,timezone

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from web_calculator import run_request,run_comparison
from portfolio_backtest import Portfolio,run_portfolio
from report_portfolios import build_extension

CHECKS=[]
def check(name,ok,evidence):
    CHECKS.append({'id':name,'status':'PASS' if ok else 'FAIL','evidence':evidence})
    if not ok:print('FAIL',name,json.dumps(evidence,ensure_ascii=False))

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

class Scripts(HTMLParser):
    def __init__(self):super().__init__();self.scripts={};self.current=None
    def handle_starttag(self,tag,attrs):
        if tag=='script':
            attrs=dict(attrs);self.current=attrs.get('id','inline-'+str(len(self.scripts)));self.scripts[self.current]=''
    def handle_data(self,data):
        if self.current is not None:self.scripts[self.current]+=data
    def handle_endtag(self,tag):
        if tag=='script':self.current=None


def main():
    data=json.loads((BASE/'normalized.json').read_text())
    # Synthetic prices serve only an algebra/input-validation grid, never the reported real backtest.
    synthetic={'start':'2020-01-02','end':'2020-01-03','calendar':['2020-01-02','2020-01-03'],
        'prices':{s:{d:{'open':100.,'close':100.,'Trading_Volume':1} for d in ['2020-01-02','2020-01-03']} for s in ['0050','00631L']},
        'splits':{'0050':[],'00631L':[]},'dividends':{'0050':[],'00631L':[]},
        'metadata':{'verified_suspensions':{'0050':[],'00631L':[]}}}
    errors=[];cases=0
    for exposure in range(0,201,5):
        maximum=min(exposure,200-exposure)
        for ordinary in [0.,maximum/2,maximum]:
            result=run_request(synthetic,{'exposure':exposure,'ordinary':ordinary})
            w=result['params']['weights'];cases+=1
            if min(w.values()) < -1e-12 or abs(sum(w.values())-1)>1e-12 or abs(2*w['00631L']+w['0050']-exposure/100)>1e-12:
                errors.append([exposure,ordinary,w])
    check('CALC-WEIGHT-ALGEBRA',not errors,{'synthetic_mapping_cases':cases,'violations':errors,'constraint':'0 <= ordinary <= min(E, 200-E); 2*wL+w50=E/100; cash>=0; weights sum to 1'})
    invalid=[{'exposure':201},{'exposure':-1},{'ordinary':-1},{'exposure':120,'ordinary':80.001},
             {'exposure':50,'ordinary':50.001},{'principal':0},{'principal':float('inf')},
             {'cash_rate':10.01},{'cash_rate':-1},{'cash_rate':float('nan')},
             {'threshold':0},{'rule':'threshold','threshold':100.1},{'rule':'exposure_threshold','threshold':200.1},
             {'rule':"__import__('os').system('invalid')"},{'exposure':"__import__('os')"}]
    rejects=0
    for request in invalid:
        try:run_request(synthetic,request)
        except (ValueError,TypeError):rejects+=1
    check('CALC-INVALID-INPUTS',rejects==len(invalid),{'cases':len(invalid),'rejected':rejects})
    for label,ordinary in [('A',0),('B',40),('C',20)]:
        for rule in ['quarterly','threshold']:
            result=run_request(data,{'exposure':160,'ordinary':ordinary,'rule':rule})
            saved=json.loads((BASE/'portfolio_results/cash_0p010000'/f'{label}_{rule}'/'metrics.json').read_text())
            errors={k:abs(result['metrics'][k]-saved[k]) for k in ['ending_nav','cagr','max_drawdown','sharpe','sortino']}
            check('CALC-REAL-FIXED '+label+' '+rule,
                  all(v<(.01 if k=='ending_nav' else 1e-9) for k,v in errors.items()),errors)
    for request in [{'exposure':120,'ordinary':25,'principal':150,'cash_rate':2,'rule':'threshold','threshold':7},
                    {'exposure':0,'ordinary':0,'principal':100,'cash_rate':1,'rule':'exposure_threshold','threshold':20},
                    {'exposure':200,'ordinary':0,'principal':100,'cash_rate':0,'rule':'quarterly','threshold':10}]:
        result=run_comparison(data,request);same=True;maximum=min(request['exposure'],200-request['exposure'])
        for item,ordinary in zip(result['comparisons'],[0.,maximum,maximum/2]):
            p=item['result']['params'];w=p['weights'];m=item['result']['metrics']
            same &= p['ordinary']==ordinary and all(p[k]==request[k] for k in ['exposure','principal','cash_rate','rule','threshold'])
            same &= abs(2*w['00631L']+w['0050']-request['exposure']/100)<1e-12
            same &= all(r['actual_internal_exposure'] is None for r in item['result']['daily'])
            same &= all(r['period_cagr'] is None for r in item['result']['annual'] if r['partial_year'])
            if request['exposure']==0:same &= m['sharpe'] is None and m['sortino'] is None
        check('CALC-COMPARISON '+str(request['exposure']),same,
              {'request':request,'ordinary_presets':[i['result']['params']['ordinary'] for i in result['comparisons']],
               'nominal_only':True,'partial_year_cagr_null':True})
    parsed=Scripts();html=(BASE/'dist/index.html').read_text();parsed.feed(html)
    packed=json.loads(parsed.scripts['bundle']);bundle=json.loads(gzip.decompress(base64.b64decode(packed['gzip'])))
    modules=['backtest.py','portfolio_backtest.py','analyze_portfolios.py','web_calculator.py']
    check('CALC-BUNDLE-SOURCES',set(bundle['modules'])==set(modules) and all(bundle['modules'][f]==(BASE/f).read_text() for f in modules),
          {f:hashlib.sha256(bundle['modules'][f].encode()).hexdigest() for f in modules})
    check('CALC-BUNDLE-MARKET-DATA',bundle['data']==data,{'normalized_file_sha256':digest(BASE/'normalized.json'),'market_sessions':len(bundle['data']['calendar'])})
    worker=(BASE/'web/calculator_worker.js').read_text();app=(BASE/'web/calculator_app.js').read_text();template=(BASE/'web/calculator_template.html').read_text()
    check('CALC-BUNDLE-JAVASCRIPT',parsed.scripts['workerSource']==worker and app in parsed.scripts.values(),
          {'worker_sha256':digest(BASE/'web/calculator_worker.js'),'app_sha256':digest(BASE/'web/calculator_app.js')})
    seed=copy.deepcopy(bundle['seed']);seed['selected']=seed['selected'] or seed['comparisons'][0]['result']
    fresh=run_comparison(data,{'exposure':160,'ordinary':0,'principal':100,'cash_rate':1,'rule':'quarterly','threshold':10})
    correct=seed['selected']==fresh['selected']
    for given,expected in zip(seed['comparisons'],fresh['comparisons']):
        account=given['result'];correct &= account['metrics']==expected['result']['metrics'] and account['params']==expected['result']['params']
        correct &= all(all(row[k]==ref[k] for k in row) for row,ref in zip(account['daily'],expected['result']['daily']))
    check('CALC-BUNDLED-SEED',correct,{'selected':'A at 160%, initial 100 wan, cash 1%, quarterly','selected_full_daily_rows':len(seed['selected']['daily']),
                                    'selected_has_full_config_trades':all(k in seed['selected'] for k in ['config','trades','stages']),
                                    'seed_metrics_match_fresh_python':correct})
    no_credential_fields=[]
    def scan(obj,path=''):
        if isinstance(obj,dict):
            for key,value in obj.items():
                if re.search(r'(^|_)(api_key|api_token|access_token|password|secret|authorization)($|_)',key,re.I):no_credential_fields.append(path+'.'+key)
                scan(value,path+'.'+key)
        elif isinstance(obj,list):
            for i,value in enumerate(obj):scan(value,path+'['+str(i)+']')
    scan(bundle['data'])
    credential_access=any(term in '\n'.join(bundle['modules'].values()) for term in ['FINMIND_API_TOKEN','Bearer ','dotenv','os.environ'])
    check('CALC-NO-CREDENTIAL-BUNDLE',not no_credential_fields and not credential_access,
          {'credential_named_data_fields':no_credential_fields,'runtime_credential_access_in_modules':credential_access,
           'note':'No credential values were accessed for this review; this is a source/schema check.'})
    check('CALC-PARAMETER-HANDOFF',"runtime.globals.set('_request_json', JSON.stringify(message.params))" in worker
          and 'json.loads(_request_json)' in worker and 'eval(' not in worker and '${message.params' not in worker,
          {'mechanism':'JSON string bound to Python variable; static Python expression decodes JSON; request data is not inserted as executable source'})
    check('CALC-PINNED-RUNTIME',"https://cdn.jsdelivr.net/pyodide/v0.29.5/full/" in worker,{'runtime_version':'0.29.5','network_dependency':'Pyodide CDN; calculation and request inputs execute locally after loading'})
    check('CALC-RESULT-BINDING','const m=result.selected.metrics,p=result.params' in app
          and "message.type==='result'&&message.id===jobId" in app
          and 'result.selected.daily.map' in app and 'JSON.stringify(result.selected,null,2)' in app,
          {'context_and_downloads':'Bound to last successful result, not pending/current form fields','stale_jobs':'Only latest matching result id is rendered'})
    for f in ['web/calculator_worker.js','web/calculator_app.js']:
        completed=subprocess.run(['node','--check',str(BASE/f)],capture_output=True,text=True)
        check('CALC-JS-SYNTAX '+f,completed.returncode==0,{'exit_code':completed.returncode,'stderr':completed.stderr[:1000]})
    report=(BASE/'report_portfolios.py').read_text();builder=(BASE/'build_report.py').read_text()
    check('CALC-REPORT-CONCLUSIONS','不能將B視為在所有再平衡規則下都優於A' in report and '未將其冒稱為完全核實的穿透曝險' in report,
          {'B_not_universally_superior':True,'observed_weights_with_nominal_multipliers_only':True})
    check('CALC-PARTIAL-AND-WINDOW-LABELS','2014及2026為部分年度，未年化' in template and '回本可延伸到樣本末' in template
          and '不同固定市場期段' in report and '不按策略事後挑選最佳日期' in report,
          {'partial_years':'2014/2026 explicitly labeled','crisis':'Calendar-year drawdown, recovery extends through sample end','stages':'Common fixed calendar windows'})
    report_artifact=(BASE/'report.html').read_text();deployed_report=(BASE/'dist/report.html').read_text()
    expected_deployed=report_artifact.replace('href="dist/index.html"','href="index.html"')
    extension_matches={str(rate):build_extension(BASE,rate) in report_artifact for rate in [0.,.01,.02]}
    check('CALC-REPORT-ARTIFACT-CURRENT',all(extension_matches.values()) and expected_deployed==deployed_report,
          {'all_rate_portfolio_sections_match_sources':extension_matches,'dist_report_identical_except_relative_calculator_link':expected_deployed==deployed_report})
    # Replay the optional adjusted-diagnostic normalizer on copied snapshots.
    # This confirms the added diagnostics do not replace executable raw prices.
    with tempfile.TemporaryDirectory(prefix='calculator-review-',dir=BASE/'validation') as directory:
        temporary=Path(directory);(temporary/'raw').mkdir()
        for filename in ['download.jsonl','parvalue.jsonl','yuanta_0050_dividends.json']:
            shutil.copyfile(BASE/'raw'/filename,temporary/'raw'/filename)
        shutil.copyfile(BASE/'twse_spotcheck.json',temporary/'twse_spotcheck.json')
        normalizer=subprocess.run([sys.executable,'-B',str(BASE/'normalize.py'),'--root',str(temporary)],capture_output=True,text=True)
        normalized=json.loads((temporary/'normalized.json').read_text()) if normalizer.returncode==0 else {}
        quality=json.loads((temporary/'data_quality.json').read_text()) if normalizer.returncode==0 else {}
        core=['start','end','calendar','prices','splits','dividends']
        check('CALC-NORMALIZER-RAW-PRESERVATION',normalizer.returncode==0 and all(normalized.get(k)==data[k] for k in core)
              and 'adjusted_price_diagnostics' in quality,
              {'exit_code':normalizer.returncode,'core_market_and_action_fields_identical':all(normalized.get(k)==data[k] for k in core),
               'adjusted_available':{s:v['available'] for s,v in quality.get('adjusted_price_diagnostics',{}).items()},
               'provider_adjusted_equivalence_certified':False})
    # Documentation findings require actual final source wording, not promises.
    doc1="percentage(p.threshold)+'個百分點" not in app
    doc2=('相同目標曝險' in template and '所持ETF' in report and '應收' in report and ('任一' in report or '任何' in report)
          and bool(re.search(r'原(?:本|有)?正二',builder)) and '不可用' in report and '相對權重' in report)
    doc3='前一年末價格作初始高點' not in report and ('前一年末組合' in report or '前一年末資產淨值' in report)
    check('CALC-DOC-THRESHOLD-UNIT',doc1,{'finding_id':'RVW-CALC-001','expected':'number + percentage points, without an extra % sign'})
    check('CALC-DOC-MULTIASSET-RULE',doc2,{'finding_id':'RVW-CALC-002','expected':'Explicit multi-asset/cash-like trigger and common opening; legacy explanation scoped separately; target exposure label'})
    check('CALC-DOC-CRISIS-ANCHOR',doc3,{'finding_id':'RVW-CALC-003','expected':'Prior-year closing portfolio NAV, not ETF price'})
    reviewed=['web_calculator.py','test_web_calculator.py','build_calculator.py','web/calculator_app.js','web/calculator_worker.js',
              'web/calculator_template.html','report_portfolios.py','build_report.py','normalize.py','analyze_portfolios.py',
              'dist/index.html','dist/report.html','normalized.json']
    findings=[]
    for number,title,okay in [('001','Threshold label used both percent sign and percentage-point unit',doc1),
                             ('002','Legacy threshold explanation needed scope and multi-asset method clarification',doc2),
                             ('003','Crisis anchor description referred to price instead of portfolio NAV',doc3)]:
        findings.append({'id':'RVW-CALC-'+number,'severity':'Minor','status':'RESOLVED' if okay else 'OPEN','result':'PASS' if okay else 'FAIL','title':title})
    result={'status':'PASS' if all(c['status']=='PASS' for c in CHECKS) else 'FAIL','review_level':'L3',
            'reviewed_at_utc':datetime.now(timezone.utc).isoformat(),'check_count':len(CHECKS),'checks':CHECKS,'findings':findings,
            'scope':'Offline Python adapter, mathematical allocation bounds, real-data parity, embedded source/data/seed integrity, JS syntax/source flow, credential-access/schema scan, and financial report wording.',
            'excluded':'No browser interaction, CDN availability test, runtime visual QA, network traffic inspection, remote publication or GitHub-secret access.',
            'limitations':['Pyodide remains an external pinned CDN dependency; the model source and market inputs are bundled, not the full runtime.',
                           'Source-flow review does not replace actual browser behavior verification, which is performed separately by the parent task.',
                           'Nominal exposure still uses fixed 2/1 multipliers; internal derivatives exposure and strict point-in-time data vintage remain unverified.'],
            'reviewed_file_sha256':{f:digest(BASE/f) for f in reviewed}}
    (BASE/'validation/calculator_source_review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'checks':len(CHECKS),'failed':[c['id'] for c in CHECKS if c['status']=='FAIL']}))
    if result['status']!='PASS':raise SystemExit(1)

if __name__=='__main__':main()
