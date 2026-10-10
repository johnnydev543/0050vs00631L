"""Public HTML sections for fixed 160% portfolios and declared sensitivities."""
from __future__ import annotations
import html
import json
from datetime import date
from pathlib import Path
from portfolio_backtest import read_daily

NAMES = {'A': 'A 正二80%＋現金20%', 'B': 'B 正二60%＋0050 40%',
         'C': 'C 正二70%＋0050 20%＋現金10%'}
RULES = {'quarterly':'季度', 'threshold':'權重門檻', 'exposure_threshold':'共同曝險門檻'}
COLORS = {'A':'var(--series-5)','B':'var(--series-3)','C':'var(--series-1)'}

def esc(value) -> str: return html.escape(str(value))
def pct(value) -> str: return '—' if value is None else f'{value*100:.2f}%'
def ratio(value) -> str: return '—' if value is None else f'{value:.3f}'
def money(value) -> str: return f'{value/10000:,.2f}'
def name(metric) -> str: return NAMES[metric['portfolio']]+'／'+RULES[metric['rule']]


def curve(root: Path, metrics: list[dict], rate: float, exposure: bool=False) -> str:
    width,height,left,right,top,bottom = 1120,355,82,18,24,45
    directory=f'cash_{rate:.6f}'.replace('.','p')
    rows=[read_daily(root/'portfolio_results'/directory/m['strategy']/'daily.csv.gz') for m in metrics]
    start=date.fromisoformat(rows[0][0]['date']).toordinal();end=date.fromisoformat(rows[0][-1]['date']).toordinal()
    key='nominal_market_exposure' if exposure else 'nav'
    values=[float(r[key]) if exposure else float(r[key])/1e6 for s in rows for r in s]
    minimum,maximal=(max(0,min(values)-.04),max(values)+.04) if exposure else (0,max(values)*1.06)
    def x(d):return left+(date.fromisoformat(d).toordinal()-start)/(end-start)*(width-left-right)
    def y(value):return top+(maximal-value)/(maximal-minimum)*(height-top-bottom)
    output=f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="同目標曝險配置的{"每日名目曝險" if exposure else "資產成長倍數"}"><g fill="var(--muted)" font-family="system-ui" font-size="13">'
    for i in range(5):
        value=minimum+(maximal-minimum)*i/4;label=f'{value*100:.0f}%' if exposure else f'{value:.1f}×'
        output+=f'<line x1="{left}" x2="{width-right}" y1="{y(value):.2f}" y2="{y(value):.2f}" stroke="var(--chart-grid)"/><text x="{left-12}" y="{y(value)+4:.2f}" text-anchor="end">{label}</text>'
    for year in [2015,2017,2019,2021,2023,2026]:
        output+=f'<text x="{x(str(year)+"-01-01"):.2f}" y="{height-12}" text-anchor="middle">{year}</text>'
    if exposure:
        output+=f'<line x1="{left}" x2="{width-right}" y1="{y(1.6):.2f}" y2="{y(1.6):.2f}" stroke="var(--muted)" stroke-dasharray="4 4"/>'
    for m,s in zip(metrics,rows):
        points=' '.join(f'{x(r["date"]):.2f},{y(float(r[key]) if exposure else float(r[key])/1e6):.2f}' for r in s)
        output+=f'<polyline points="{points}" fill="none" stroke="{COLORS[m["portfolio"]]}" stroke-width="1.6"/>'
    output+='</g></svg><p class="note">'+ '　'.join(f'<span style="color:{COLORS[m["portfolio"]]}">●</span> {esc(NAMES[m["portfolio"]])}' for m in metrics)+'</p>'
    return output


def performance_table(metrics: list[dict]) -> str:
    body=''
    for m in metrics:
        recovery=m['longest_recovery'] or {}
        body+=f'<tr><th>{esc(name(m))}</th><td>{money(m["initial_capital"])}</td><td>{money(m["ending_nav"])}</td><td>{pct(m["cagr"])}</td><td>{pct(m["max_drawdown"])}</td><td>{ratio(m["sharpe"])}</td><td>{ratio(m["sortino"])}</td><td>{recovery.get("duration_calendar_days",0):,}天{ "（未回本）" if recovery.get("censored") else ""}</td></tr>'
    return '<div class="tablewrap"><table><thead><tr><th>配置／再平衡</th><th>本金（萬）</th><th>期末（萬）</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>Sortino</th><th>最長回本</th></tr></thead><tbody>'+body+'</tbody></table></div>'


