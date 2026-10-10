# -*- coding: utf-8 -*-
"""Build an offline Traditional Chinese HTML report from verified results."""
from __future__ import annotations
import csv, html, json, math
from datetime import date
from pathlib import Path
from report_portfolios import build_extension

ROOT=Path(__file__).resolve().parent
LABELS={'0050_buyhold':'0050 買進持有', '00631L_100_buyhold':'00631L 100% 持有'}
for w in (50,60,80):
 for rule,name in [('quarterly','季度'),('threshold','偏離 10 個百分點')]:
  LABELS[f'00631L_{w}_{rule}']=f'00631L {w}%・{name}'
COLORS=[f'var(--series-{i})' for i in range(8)]

def esc(x: object) -> str:return html.escape(str(x))
def pct(x: float) -> str:return f'{x*100:.2f}%'
def num(x: float|None) -> str:return '—' if x is None else f'{x:.2f}'
def money(x: float) -> str:return f'{x/10000:,.2f}'
def scenario(rate: float) -> str:return f'cash_{rate:.6f}'.replace('.','p').replace('-','m')

def chart(rows: list[dict], rate: float, drawdown: bool=False) -> str:
 width,height=1120,410;left,right,top,bottom=80,20,24,55
 paths=[];series=[];start=date(2014,10,31).toordinal();end=date(2026,10,8).toordinal()
 for m in rows:
  with (ROOT/'results'/scenario(rate)/m['strategy']/'daily.csv').open() as f:series.append(list(csv.DictReader(f)))
 ymax=math.log10(60);ymin=math.log10(.5)
 def xy(r:dict)->tuple[float,float]:
  x=left+(date.fromisoformat(r['date']).toordinal()-start)/(end-start)*(width-left-right)
  if drawdown:y=top+(-float(r['drawdown']))/.7*(height-top-bottom)
  else:y=top+(ymax-math.log10(float(r['nav'])/1e6))/(ymax-ymin)*(height-top-bottom)
  return x,y
 ticks=[0,-.2,-.4,-.6] if drawdown else [.5,1,2,5,10,20,50]
 for v in ticks:
  y=top+(-v)/.7*(height-top-bottom) if drawdown else top+(ymax-math.log10(v))/(ymax-ymin)*(height-top-bottom)
  label=f'{v*100:.0f}%' if drawdown else f'{v:g}×'
  paths.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="var(--chart-grid)"/><text x="{left-12}" y="{y+4:.1f}" text-anchor="end">{label}</text>')
 for yr in range(2015,2027,2):
  x=left+(date(yr,1,1).toordinal()-start)/(end-start)*(width-left-right)
  paths.append(f'<text x="{x:.1f}" y="{height-20}" text-anchor="middle">{yr}</text>')
 for i,s in enumerate(series):
  points=' '.join(f'{x:.2f},{y:.2f}' for x,y in map(xy,s))
  paths.append(f'<polyline points="{points}" fill="none" stroke="{COLORS[i]}" stroke-width="1.5" opacity=".88"><title>{esc(LABELS[rows[i]["strategy"]])}</title></polyline>')
 return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{"資產回撤" if drawdown else "資產成長倍數，對數刻度"}"><g font-family="system-ui" font-size="14" fill="var(--muted)">'+''.join(paths)+'</g></svg>'

def table(rows:list[dict])->str:
 body=''
 for i,m in enumerate(rows):
  r=m['longest_recovery'] or {};duration=r.get('duration_calendar_days',0)
  status='（尚未回本）' if r.get('censored') else ''
  body+=f'<tr><th><span class="dot" style="background:{COLORS[i]}"></span>{esc(LABELS[m["strategy"]])}</th><td>{money(m["initial_capital"])}</td><td>{money(m["ending_nav"])}</td><td>{pct(m["cagr"])}</td><td>{pct(m["max_drawdown"])}</td><td>{num(m["sharpe"])}</td><td>{num(m["sortino"])}</td><td>{duration:,} 天{status}</td></tr>'
 return '<div class="tablewrap"><table><thead><tr><th>配置與再平衡方式</th><th>本金（萬元）</th><th>期末資產（萬元）</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>Sortino</th><th>最長回本期</th></tr></thead><tbody>'+body+'</tbody></table></div>'

