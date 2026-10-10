"""Two-ETF portfolio accounting; unchanged legacy engine supplies risk metrics.

Exposure is nominal look-through at target multipliers 2 and 1, not an observed
series of the funds' internal derivative leverage. Fractional units and the
legacy transaction-cost/next-open conventions are retained.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from backtest import (Costs, DAYS_PER_YEAR, calculate_metrics, canonical_json,
                      validate_input, _write_csv, _write_json)

SYMBOLS = ("00631L", "0050")
MULTIPLIERS = {"00631L": 2.0, "0050": 1.0}
VERSION = "1.0.0"


@dataclass(frozen=True)
class Portfolio:
    label: str
    leveraged_weight: float
    ordinary_weight: float
    cash_weight: float
    rule: str
    weight_threshold: float = .10
    exposure_threshold: float = .20

    def __post_init__(self) -> None:
        weights = (self.leveraged_weight, self.ordinary_weight, self.cash_weight)
        if any(not math.isfinite(w) or w < 0 for w in weights) or abs(sum(weights)-1) > 1e-12:
            raise ValueError("Weights must be nonnegative and sum to one.")
        if self.rule not in {"quarterly", "threshold", "exposure_threshold"}:
            raise ValueError("Unsupported portfolio rule.")
        if not 0 < self.weight_threshold <= 1 or not 0 < self.exposure_threshold <= 2:
            raise ValueError("Invalid rebalancing threshold.")

    @property
    def weights(self) -> dict[str, float]:
        return {"00631L": self.leveraged_weight, "0050": self.ordinary_weight}

    @property
    def name(self) -> str:
        return f"{self.label}_{self.rule}"

    @property
    def target_exposure(self) -> float:
        return 2*self.leveraged_weight+self.ordinary_weight


def portfolios() -> list[Portfolio]:
    return [Portfolio(label, *weights, rule)
            for label, weights in [("A", (.8, 0., .2)), ("B", (.6, .4, 0.)),
                                   ("C", (.7, .2, .1))]
            for rule in ("quarterly", "threshold", "exposure_threshold")
            if not (label == "A" and rule == "exposure_threshold")]


def solve_targets(values: dict[str, float], cash: float, locked: float,
                  weights: dict[str, float], costs: Costs) -> tuple[dict[str, float], float]:
    """Solve post-cost targets together; locked = receivables + today's payments.

    If the desired stock allocation cannot be funded without locked assets,
    reduce total invested value while retaining the relative stock targets.
    This is explicit funding restraint, not margin borrowing against dividends.
    """
    if min([cash, locked, *values.values()]) < -1e-7 or sum(weights.values()) > 1+1e-12:
        raise ValueError("Invalid account or target.")
    nav = sum(values.values())+cash+locked
    stock_weight = sum(weights.values())
    buy_cost = (1+costs.slippage)*(1+costs.commission)-1
    sell_cost = 1-(1-costs.slippage)*(1-costs.commission-costs.sell_tax)

    def targets(post_nav: float) -> dict[str, float]:
        total = min(stock_weight*post_nav, max(0., post_nav-locked))
        return {s: total*weights.get(s, 0)/stock_weight if stock_weight else 0.
                for s in values}

    def residual(post_nav: float) -> float:
        x = targets(post_nav)
        charges = sum((x[s]-v)*buy_cost if x[s] >= v else (v-x[s])*sell_cost
                      for s, v in values.items())
        return post_nav+charges-nav

    lo, hi = 0., nav
    for _ in range(80):
        mid = (lo+hi)/2
        if residual(mid) > 0:
            hi = mid
        else:
            lo = mid
    post_nav = (lo+hi)/2
    return targets(post_nav), post_nav


def trade_value(units: dict[str, float], cash: float, symbol: str, delta: float,
                price: float, costs: Costs, day: str, reason: str,
                event: int) -> tuple[float, dict[str, Any] | None]:
    if abs(delta) < 1e-7:
        return cash, None
    side = "buy" if delta > 0 else "sell"
    quantity = abs(delta)/price
    execution = price*(1+costs.slippage if delta > 0 else 1-costs.slippage)
    gross = quantity*execution
    commission = gross*costs.commission
    tax = gross*costs.sell_tax if delta < 0 else 0.
    slip = quantity*abs(execution-price)
    cash += -(gross+commission) if delta > 0 else gross-commission-tax
    units[symbol] += quantity if delta > 0 else -quantity
    if cash < -1e-5 or units[symbol] < -1e-7:
        raise ArithmeticError("Negative cash or units after a trade.")
    cash = max(0., cash)
    units[symbol] = max(0., units[symbol])
    return cash, {"date": day, "symbol": symbol, "reason": reason,
                  "event_id": event, "side": side, "quantity": quantity,
                  "raw_open": price, "execution_price": execution,
                  "gross_amount": gross, "commission": commission,
                  "sell_tax": tax, "slippage_cost": slip,
                  "total_cost": commission+tax+slip, "cash_after": cash,
                  "units_after": units[symbol]}


def run_portfolio(data: dict, portfolio: Portfolio, cash_yield: float = .01,
                  initial: float = 1_000_000., costs: Costs | None = None) -> dict:
    validate_input(data)
    costs = costs or Costs()
    if initial <= 0 or not math.isfinite(initial) or cash_yield <= -1 or not math.isfinite(cash_yield):
        raise ValueError("Invalid initial capital or cash yield.")
    active = [s for s in SYMBOLS if portfolio.weights[s] > 0]
    calendar = data["calendar"]
    sessions = set(calendar)
    config = {"portfolio_engine_version": VERSION,
              "input_semantic_sha256": hashlib.sha256(canonical_json(data).encode()).hexdigest(),
              "portfolio": asdict(portfolio), "costs": asdict(costs),
              "initial_capital": initial, "cash_yield": cash_yield,
              "start": calendar[0], "end": calendar[-1],
              "threshold_rule": f"Any stock/cash-like close weight differs by >={portfolio.weight_threshold*100:g} percentage points; next jointly executable open.",
              "exposure_rule": f"Nominal exposure differs by >={portfolio.exposure_threshold*100:g} percentage points from target; next jointly executable open.",
              "dividend_rule": "Ex-date receivable; payment cash usable after payment date; reinvest paid pool into issuing ETF at next executable open. A full rebalance absorbs eligible paid pools.",
              "funding_rule": "No borrowing; cash-like target includes receivables. If locked receivables exceed target cash, scale stock allocation to available capital.",
              "one_price_rule": "Observed close is valued; high=low bars cannot fill; both target ETFs must be executable for a portfolio rebalance.",
              "nominal_exposure_multipliers": MULTIPLIERS,
              "actual_internal_fund_leverage_available": False}
    config_hash = hashlib.sha256(canonical_json(config).encode()).hexdigest()
    units = {s: 0. for s in SYMBOLS}
    marks = {s: None for s in SYMBOLS}
    split_events = {s: {e["date"]: e["ratio"] for e in data["splits"].get(s, [])} for s in SYMBOLS}
    ex_events: dict[str, list[tuple[str, dict]]] = {}
    for s in active:
        for e in data["dividends"].get(s, []):
            ex_events.setdefault(e["ex_date"], []).append((s, e))
    cash = initial
    receivables: list[dict] = []
    paid_pools: list[dict] = []
    pending = "initial"
    previous_session = None
    previous_nav = initial
    daily: list[dict] = []
    trades: list[dict] = []
    event = 0
    gap_interest = accrued = paid = 0.
    factor = (1+cash_yield)**(1/DAYS_PER_YEAR)
    day, end = date.fromisoformat(calendar[0]), date.fromisoformat(calendar[-1])
    while day <= end:
        d = day.isoformat()
        if d != calendar[0]:
            interest = cash*(factor-1)
            cash += interest
            gap_interest += interest
            for pool in paid_pools:
                pool["amount"] *= factor
        for s in active:
            if d in split_events[s]:
                ratio = float(split_events[s][d])
                units[s] *= ratio
                if marks[s] is not None:
                    marks[s] /= ratio
        for s, e in ex_events.get(d, []):
            amount = units[s]*e["amount"]
            if amount:
                receivables.append({"symbol": s, "amount": amount, "pay_date": e["pay_date"]})
                accrued += amount
        for r in list(receivables):
            if r["pay_date"] <= d:
                cash += r["amount"]
                paid += r["amount"]
                paid_pools.append(dict(r))
                receivables.remove(r)
        if d in sessions:
            bars = {s: data["prices"][s].get(d) for s in active}
            observed = {s: bool(b and b.get("Trading_Volume", 1) > 0) for s, b in bars.items()}
            one_price = {s: bool(observed[s] and "max" in b and "min" in b and b["max"] == b["min"])
                         for s, b in bars.items()}
            executable = {s: observed[s] and not one_price[s] for s in active}
            jointly_executable = all(executable.values())
            ar = sum(r["amount"] for r in receivables)
            opening = {s: float(bars[s]["open"]) if observed[s] else (marks[s] or 0.) for s in active}
            if portfolio.rule == "quarterly" and previous_session is not None:
                if (day.year, (day.month-1)//3) != (previous_session.year, (previous_session.month-1)//3):
                    pending = "quarterly" if pending != "initial" else "initial"
            deferred = pending is not None and not jointly_executable
            if pending is not None and jointly_executable:
                blocked = sum(p["amount"] for p in paid_pools if p["pay_date"] >= d)
                values = {s: units[s]*opening[s] for s in active}
                targets, post_nav = solve_targets(values, max(0., cash-blocked), ar+blocked,
                                                  portfolio.weights, costs)
                deltas = {s: targets[s]-values[s] for s in active}
                event += 1
                reason = pending
                # Sell proceeds fund purchases; no borrowing or simultaneous leverage.
                for s in sorted(active, key=lambda symbol: (deltas[symbol] >= 0, symbol)):
                    cash, record = trade_value(units, cash, s, deltas[s], opening[s], costs, d, reason, event)
                    if record:
                        record.update(nav_after_event=post_nav, target_weight=portfolio.weights[s],
                                      weight_after=targets[s]/post_nav)
                        trades.append(record)
                paid_pools = [p for p in paid_pools if p["pay_date"] >= d]
                pending = None
            for s in active:
                eligible = [p for p in paid_pools if p["symbol"] == s and p["pay_date"] < d]
                if eligible and executable[s]:
                    blocked = sum(p["amount"] for p in paid_pools if p["pay_date"] >= d)
                    budget = min(sum(p["amount"] for p in eligible), max(0., cash-blocked))
                    if budget > 1e-7:
                        event += 1
                        delta = budget/((1+costs.slippage)*(1+costs.commission))
                        cash, record = trade_value(units, cash, s, delta, opening[s], costs, d,
                                                   "dividend_reinvestment", event)
                        if record:
                            record.update(nav_after_event=None, target_weight=None, weight_after=None)
                            trades.append(record)
                    paid_pools = [p for p in paid_pools if p not in eligible]
                elif eligible:
                    deferred = True
            for s in active:
                if observed[s]:
                    marks[s] = float(bars[s]["close"])
            values = {s: units[s]*(marks[s] or 0.) for s in SYMBOLS}
            nav = sum(values.values())+cash+ar
            if nav <= 0 or not math.isfinite(nav):
                raise ArithmeticError("Invalid NAV.")
            weights = {s: values[s]/nav for s in SYMBOLS}
            cash_weight = cash/nav
            cashlike_weight = (cash+ar)/nav
            exposure = sum(MULTIPLIERS[s]*weights[s] for s in SYMBOLS)
            gap = (day-previous_session).days if previous_session is not None else 0
            rf = (1+cash_yield)**(gap/DAYS_PER_YEAR)-1
            if pending is None:
                if portfolio.rule == "threshold" and max(
                        [abs(weights[s]-portfolio.weights[s]) for s in SYMBOLS]
                        + [abs(cashlike_weight-portfolio.cash_weight)]) >= portfolio.weight_threshold-1e-12:
                    pending = "threshold"
                elif portfolio.rule == "exposure_threshold" and abs(exposure-portfolio.target_exposure) >= portfolio.exposure_threshold-1e-12:
                    pending = "exposure_threshold"
            daily.append({"date": d, "nav": nav, "return": nav/previous_nav-1,
                          "rf_return": rf, "excess_return": nav/previous_nav-1-rf,
                          "cash": cash, "receivables": ar, "holdings_value": sum(values.values()),
                          "units": dict(units), "units_00631L": units["00631L"], "units_0050": units["0050"],
                          "close_00631L": marks["00631L"], "close_0050": marks["0050"],
                          "weight_00631L": weights["00631L"], "weight_0050": weights["0050"],
                          "weight_cash": cash_weight, "weight_receivables": ar/nav,
                          "nominal_market_exposure": exposure, "actual_internal_exposure": None,
                          "cash_interest": gap_interest, "dividends_accrued": accrued,
                          "dividends_paid": paid, "tradable": jointly_executable,
                          "price_stale": not all(observed.values()), "one_price_bar": any(one_price.values()),
                          "execution_deferred": deferred, "pending_rebalance": pending or ""})
            previous_nav, previous_session = nav, day
            gap_interest = accrued = paid = 0.
        day += timedelta(days=1)
    metrics = calculate_metrics(daily, trades, initial, cash_yield, costs)
    if max(abs(r["excess_return"]) for r in daily) < 1e-12:
        metrics.update(sharpe=None, sortino=None)
    exposures = [r["nominal_market_exposure"] for r in daily]
    metrics.update(strategy=portfolio.name, portfolio=portfolio.label, rule=portfolio.rule,
                   config_hash=config_hash, target_exposure=portfolio.target_exposure,
                   mean_nominal_exposure=sum(exposures)/len(exposures),
                   min_nominal_exposure=min(exposures), max_nominal_exposure=max(exposures),
                   rebalance_count=len({t["event_id"] for t in trades if t["reason"] in
                                       {"quarterly", "threshold", "exposure_threshold"}}),
                   actual_internal_fund_leverage_available=False)
    return {"config": config, "config_hash": config_hash, "daily": daily,
            "trades": trades, "metrics": metrics}


def write_gzip_csv(path: Path, rows: list[dict]) -> None:
    with path.open("wb") as binary:
        with gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=list(rows[0]) if rows else ["date", "nav"])
                writer.writeheader()
                writer.writerows(rows)


def read_daily(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path(__file__).parent/"normalized.json")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent/"portfolio_results")
    parser.add_argument("--cash-yields", type=float, nargs="+", default=[0., .01, .02])
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    summary = []
    for rate in args.cash_yields:
        scenario = f"cash_{rate:.6f}".replace(".", "p")
        for portfolio in portfolios():
            result = run_portfolio(data, portfolio, rate)
            directory = args.out/scenario/portfolio.name
            directory.mkdir(parents=True, exist_ok=True)
            _write_json(directory/"config.json", result["config"])
            (directory/"config.sha256").write_text(result["config_hash"]+"\n")
            _write_json(directory/"metrics.json", result["metrics"])
            write_gzip_csv(directory/"daily.csv.gz", result["daily"])
            _write_csv(directory/"trades.csv", result["trades"], ["date", "symbol", "reason"])
            summary.append(result["metrics"])
    _write_json(args.out/"summary.json", summary)
    fields = ["strategy", "cash_yield_assumption", "ending_nav", "cagr", "max_drawdown", "sharpe", "sortino",
              "rebalance_count", "number_of_trades", "total_trading_cost", "mean_nominal_exposure", "min_nominal_exposure", "max_nominal_exposure"]
    _write_csv(args.out/"summary.csv", [{k: m[k] for k in fields} for m in summary], fields)
    _write_json(args.out/"manifest.json", {"engine_version": VERSION,
                "engine_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "legacy_engine_sha256": hashlib.sha256((Path(__file__).parent/"backtest.py").read_bytes()).hexdigest(),
                "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(), "runs": len(summary)})
    print(f"Completed {len(summary)} portfolio simulations.")


if __name__ == "__main__":
    main()
