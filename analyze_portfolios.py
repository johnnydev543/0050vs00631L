"""Calendar-year, predeclared-stage and crisis-event analyses; no trading signals."""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from datetime import date
from pathlib import Path

from backtest import DAYS_PER_YEAR, drawdown_statistics, _write_csv, _write_json
from portfolio_backtest import read_daily, write_gzip_csv

STAGES = [
    ("2014–2019", "2014-10-31", "2019-12-31"),
    ("2020 疫情年", "2020-01-01", "2020-12-31"),
    ("2021", "2021-01-01", "2021-12-31"),
    ("2022 熊市年", "2022-01-01", "2022-12-31"),
    ("2023 至樣本末", "2023-01-01", "2026-10-08"),
]


def numeric_daily(rows: list[dict], symbol: str | None = None) -> list[dict]:
    result = []
    for source in rows:
        row = dict(source)
        for key in ["nav", "return", "rf_return", "excess_return", "drawdown", "cash", "receivables",
                    "holdings_value", "weight_00631L", "weight_0050", "weight_cash", "weight_receivables",
                    "nominal_market_exposure"]:
            if key in row:
                row[key] = float(row[key])
        if symbol:
            holding_weight = row["holdings_value"]/row["nav"]
            row.update(weight_00631L=holding_weight if symbol == "00631L" else 0.,
                       weight_0050=holding_weight if symbol == "0050" else 0.,
                       weight_cash=row["cash"]/row["nav"],
                       weight_receivables=row["receivables"]/row["nav"],
                       nominal_market_exposure=(2 if symbol == "00631L" else 1)*holding_weight)
        result.append(row)
    return result


def window_stats(rows: list[dict], start: str, end: str, initial: float = 1e6) -> dict:
    selected = [i for i, row in enumerate(rows) if start <= row["date"] <= end]
    if not selected:
        return {"included": False}
    first, last = selected[0], selected[-1]
    anchor = rows[first-1]["nav"] if first else initial
    anchor_date = rows[first-1]["date"] if first else rows[0]["date"]
    section = rows[first:last+1]
    navs = [row["nav"] for row in section]
    excess = [row["excess_return"] for row in section]
    stdev = statistics.stdev(excess) if len(excess) > 1 else 0.
    downside = math.sqrt(statistics.mean(min(x, 0.)**2 for x in excess))
    if max(abs(x) for x in excess) < 1e-12:
        stdev = downside = 0.
    mean = statistics.mean(excess)
    elapsed = (date.fromisoformat(section[-1]["date"])-date.fromisoformat(anchor_date)).days
    if first == 0:
        elapsed += 4.5/24
    cagr = (navs[-1]/anchor)**(DAYS_PER_YEAR/elapsed)-1 if elapsed > 0 else None
    dd = drawdown_statistics([row["date"] for row in section], navs, anchor)
    return {"included": True, "start": section[0]["date"], "end": section[-1]["date"],
            "anchor_date": anchor_date, "anchor_nav": anchor, "ending_nav": navs[-1],
            "sessions": len(section), "period_return": navs[-1]/anchor-1,
            "period_cagr": cagr, "max_drawdown": dd["max_drawdown"],
            "sharpe": mean/stdev*math.sqrt(252) if stdev else None,
            "sortino": mean/downside*math.sqrt(252) if downside else None,
            "mean_nominal_exposure": statistics.mean(row["nominal_market_exposure"] for row in section)}


def crisis_event(rows: list[dict], year: int, initial: float = 1e6) -> dict:
    """Worst peak-to-trough within fixed calendar year; recover through sample end.

    The prior year-end close anchors the running high; this does not cherry-pick
    a different pandemic/bear-market trading period for each strategy.
    """
    indices = [i for i, row in enumerate(rows) if row["date"].startswith(str(year)+"-")]
    if not indices:
        return {"year": year, "included": False}
    first = indices[0]
    peak_nav = rows[first-1]["nav"] if first else initial
    peak_index = first-1 if first else -1
    peak_date = rows[first-1]["date"] if first else rows[0]["date"]
    worst = None
    for index in indices:
        row = rows[index]
        if row["nav"] >= peak_nav-peak_nav*1e-12:
            peak_nav, peak_date, peak_index = row["nav"], row["date"], index
        depth = row["nav"]/peak_nav-1
        if worst is None or depth < worst["max_drawdown"]:
            worst = {"year": year, "included": True, "max_drawdown": depth,
                     "peak_date": peak_date, "peak_nav": peak_nav, "peak_index": peak_index,
                     "trough_date": row["date"], "trough_nav": row["nav"], "trough_index": index}
    trough = worst["trough_index"]
    recovery_index = next((i for i in range(trough, len(rows))
                           if rows[i]["nav"] >= worst["peak_nav"]-worst["peak_nav"]*1e-12), None)
    recovery_date = rows[recovery_index]["date"] if recovery_index is not None else None
    end_date = recovery_date or rows[-1]["date"]
    worst.update(recovery_date=recovery_date, censored=recovery_index is None,
                 peak_to_recovery_days=(date.fromisoformat(end_date)-date.fromisoformat(worst["peak_date"])).days,
                 trough_to_recovery_days=(date.fromisoformat(end_date)-date.fromisoformat(worst["trough_date"])).days,
                 peak_to_recovery_sessions=(recovery_index if recovery_index is not None else len(rows)-1)-worst["peak_index"])
    worst.pop("peak_index"); worst.pop("trough_index")
    return worst