def main()->None:
 metrics=json.loads((ROOT/'results/summary.json').read_text());quality=json.loads((ROOT/'data_quality.json').read_text())
 review=json.loads((ROOT/'review.json').read_text()) if (ROOT/'review.json').exists() else {'status':'PENDING'}
 repo=json.loads((ROOT/'repo_integrity.json').read_text()) if (ROOT/'repo_integrity.json').exists() else {'status':'PENDING'}
 mainrows=[x for x in metrics if x['cash_yield_assumption']==.01]
 assert len(metrics)==24 and len(mainrows)==8,'This report template requires the default 24-scenario experiment.'
 for m in metrics:
  cfg=json.loads((ROOT/'results'/scenario(m['cash_yield_assumption'])/m['strategy']/'config.json').read_text())
  assert cfg['costs']=={'commission':.001425,'sell_tax':.001,'slippage':.0005} and cfg['initial_capital']==1_000_000,'Update report assumptions for custom costs/capital.'
 panels=[]
 for rate in [0,.01,.02]:
  rows=[x for x in metrics if x['cash_yield_assumption']==rate]
  costs=''.join(f'<tr><th>{esc(LABELS[m["strategy"]])}</th><td>{m["number_of_trades"]}</td><td>{money(m["total_trading_cost"])}</td><td>{money(m["cash_interest_earned"])}</td><td>{money(m["ending_liquidation_nav"])}</td></tr>' for m in rows)
  recovery=''
  for m in rows:
   r=m['longest_recovery'] or {};current=m.get('ongoing_drawdown')
   recovery+=f'<tr><th>{esc(LABELS[m["strategy"]])}</th><td>{esc(r.get("peak_date","—"))}</td><td>{esc(r.get("trough_date","—"))}</td><td>{esc(r.get("recovery_date") or "尚未回本")}</td><td>{r.get("duration_sessions",0)}</td><td>{(str(current["duration_calendar_days"])+" 天") if current else "無"}</td></tr>'
  panels.append(f'''<section class="scenario" data-rate="{rate}" {'' if rate==.01 else 'hidden'}>
  {build_extension(ROOT,rate)}<h2>原有固定配置結果</h2>{table(rows)}<p class="note">期末資產為持倉按收盤市價估值，已扣期間交易成本；下表另列全部賣出後金額。回本期以先前資產高點至恢復高點的曆日數衡量。</p>
  <h2>資產路徑</h2><p>成長倍數以 100 萬元起始資產為基準；使用對數刻度，便於比較長期成長率。</p>{chart(rows,rate)}
  <h2>從歷史高點回撤</h2>{chart(rows,rate,True)}
  <h2>交易成本與現金收益</h2><div class="tablewrap"><table><thead><tr><th>策略</th><th>交易筆數</th><th>累計交易成本（萬）</th><th>累計現金利息（萬）</th><th>期末全數賣出（萬）</th></tr></thead><tbody>{costs}</tbody></table></div>
  <p class="note">成本為佣金、交易稅及滑價的實際帳本合計，並非與零成本情境的期末差額；「全數賣出」另扣期末一次賣出成本。</p>
  <h2>最長回本期間的起訖</h2><div class="tablewrap"><table><thead><tr><th>策略</th><th>起始高點</th><th>期間低點</th><th>回到高點</th><th>市場交易日</th><th>期末未回本期間</th></tr></thead><tbody>{recovery}</tbody></table></div>
  </section>''')
 baseline=mainrows[0];full=mainrows[-1]
 sources=[
  ('日價資料與交易日規格','https://finmind.github.io/tutor/TaiwanMarket/Technical/'),
  ('配息與分割資料規格','https://finmind.github.io/tutor/TaiwanMarket/Fundamental/'),
  ('元大 0050 官方股利歷史','https://www.yuantafunds.com/myfund/dividend/history?fundid=1066'),
  ('元大 00631L 官方基本資料：2014-10-31 上市、不配息','https://www.yuantaetfs.com/product/detail/00631L/Basic_information'),
  ('TWSE 0050 一拆四／停牌公告',quality['instruments']['0050']['verified_suspension_source']),
  ('TWSE 00631L 一拆22／停牌公告',quality['instruments']['00631L']['verified_suspension_source']),
  ('TWSE 2026 年開休市表','https://www.twse.com.tw/holidaySchedule/holidaySchedule?queryYear=115&response=html'),
  ('TWSE ETF 交易規則與賣出稅','https://www.twse.com.tw/zh/products/securities/etf/overview/rules.html'),
  ('TWSE 投資指南：手續費由券商訂定','https://www.twse.com.tw/zh/about/company/guide.html')]
 source_list=''.join(f'<li><a href="{esc(url)}">{esc(label)}</a></li>' for label,url in sources)
 document=f'''<!doctype html><html lang="zh-Hant" data-theme="dark"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>0050 與正二配置｜真實資料回測</title>
 <script>try{{const saved=localStorage.getItem('etf-report-theme');if(saved==='light'||saved==='dark')document.documentElement.dataset.theme=saved;}}catch(e){{}}</script>
 <style>
:root{{color-scheme:light;--page:#f3f5f8;--surface:#fff;--surface-alt:#f8fafc;--text:#172335;--muted:#657287;--eyebrow:#547186;--border:#e2e7ee;--table-head:#edf2f7;--table-head-text:#3f536d;--alert:#fff5df;--alert-accent:#d69727;--link:#096b9d;--chart-grid:#e5e7eb;--series-0:#111827;--series-1:#40739e;--series-2:#70a1c8;--series-3:#127d65;--series-4:#53b99d;--series-5:#ae6523;--series-6:#dfa256;--series-7:#be3455}}
:root[data-theme=dark]{{color-scheme:dark;--page:#0b111c;--surface:#121c2c;--surface-alt:#19263a;--text:#e5ecf7;--muted:#a7b6cc;--eyebrow:#9db5d0;--border:#30415b;--table-head:#1c2c43;--table-head-text:#c6d7ec;--alert:#33291b;--alert-accent:#eeb458;--link:#85ccff;--chart-grid:#2a3b53;--series-0:#e5ecf7;--series-1:#75ace0;--series-2:#abd3f5;--series-3:#57d5ae;--series-4:#a3e6cd;--series-5:#e4a25e;--series-6:#ffd19b;--series-7:#ff759b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--text);font:16px/1.7 system-ui,-apple-system,"PingFang TC",sans-serif}}main{{max-width:1240px;margin:36px auto;background:var(--surface);padding:44px 48px;border-radius:16px}}h1{{font-size:35px;line-height:1.3;margin:8px 0 16px}}h2{{font-size:23px;margin-top:40px}}p{{max-width:1000px}}.eyebrow{{letter-spacing:.13em;color:var(--eyebrow);font-size:12px;font-weight:700}}.meta,.note{{color:var(--muted);font-size:14px}}.alert{{padding:18px 22px;background:var(--alert);border-left:4px solid var(--alert-accent);border-radius:6px;margin:25px 0}}.lead{{font-size:18px}}.controls{{position:sticky;top:0;background:var(--surface-alt);padding:16px;margin-top:28px;border:1px solid var(--border);border-radius:8px;z-index:3;display:flex;align-items:center;flex-wrap:wrap;gap:12px}}.controls .note{{flex-basis:100%}}select,.theme-toggle{{font:inherit;color:var(--text);padding:5px 12px;border:1px solid var(--border);border-radius:6px;background:var(--surface)}}select{{margin-left:8px}}.theme-toggle{{margin-left:auto;cursor:pointer;white-space:nowrap}}.theme-toggle:hover{{background:var(--table-head)}}select:focus-visible,.theme-toggle:focus-visible,a:focus-visible{{outline:2px solid var(--link);outline-offset:3px}}.tablewrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px;margin:18px 0}}td,th{{border-bottom:1px solid var(--border);padding:12px 9px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}th:first-child{{text-align:left}}thead{{background:var(--table-head);color:var(--table-head-text)}}tbody th{{font-weight:500}}.dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px}}svg{{width:100%;height:auto}}a{{color:var(--link);text-underline-offset:3px}}pre{{background:#0c1726;color:#d3e5f0;padding:20px;border:1px solid var(--border);border-radius:8px;overflow:auto;font-size:13px}}code{{font-size:.9em}}li{{margin:8px 0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}.card{{border:1px solid var(--border);padding:20px;border-radius:9px}}.card h3{{margin:0 0 12px;font-size:18px}}footer{{border-top:1px solid var(--border);margin-top:40px;padding-top:16px;color:var(--muted);font-size:13px}}.capital-card{{display:flex;flex-direction:column;gap:3px;margin:24px 0;padding:20px 24px;background:var(--surface-alt);border:1px solid var(--border);border-radius:10px}}.capital-card span,.capital-card small{{color:var(--muted)}}.capital-card strong{{font-size:32px;line-height:1.4;font-variant-numeric:tabular-nums}}[hidden]{{display:none!important}}@media(max-width:750px){{main{{margin:0;padding:26px 18px;border-radius:0}}h1{{font-size:28px}}.grid{{grid-template-columns:1fr}}.controls label{{display:flex;align-items:center;flex-wrap:wrap;gap:8px}}select{{margin-left:0}}}}
@media print{{:root,:root[data-theme=dark]{{color-scheme:light;--page:#fff;--surface:#fff;--surface-alt:#f8fafc;--text:#172335;--muted:#657287;--eyebrow:#547186;--border:#e2e7ee;--table-head:#edf2f7;--table-head-text:#3f536d;--alert:#fff5df;--alert-accent:#d69727;--link:#096b9d;--chart-grid:#e5e7eb;--series-0:#111827;--series-1:#40739e;--series-2:#70a1c8;--series-3:#127d65;--series-4:#53b99d;--series-5:#ae6523;--series-6:#dfa256;--series-7:#be3455}}main{{margin:0;max-width:none;padding:0}}.controls{{position:static}}.theme-toggle{{display:none}}.scenario.capital-card{{display:flex;flex-direction:column;gap:3px;margin:24px 0;padding:20px 24px;background:var(--surface-alt);border:1px solid var(--border);border-radius:10px}}.capital-card span,.capital-card small{{color:var(--muted)}}.capital-card strong{{font-size:32px;line-height:1.4;font-variant-numeric:tabular-nums}}[hidden]{{display:none!important}}h2{{break-after:avoid}}table,svg{{break-inside:avoid}}}}
</style>
 <main><div class="eyebrow">TAIWAN ETF · REPRODUCIBLE RESEARCH</div><h1>0050 與正二＋現金配置</h1><p class="meta">實際期間 2014-10-31 開盤 → 2026-10-08 收盤｜資料查核 2026-10-10</p>
 <div class="capital-card"><span>投入本金</span><strong>NT${baseline['initial_capital']:,.0f}</strong><small>{money(baseline['initial_capital'])} 萬元 · 各策略本金相同，期間沒有額外投入</small></div>
 <p class="lead">保留原有五種固定配置與兩種再平衡方式，加入A／B／C三種160%目標市場曝險比較，並提供年度、危機回撤與每日曝險分析。所有策略由同一段真實ETF日價與公司行動建立持倉帳本，現金年收益提供0%、1%、2%假設情境。</p>
 <p><a href="dist/index.html">開啟互動回測：自行輸入目標曝險、本金與配置</a></p>

 <div class="controls"><label>現金年有效收益率假設<select id="cash"><option value="0">0%</option><option value="0.01" selected>1%（主要情境）</option><option value="0.02">2%</option></select></label><button type="button" id="themeToggle" class="theme-toggle" aria-label="切換為淺色主題" aria-pressed="true">☀ 淺色主題</button><div class="note">固定情境值，並非觀測到的歷史存款利率。其餘成本、規則與價格保持相同。</div></div>
 {''.join(panels)}
 <section class="rebalance-explanation"><h2>偏離10個百分點是什麼意思？</h2><p>在原本正二＋現金策略中，衡量的是<strong>正二占總資產的比例</strong>。當收盤比例與目標相差至少10個百分點，就在下一個可交易日開盤，將比例調回目標。</p><div class="tablewrap"><table><thead><tr><th>正二目標比例</th><th>觸發買入</th><th>觸發賣出</th></tr></thead><tbody><tr><th>50%</th><td>≤40%</td><td>≥60%</td></tr><tr><th>60%</th><td>≤50%</td><td>≥70%</td></tr><tr><th>80%</th><td>≤70%</td><td>≥90%</td></tr></tbody></table></div><p><strong>100萬元範例：</strong>一開始正二50萬元、現金50萬元。正二漲到75萬元時，總資產為125萬元，正二比例＝75÷125＝<strong>60%</strong>，與50%目標相差10個百分點，因此觸發賣出再平衡。若執行時價格不變，未扣成本前正二與現金會各約62.5萬元；實際金額會納入交易成本。</p><p class="note">50%變成60%是增加10個百分點。這個門檻觀察的是資產配置比例，兩次再平衡之間比例會隨市場漲跌與現金收益改變。</p></section>
 <h2>交易與計算口徑</h2><div class="grid"><div class="card"><h3>部位與時點</h3><ul><li>2014-10-31 開盤建倉。0050 為全額持有並將已發放股利再投入；00631L 為 50%、60%、80%、100%，其餘持現金。</li><li>季度：新季度第一個市場交易日開盤回到目標權重；ETF 不可交易則遞延。</li><li>偏離門檻：收盤權重距目標達 10 個百分點，於下一個可交易日開盤回到目標；50% 的觸發區間即 ≤40% 或 ≥60%。</li><li>目標權重按扣除當筆成本後的資產解算，不借貸；兩次再平衡間權重會漂移。</li></ul></div><div class="card"><h3>成本與現金</h3><ul><li>每側佣金 0.1425% 為模型假設，賣出交易稅 0.1%，每側滑價 0.05%。基金內部費用已反映在實際日價，未重複扣除。</li><li>現金按曆日、年有效利率每日複利。買賣資金採交易日記帳，未另建 T+2 交割凍結模型。</li><li>允許小數受益單位；未套券商最低費、折扣、整股／零股撮合差異，未扣個人股利所得稅、補充保費、利息稅。</li><li>成交按原始開盤價加減固定滑價，沒有逐筆成交佇列資料；不保證真實帳戶能以模擬價格成交。</li></ul></div></div>
 <p>股利於除息日認列應收、發放日轉現金，於發放日之後首個 ETF 可交易日開盤再投入，避免股息提前投資。起日前 2014-10-24 除息的 1.55 元不屬於本持倉，即使發放日在起日後也不計入。分割只改受益單位數與單位價格，不當作投資損益。停牌以最近實價估值並標示陳舊，禁止以補值成交。</p>
 <p>CAGR 使用 2014-10-31 09:00 至末日 13:30 的實際曆日／365.2425；最大回撤以含初始本金的累積高點計算。Sharpe 使用日超額報酬平均／樣本標準差 × √252；超額基準為同情境現金利率、依相鄰交易日曆日間隔換算。Sortino＝日超額報酬平均 ÷ √[所有觀測日中負超額報酬平方的平均] × √252；非負日貢獻 0，最低可接受報酬同現金利率。首日開盤至收盤損益及建倉成本均納入；首日無風險報酬設 0。最長回本時間與最大跌幅可能來自不同事件，期末未回本另外標示，不能當作已完成回本。</p>
 <p class="note">以上是固定配置的歷史模擬，00631L 的目標為單日正向二倍，並非整段期間報酬必為0050的二倍。價格反映該段歷史市場路徑；樣本沒有涵蓋00631L上市以前的危機，也未用來選出最優參數。</p>
 <p>一價日（當日最高價＝最低價）在成交模型中一律假設未成交，保留真實收盤價估值，待下一個可交易開盤重試。這是依日線資料事後判定成交可得性的近似，不是績效下界，也不代表知道真實委託排隊結果。兩檔均在 2025-04-07、2025-04-10 出現一價日。主情境 50% 門檻策略有一筆 2025-04-10 買單遞延至 04-11；相較假設 04-10 可成交，其期末資產增加 42,410.40 元、CAGR 增加約 0.044882 個百分點。其他主情境結果不變。完整成交敏感度見 <a href="validation/one_price_execution_sensitivity.json">驗證紀錄</a>。</p><h2>資料品質</h2><table><thead><tr><th>檢查</th><th>結果</th></tr></thead><tbody>
 <tr><th>要求截止日</th><td>2026-10-09 國慶補假休市；最後實際交易日 10-08</td></tr><tr><th>市場交易日</th><td>2,912</td></tr><tr><th>0050 原始日價／股利</th><td>2,907 筆／22 次；股利金額、除息日、發放日與元大一致</td></tr><tr><th>00631L 原始日價／股利</th><td>2,908 筆／查無配息；元大產品頁標示不配息</td></tr><tr><th>0050 分割停牌</th><td>2025-06-11～17，5個市場日；6/18 一拆四</td></tr><tr><th>00631L 分割停牌</th><td>2026-03-25～30，4個市場日；3/31 一拆22</td></tr><tr><th>重複日期／非法OHLC／不明缺日</th><td>均為 0</td></tr><tr><th>TWSE 獨立行情抽查</th><td>6個股票月份／48筆／192個OHLC欄位，全部一致</td></tr><tr><th>減資／面額變更</th><td>查無本期間兩檔相關事件</td></tr></tbody></table>
 <p class="note">以實際ETF日價、官方分割比例與配息紀錄重建歷史持倉。停牌日保留最近價格估值；行情未逐日全面向證交所複核，也未驗證每筆資料當年的發布版本。因此這是明列交易假設下的歷史模擬。</p>
 <h2>計算驗證與可重現資料</h2><p>14項計算測試與獨立重算已通過。24組情境各自保存逐日資產、交易紀錄、設定與完整指標。<a href="results/summary.csv">下載24組結果CSV</a> · <a href="results/summary.json">完整指標JSON</a> · <a href="review.json">計算覆核紀錄</a></p><p class="note">可重現程式與離線重跑方式見專案README及START_HERE.txt。上述數值為歷史模擬，交易成本、現金年收益及成交方式均採本文列出的假設。</p>
 <h2>來源與證據</h2><ul>{source_list}</ul><p><a href="data_quality.json">資料品質明細</a> · <a href="twse_spotcheck.json">TWSE 抽查</a> · <a href="results/summary.json">完整指標 JSON</a> · <a href="raw/yuanta_0050_dividends.json">元大股利原始回應</a></p>
 <footer>此報告所有績效數字由交付的 Python 程式計算；交易規則、現金利率與成交模型為明列假設。本金、配息與成本均納入持倉帳本。</footer></main>
 <script>
document.getElementById('cash').addEventListener('change',function(){{document.querySelectorAll('.scenario').forEach(x=>x.hidden=x.dataset.rate!==this.value);}});
const themeButton=document.getElementById('themeToggle');
function syncThemeButton(){{const dark=document.documentElement.dataset.theme==='dark';themeButton.textContent=dark?'☀ 淺色主題':'☾ 深色主題';themeButton.setAttribute('aria-label',dark?'切換為淺色主題':'切換為深色主題');themeButton.setAttribute('aria-pressed',String(dark));}}
themeButton.addEventListener('click',function(){{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;try{{localStorage.setItem('etf-report-theme',theme);}}catch(e){{}}syncThemeButton();}});
syncThemeButton();
</script></html>'''
 (ROOT/'report.html').write_text(document,encoding='utf-8')
 print(ROOT/'report.html')

if __name__=='__main__':main()
