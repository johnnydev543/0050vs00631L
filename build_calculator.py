"""Bundle a static, self-contained calculator that runs the same Python code."""
from __future__ import annotations
import base64
import gzip
import io
import json
from pathlib import Path
from web_calculator import run_comparison

ROOT = Path(__file__).resolve().parent


def main() -> None:
    data = json.loads((ROOT/'normalized.json').read_text())
    modules = {name: (ROOT/name).read_text() for name in
               ['backtest.py', 'portfolio_backtest.py', 'analyze_portfolios.py', 'web_calculator.py']}
    seed = run_comparison(data, {'exposure':160,'ordinary':0,'principal':100,
                                 'cash_rate':1,'rule':'quarterly','threshold':10})
    # Keep the selected full account, and only plotted fields for reference paths.
    # The browser reconstructs the selected reference without duplicate storage.
    seed['selected'] = None
    for item in seed['comparisons'][1:]:
        account = item['result']
        account['daily'] = [{k: row[k] for k in ['date','nav','nominal_market_exposure']}
                            for row in account['daily']]
        for key in ['trades','config','stages']:
            account.pop(key, None)
    payload = json.dumps({'data':data,'modules':modules,'seed':seed},ensure_ascii=False,
                         separators=(',',':'),allow_nan=False).encode('utf-8')
    compressed = io.BytesIO()
    with gzip.GzipFile(filename='',fileobj=compressed,mode='wb',mtime=0) as handle:
        handle.write(payload)
    bundle = json.dumps({'gzip':base64.b64encode(compressed.getvalue()).decode()})
    template = (ROOT/'web/calculator_template.html').read_text()
    html = template.replace('@@BUNDLE@@',bundle).replace('@@WORKER@@',(ROOT/'web/calculator_worker.js').read_text())
    html = html.replace('@@APP@@',(ROOT/'web/calculator_app.js').read_text())
    assert '@@' not in html
    (ROOT/'dist').mkdir(exist_ok=True)
    (ROOT/'dist/index.html').write_text(html)
    (ROOT/'index.html').write_text('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=dist/index.html"><title>市場曝險回測計算機</title><a href="dist/index.html">開啟市場曝險回測計算機</a></html>')
    print(ROOT/'dist/index.html')


if __name__ == '__main__':
    main()