def collect(root: Path) -> list[dict]:
    series = []
    for kind, directory in [("legacy", "results"), ("portfolio", "portfolio_results")]:
        for metrics in json.loads((root/directory/"summary.json").read_text()):
            rate = metrics["cash_yield_assumption"]
            scenario = f"cash_{rate:.6f}".replace(".", "p")
            path = root/directory/scenario/metrics["strategy"]
            symbol = ("0050" if metrics["strategy"] == "0050_buyhold" else "00631L") if kind == "legacy" else None
            rows = numeric_daily(read_daily(path/("daily.csv" if kind == "legacy" else "daily.csv.gz")), symbol)
            target = (1 if symbol == "0050" else int(metrics["strategy"].split('_')[1])/100*2) if kind == "legacy" else metrics["target_exposure"]
            series.append({"series_id": kind+":"+metrics["strategy"], "kind": kind,
                           "strategy": metrics["strategy"], "cash_yield_assumption": rate,
                           "metrics": metrics, "daily": rows, "target_exposure": target})
    return series


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    root = args.root
    output = root/"analysis"
    output.mkdir(exist_ok=True)
    annual, stages, crises, exposure, daily_exposure = [], [], [], [], []
    controls = []
    series = collect(root)
    lookup = {(s["kind"], s["strategy"], s["cash_yield_assumption"]): s for s in series}
    for item in series:
        rows = item["daily"]
        common = {k: item[k] for k in ["series_id", "kind", "strategy", "cash_yield_assumption"]}
        for year in range(2014, 2027):
            stat = window_stats(rows, f"{year}-01-01", f"{year}-12-31")
            if year in (2014, 2026):
                stat["period_cagr"] = None
            annual.append({**common, "year": year, "partial_year": year in (2014, 2026), **stat})
        for label, start, end in STAGES:
            stages.append({**common, "stage": label, **window_stats(rows, start, end)})
        for year in [2020, 2022]:
            crises.append({**common, **crisis_event(rows, year)})
        amounts = [row["nominal_market_exposure"] for row in rows]
        exposure.append({**common, "target_nominal_exposure": item["target_exposure"],
                         "mean_nominal_exposure": statistics.mean(amounts), "median_nominal_exposure": statistics.median(amounts),
                         "min_nominal_exposure": min(amounts), "max_nominal_exposure": max(amounts),
                         "exposure_stdev": statistics.stdev(amounts),
                         "sessions_above_target": sum(x > item["target_exposure"]+1e-12 for x in amounts),
                         "sessions": len(rows), "actual_internal_exposure_available": False})
        for row in rows:
            daily_exposure.append({**common, **{k: row[k] for k in ["date", "nav", "weight_00631L", "weight_0050", "weight_cash", "weight_receivables", "nominal_market_exposure"]},
                                   "actual_internal_exposure": None})
        if item["kind"] == "portfolio" and item["strategy"].startswith("A_"):
            legacy = lookup["legacy", "00631L_80_"+item["strategy"].split('_', 1)[1], item["cash_yield_assumption"]]
            difference = max(abs(a["nav"]-b["nav"]) for a, b in zip(rows, legacy["daily"]))
            controls.append({"portfolio": item["strategy"], "cash_yield_assumption": item["cash_yield_assumption"],
                             "max_nav_difference_twd": difference, "status": "PASS" if difference < 1e-5 else "FAIL"})
    if any(x["status"] != "PASS" for x in controls):
        raise AssertionError("Portfolio A no longer matches the original 80% strategy.")
    for name, records in [("annual_performance", annual), ("market_stages", stages),
                          ("crisis_recovery", crises), ("exposure_summary", exposure)]:
        _write_csv(output/(name+".csv"), records, [])
        _write_json(output/(name+".json"), records)
    write_gzip_csv(output/"daily_nominal_exposure.csv.gz", daily_exposure)
    _write_json(output/"legacy_A_regression.json", {"status": "PASS", "checks": controls})
    _write_json(output/"analysis_config.json", {"stages": STAGES, "crisis_years": [2020, 2022],
            "crisis_definition": "Worst drawdown inside fixed calendar year, anchored at preceding year-end close; recovery searched through full sample.",
            "annual_return": "Year-end NAV divided by prior-year closing NAV; first year uses initial capital. 2014 and 2026 are partial, not annualized.",
            "nominal_exposure": "2*actual 00631L market-value weight + actual 0050 market-value weight; cash and dividend receivables have zero nominal exposure.",
            "internal_fund_exposure": "Unavailable; cannot certify actual daily futures leverage/beta.",
            "main_threshold": "Any asset/cash-like weight differs by 10 percentage points.",
            "matched_exposure_sensitivity": "Nominal exposure differs by 20 percentage points; A is the same as its main weight-threshold strategy.",
            "distinct_configuration_scenarios": 42, "rows_including_six_A_controls": len(series)})
    print(f"Analyzed {len(series)} series, including six legacy-A controls.")


if __name__ == '__main__':
    main()