def period_matrix(records: list[dict], metrics: list[dict], labels: list, field: str) -> str:
    lookup={(r['strategy'],r[field]):r for r in records}
    body=''
    for label in labels:
        suffix='＊' if field=='year' and label in [2014,2026] else ''
        body+=f'<tr><th>{esc(label)}{suffix}</th>'
        for m in metrics:
            r=lookup[m['strategy'],label]
            body+=f'<td>{pct(r["period_return"])}／{pct(r["max_drawdown"])}</td>'
        body+='</tr>'
    headers=''.join(f'<th>{m["portfolio"]}／{RULES[m["rule"]]}</th>' for m in metrics)
    return '<div class="tablewrap"><table><thead><tr><th>'+('年度' if field=='year' else '固定期段')+'</th>'+headers+'</tr></thead><tbody>'+body+'</tbody></table></div>'


def build_extension(root: Path, rate: float) -> str:
    if not (root/'portfolio_results/summary.json').exists():return ''
    all_metrics=json.loads((root/'portfolio_results/summary.json').read_text())
    main=[m for m in all_metrics if m['cash_yield_assumption']==rate and m['rule']!='exposure_threshold']
    lookup={m['strategy']:m for m in all_metrics if m['cash_yield_assumption']==rate}
    pairs=[(lookup['A_'+r],lookup['B_'+r]) for r in ['quarterly','threshold']]
    comparisons=[]
    for a,b in pairs:
        relation='較高' if b['sharpe']>a['sharpe'] and b['sortino']>a['sortino'] else '未同時高於A'
        comparisons.append(f'{RULES[a["rule"]]}方式下，B的Sharpe／Sortino為{ratio(b["sharpe"])}／{ratio(b["sortino"])}，A為{ratio(a["sharpe"])}／{ratio(a["sortino"])}，B的兩項風險調整指標{relation}。')
    result='''<h2>相同160%目標曝險：三種配置</h2><p>沿用100萬元本金、相同資料與交易成本。A為原80%正二＋20%現金配置；B降低正二比例、加入0050；C同時保留0050與現金。目標曝險＝2×正二比例＋0050比例。</p><div class="tablewrap"><table><thead><tr><th>方案</th><th>00631L</th><th>0050</th><th>現金</th><th>目標曝險</th></tr></thead><tbody><tr><th>A 正二＋現金</th><td>80%</td><td>0%</td><td>20%</td><td>160%</td></tr><tr><th>B 正二＋0050</th><td>60%</td><td>40%</td><td>0%</td><td>160%</td></tr><tr><th>C 三資產混合</th><td>70%</td><td>20%</td><td>10%</td><td>160%</td></tr></tbody></table></div>'''
    result+=performance_table(main)
    result+='<div class="alert"><b>研究判斷：</b>'+esc(' '.join(comparisons))+'不能將B視為在所有再平衡規則下都優於A。這裡的A是80%／20%固定配置，沒有模擬完整動態調槓桿策略。</div>'
    result+='<h3>每日曝險與再平衡成本</h3><p>每日配置名目曝險＝2×當日正二市值比例＋當日0050市值比例；現金與應收股利的曝險為0。它使用實際帳本比例，但沒有取得基金內部逐日精確期貨槓桿；未將其冒稱為完全核實的穿透曝險。</p>'
    body=''
    for m in main:
        body+=f'<tr><th>{esc(name(m))}</th><td>160%</td><td>{pct(m["mean_nominal_exposure"])}</td><td>{pct(m["min_nominal_exposure"])}～{pct(m["max_nominal_exposure"])}</td><td>{m["rebalance_count"]}</td><td>{m["number_of_trades"]}</td><td>{money(m["total_trading_cost"])}</td></tr>'
    result+='<div class="tablewrap"><table><thead><tr><th>配置／規則</th><th>目標</th><th>每日平均曝險</th><th>最低～最高曝險</th><th>再平衡次數</th><th>交易筆數</th><th>累計成本（萬）</th></tr></thead><tbody>'+body+'</tbody></table></div>'
    result+='<p class="note">平均依市場交易日計算。再平衡次數按一次完整配置調整計；兩檔ETF可能產生兩筆交易，股利再投入另計交易筆數與成本。</p>'
    for rule in ['quarterly','threshold']:
        group=[lookup[p+'_'+rule] for p in ['A','B','C']]
        result+=f'<h3>{RULES[rule]}：資產成長倍數</h3>'+curve(root,group,rate)
        result+=f'<h3>{RULES[rule]}：每日名目曝險</h3>'+curve(root,group,rate,True)
    result+='<h3>年度績效</h3><p class="note">每格為當年累積報酬／當年最大回撤；2014、2026為部分年度，沒有年化。</p>'
    annual=[r for r in json.loads((root/'analysis/annual_performance.json').read_text()) if r['kind']=='portfolio' and r['cash_yield_assumption']==rate]
    result+=period_matrix(annual,main,list(range(2014,2027)),'year')
    result+='<h3>不同固定市場期段</h3><p class="note">以事前固定的曆年期段比較；每格為期間累積報酬／期間最大回撤，不按策略事後挑選最佳日期。</p>'
    stages=[r for r in json.loads((root/'analysis/market_stages.json').read_text()) if r['kind']=='portfolio' and r['cash_yield_assumption']==rate]
    labels=list(dict.fromkeys(r['stage'] for r in stages))
    result+=period_matrix(stages,main,labels,'stage')
    crises=[r for r in json.loads((root/'analysis/crisis_recovery.json').read_text()) if r['kind']=='portfolio' and r['cash_yield_assumption']==rate]
    crisis_lookup={(r['strategy'],r['year']):r for r in crises}
    for year in [2020,2022]:
        result+=f'<h3>{year}{"疫情" if year==2020 else "熊市"}：回撤與恢復</h3>'
        body=''
        for m in main:
            r=crisis_lookup[m['strategy'],year]
            body+=f'<tr><th>{esc(name(m))}</th><td>{pct(r["max_drawdown"])}</td><td>{r["peak_date"]}</td><td>{r["trough_date"]}</td><td>{r["recovery_date"] or "尚未回本"}</td><td>{r["peak_to_recovery_days"]}天{ "+" if r["censored"] else ""}</td><td>{r["trough_to_recovery_days"]}天{ "+" if r["censored"] else ""}</td></tr>'
        result+='<div class="tablewrap"><table><thead><tr><th>配置／規則</th><th>最大回撤</th><th>高點</th><th>谷底</th><th>回到高點</th><th>高點→回本</th><th>谷底→回本</th></tr></thead><tbody>'+body+'</tbody></table></div>'
    result+='<p class="note">上述事件採各曆年內的最大回撤，前一年末組合資產淨值作初始高點；回本搜尋延伸至整段樣本末。這不是各策略自行挑選疫情或熊市的起訖。</p>'
    supplementary=[lookup['A_threshold'],lookup['B_exposure_threshold'],lookup['C_exposure_threshold']]
    result+='<h3>補充：共同曝險門檻，檢查規則差異</h3><p>主比較保留相同權重偏離10個百分點：A／B／C任一股票權重或現金類比例（現金＋應收股利）距目標達10個百分點，下一個該配置所持ETF皆可交易的共同開盤再平衡（A只需正二）。0050配息於除息日記應收，發放當日資金鎖定，之後首個可交易開盤再投入0050；完整再平衡已吸收可用股利時不重複投入。應收股利不可用來支付買進；應收及當日鎖定股利超過目標現金時，下調股票配置總額並保留相對權重。簡化且無應收股利時，A約對應140%～180%曝險，B約對應150%～170%，因此相同權重門檻並不是相同曝險範圍。補充情境統一為名目曝險距160%達20個百分點，次可交易日再平衡；A等同原權重門檻結果。跳空、停牌與應收股利仍可能使實際配置超出範圍。</p>'
    result+=performance_table(supplementary)
    result+='<p>較少正二與較多現金／0050會同時改變基金內部成本、股利、持倉漂移和再平衡資金來源。這是整體配置比較，不能將報酬差單獨歸因於現金比例；基金內部費用已反映在實價，沒有再次扣除。</p>'
    result+='<p><a href="analysis/daily_nominal_exposure.csv.gz">每日曝險CSV（gzip）</a> · <a href="analysis/annual_performance.csv">年度績效CSV</a> · <a href="analysis/market_stages.csv">固定期段CSV</a> · <a href="analysis/crisis_recovery.csv">危機與回本CSV</a> · <a href="portfolio_results/summary.csv">同曝險配置彙總</a> · <a href="portfolio_review.json">新增配置覆核</a></p>'
    return result
