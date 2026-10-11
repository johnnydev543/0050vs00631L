"""Check the actual bundled daily inputs and chart geometry against saved accounts."""
from __future__ import annotations
import base64
import gzip
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from web_calculator import run_comparison

def main():
    html=(BASE/'dist/index.html').read_text()
    packed=json.loads(re.search(r'<script id="bundle" type="application/json">(.*?)</script>',html,re.S).group(1))
    bundle=json.loads(gzip.decompress(base64.b64decode(packed['gzip'])))
    data=json.loads((BASE/'normalized.json').read_text())
    assert bundle['data']==data
    seeds=bundle['seed']['comparisons'];checks=[]
    for item in seeds:
        rows=item['result']['daily'];label=item['id']
        metrics=json.loads((BASE/f'portfolio_results/cash_0p010000/{label}_quarterly/metrics.json').read_text())
        import csv,io
        csv_rows=list(csv.DictReader(io.StringIO(gzip.decompress((BASE/f'portfolio_results/cash_0p010000/{label}_quarterly/daily.csv.gz').read_bytes()).decode())))
        assert len(rows)==len(csv_rows)==len(data['calendar'])
        errors=[]
        for row,ref in zip(rows,csv_rows):
            assert row['date']==ref['date']
            errors.append(abs(row['nav']-float(ref['nav'])))
        assert max(errors)<.00001
        assert abs(rows[-1]['nav']-metrics['ending_nav'])<.00001
        checks.append({'series':label,'daily_rows':len(rows),'max_nav_difference_twd':max(errors),'ending_nav_twd':rows[-1]['nav']})
    requests=[{'exposure':160,'ordinary':0}, {'exposure':137.5,'ordinary':11.2,'principal':234.5,'cash_rate':1.5,'rule':'threshold','threshold':7.5},
              {'exposure':120,'ordinary':25}, {'exposure':0,'ordinary':0}, {'exposure':200,'ordinary':0}]
    cases=[]
    for request in requests:
        result=run_comparison(data,request)
        list_=result['comparisons']+[{'id':'D','result':result['selected']}]
        cases.append({'principal':result['params']['principal'],'exposure':result['params']['exposure'],
                      'list':[{'id':x['id'],'result':{'daily':[{k:r[k] for k in ['date','nav','nominal_market_exposure']} for r in x['result']['daily']]}} for x in list_]})
    with tempfile.TemporaryDirectory() as directory:
        p=Path(directory)/'chart_cases.json';p.write_text(json.dumps(cases))
        completed=subprocess.run(['node',str(BASE/'validation/test_chart_model.js'),str(p)],capture_output=True,text=True)
        if completed.returncode:
            print(completed.stderr);raise SystemExit(completed.returncode)
    geometry=json.loads(completed.stdout)
    result={'status':'PASS','checked_at_utc':datetime.now(timezone.utc).isoformat(),'bundle_vs_saved_accounts':checks,'geometry':geometry,
            'scope':'Real daily portfolio NAV including cash and receivables, exact calendar dates and nominal exposure, linear/log geometry; no change to financial engine.',
            'display_changes':['D is always a separate dashed series, even when its allocation matches A/B/C.','Linear and logarithmic NAV scales use the same amounts, not cumulative percentage returns.','Initial principal is a reference, not an invented pre-cost trading observation.']}
    (BASE/'validation/chart_audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
