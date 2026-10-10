#!/usr/bin/env python3
"""Reproducible Taiwan ETF backtest using only Python's standard library.

Input is normalized.json with calendar, raw open/close prices, verified splits,
cash dividends (ex_date, pay_date, amount per unit), and source metadata.
Missing bars must already have been verified as suspensions by the data audit.
The engine never forward-fills an executable price. It only carries the last
close for valuation; splits also divide that carried price.

Run: python3 backtest.py --input normalized.json --out results
Tests: python3 -m unittest -v test_backtest.py

Fractional ETF units are allowed. Fees have no minimum or broker discount.
Cash yield is a constant hypothetical annual effective rate, not observed data.
The ETF itself already embeds leverage financing and fund management costs;
these are not charged a second time. Taxes on cash interest/dividends, inflation,
personal tax circumstances, bid-ask variation, and market impact are excluded.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ENGINE_VERSION = "1.0.0"
DAYS_PER_YEAR = 365.2425
SESSIONS_PER_YEAR = 252
OPEN_TO_CLOSE_DAYS = 4.5 / 24  # 09:00 to 13:30, Asia/Taipei.


@dataclass(frozen=True)
class Costs:
    commission: float = 0.001425
    sell_tax: float = 0.001
    slippage: float = 0.0005

    def __post_init__(self):
        if not all(0 <= x < 1 for x in asdict(self).values()):
            raise ValueError("Costs must be fractions in [0, 1).")
        if self.commission + self.sell_tax >= 1:
            raise ValueError("Sell charges must sum to less than one.")


@dataclass(frozen=True)
class Strategy:
    name: str
    symbol: str
    weight: float
    rebalance: str  # buyhold, quarterly, threshold
    threshold: float = 0.10

    def __post_init__(self):
        if not 0 <= self.weight <= 1:
            raise ValueError("Target weight must be in [0, 1].")
        if self.rebalance not in {"buyhold", "quarterly", "threshold"}:
            raise ValueError("Unknown rebalance rule.")
        if not 0 < self.threshold <= 1:
            raise ValueError("Threshold must be a fraction in (0, 1].")


def standard_strategies(threshold: float = 0.10) -> list[Strategy]:
    result = [Strategy("0050_buyhold", "0050", 1.0, "buyhold", threshold)]
    for weight in (0.50, 0.60, 0.80):
        for rule in ("quarterly", "threshold"):
            result.append(Strategy(f"00631L_{int(weight * 100)}_{rule}",
                                   "00631L", weight, rule, threshold))
    result.append(Strategy("00631L_100_buyhold", "00631L", 1.0,
                           "buyhold", threshold))
    return result


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _finite_number(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Nonfinite number: {label}")
    return number


def validate_input(data: dict) -> None:
    calendar = data["calendar"]
    if not calendar or calendar != sorted(set(calendar)):
        raise ValueError("Calendar must be nonempty, sorted, and unique.")
    for day in calendar:
        date.fromisoformat(day)
    if calendar[0] != data["start"] or calendar[-1] != data["end"]:
        raise ValueError("Calendar must exactly match the normalized start/end bounds.")
    for symbol, prices in data["prices"].items():
        missing = set(calendar) - set(prices)
        verified = set(data.get("metadata", {}).get("verified_suspensions", {}).get(symbol, []))
        if missing - verified:
            raise ValueError(f"Unverified missing bars for {symbol}: {sorted(missing - verified)}")
        for day, bar in prices.items():
            date.fromisoformat(day)
            if _finite_number(bar["close"], f"{symbol} {day} close") <= 0:
                raise ValueError(f"Close must be positive: {symbol} {day}")
            volume = _finite_number(bar.get("Trading_Volume", 1), "volume")
            if volume < 0:
                raise ValueError("Trading volume cannot be negative.")
            if volume > 0 and _finite_number(bar["open"], "open") <= 0:
                raise ValueError(f"Executable open must be positive: {symbol} {day}")
    for symbol, events in data.get("splits", {}).items():
        seen = set()
        for event in events:
            date.fromisoformat(event["date"])
            if event["date"] in seen:
                raise ValueError(f"Duplicate split: {symbol} {event['date']}")
            seen.add(event["date"])
            if _finite_number(event["ratio"], "split ratio") <= 0:
                raise ValueError("Split ratio must be positive.")
    for events in data.get("dividends", {}).values():
        for event in events:
            ex_day = date.fromisoformat(event["ex_date"])
            pay_day = date.fromisoformat(event["pay_date"])
            if pay_day < ex_day or _finite_number(event["amount"], "dividend") < 0:
                raise ValueError("Invalid cash dividend.")


def target_trade(units: float, cash: float, receivables: float,
                 price: float, weight: float, costs: Costs,
                 max_cash_to_spend: float | None = None) -> tuple[float, float, dict | None]:
    """Solve target weight against raw-open NAV AFTER all transaction charges.

    NAV includes receivables, but buy funding is restricted to available cash.
    A limited-cash buy may therefore finish below target. No leverage, shorting,
    or borrowing against a dividend receivable is allowed.
    """
    if price <= 0 or min(units, cash, receivables) < -1e-7:
        raise ValueError("Invalid account or execution price.")
    nav_before = cash + units * price + receivables
    gap = weight * nav_before - units * price
    if abs(gap) <= max(1e-9, nav_before * 1e-12):
        return units, cash, None
    if gap > 0:
        side = "buy"
        execution_price = price * (1 + costs.slippage)
        unit_cash = execution_price * (1 + costs.commission)
        unit_loss = unit_cash - price
        quantity = gap / (price + weight * unit_loss)
        available = max(0.0, cash)
        if max_cash_to_spend is not None:
            available = min(available, max(0.0, max_cash_to_spend))
        quantity = min(quantity, available / unit_cash)
        signed_quantity = quantity
        gross = quantity * execution_price
        commission = gross * costs.commission
        sell_tax = 0.0
        cash_change = -(gross + commission)
    else:
        side = "sell"
        execution_price = price * (1 - costs.slippage)
        unit_cash = execution_price * (1 - costs.commission - costs.sell_tax)
        unit_loss = price - unit_cash
        quantity = min(units, -gap / (price - weight * unit_loss))
        signed_quantity = -quantity
        gross = quantity * execution_price
        commission = gross * costs.commission
        sell_tax = gross * costs.sell_tax
        cash_change = gross - commission - sell_tax
    if quantity <= 1e-12:
        return units, cash, None
    new_units = max(0.0, units + signed_quantity)
    new_cash = cash + cash_change
    if new_cash < -max(1e-7, nav_before * 1e-12):
        raise ArithmeticError("Trade attempted to borrow cash.")
    new_cash = max(0.0, new_cash)
    nav_after = new_cash + new_units * price + receivables
    slippage_cost = quantity * abs(execution_price - price)
    record = {
        "side": side, "quantity": quantity, "raw_open": price,
        "execution_price": execution_price, "gross_amount": gross,
        "commission": commission, "sell_tax": sell_tax,
        "slippage_cost": slippage_cost,
        "total_cost": commission + sell_tax + slippage_cost,
        "cash_change": cash_change, "units_after": new_units,
        "cash_after": new_cash, "receivables": receivables,
        "nav_before": nav_before, "nav_after": nav_after,
        "target_weight": weight,
        "weight_after": new_units * price / nav_after if nav_after else 0.0,
    }
    return new_units, new_cash, record


def drawdown_statistics(days: list[str], navs: list[float], initial: float) -> dict:
    """Drawdowns include the first-day initial-capital/open anchor.

    A censored episode is still underwater at the sample end; its duration is
    elapsed time so far, never a claim that recovery has occurred.
    """
    peak_value = initial
    peak_day = days[0]
    peak_index = -1
    active = None
    episodes = []
    drawdowns = []
    for index, (day, nav) in enumerate(zip(days, navs)):
        dd = nav / peak_value - 1
        drawdowns.append(min(0.0, dd))
        if nav >= peak_value - max(1e-8, peak_value * 1e-12):
            if active is not None:
                active.update(recovery_date=day, censored=False,
                              duration_calendar_days=(date.fromisoformat(day) -
                                                      date.fromisoformat(peak_day)).days,
                              duration_sessions=index - peak_index)
                episodes.append(active)
                active = None
            peak_value, peak_day, peak_index = nav, day, index
        else:
            if active is None:
                active = {"peak_date": peak_day, "peak_nav": peak_value,
                          "trough_date": day, "trough_nav": nav,
                          "depth": dd, "recovery_date": None, "censored": True}
            elif dd < active["depth"]:
                active.update(trough_date=day, trough_nav=nav, depth=dd)
    if active is not None:
        active.update(duration_calendar_days=(date.fromisoformat(days[-1]) -
                                               date.fromisoformat(peak_day)).days,
                      duration_sessions=len(days) - 1 - peak_index)
        episodes.append(active)
    rank = lambda e: (e["duration_calendar_days"], e["duration_sessions"])
    completed = [e for e in episodes if not e["censored"]]
    return {
        "max_drawdown": min(drawdowns, default=0.0),
        "max_drawdown_episode": min(episodes, key=lambda e: e["depth"]) if episodes else None,
        "longest_recovery": max(episodes, key=rank) if episodes else None,
        "longest_completed_recovery": max(completed, key=rank) if completed else None,
        "ongoing_drawdown": active,
        "drawdown_series": drawdowns,
    }


def calculate_metrics(daily: list[dict], trades: list[dict], initial: float,
                      cash_yield: float, costs: Costs) -> dict:
    navs = [r["nav"] for r in daily]
    days = [r["date"] for r in daily]
    returns = [r["return"] for r in daily]
    excess = [r["excess_return"] for r in daily]
    years = ((date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days
             + OPEN_TO_CLOSE_DAYS) / DAYS_PER_YEAR
    excess_mean = statistics.mean(excess)
    excess_std = statistics.stdev(excess) if len(excess) > 1 else 0.0
    downside = math.sqrt(statistics.mean(min(r, 0.0) ** 2 for r in excess))
    last = daily[-1]
    liquidation_gross = last["holdings_value"] * (1 - costs.slippage)
    liquidation_cost = (last["holdings_value"] * costs.slippage +
                        liquidation_gross * (costs.commission + costs.sell_tax))
    result = {
        "start": days[0], "end": days[-1], "sessions": len(daily),
        "initial_capital": initial, "cash_yield_assumption": cash_yield,
        "elapsed_years": years, "ending_nav": navs[-1],
        "ending_cash": last["cash"], "ending_units": last["units"],
        "ending_receivables": last["receivables"],
        "ending_liquidation_nav": navs[-1] - liquidation_cost,
        "additional_terminal_liquidation_cost": liquidation_cost,
        "total_return": navs[-1] / initial - 1,
        "cagr": (navs[-1] / initial) ** (1 / years) - 1 if years else None,
        "sharpe": excess_mean / excess_std * math.sqrt(SESSIONS_PER_YEAR)
                  if excess_std > 0 else None,
        "sortino": excess_mean / downside * math.sqrt(SESSIONS_PER_YEAR)
                   if downside > 0 else None,
        "annualized_volatility": statistics.stdev(returns) * math.sqrt(SESSIONS_PER_YEAR)
                                 if len(returns) > 1 else None,
        "number_of_trades": len(trades),
        "total_commission": sum(t["commission"] for t in trades),
        "total_sell_tax": sum(t["sell_tax"] for t in trades),
        "total_slippage": sum(t["slippage_cost"] for t in trades),
        "total_trading_cost": sum(t["total_cost"] for t in trades),
        "cash_interest_earned": sum(r["cash_interest"] for r in daily),
        "dividends_accrued": sum(r["dividends_accrued"] for r in daily),
        "dividends_paid": sum(r["dividends_paid"] for r in daily),
        "stale_valuation_sessions": sum(r["price_stale"] for r in daily),
        "one_price_bar_sessions": sum(r["one_price_bar"] for r in daily),
        "deferred_execution_sessions": sum(r["execution_deferred"] for r in daily),
        "cash_yield_is_hypothetical": True,
        "ratio_undefined_convention": "null when variance/downside denominator is zero",
        "cagr_clock": "Asia/Taipei first open 09:00 to final close 13:30; ACT/365.2425",
        "risk_ratio_convention": "252 sessions; sample stdev of excess returns; Sortino uses all observations, MAR=cash rate; first-day capital anchor included",
        "liquidation_convention": "Sell all units at final close with slippage/commission/tax; outstanding dividend receivables retained at face value",
    }
    result.update(drawdown_statistics(days, navs, initial))
    drawdowns = result.pop("drawdown_series")
    for row, dd in zip(daily, drawdowns):
        row["drawdown"] = dd
    return result


def run_backtest(data: dict, strategy: Strategy, cash_yield: float = 0.01,
                 initial: float = 1_000_000.0, costs: Costs | None = None) -> dict:
    validate_input(data)
    costs = costs or Costs()
    if initial <= 0 or not math.isfinite(initial) or cash_yield <= -1 or not math.isfinite(cash_yield):
        raise ValueError("Invalid initial capital or cash yield.")
    bars = data["prices"][strategy.symbol]
    calendar = data["calendar"]
    session_set = set(calendar)
    split_events = {x["date"]: float(x["ratio"])
                    for x in data.get("splits", {}).get(strategy.symbol, [])}
    ex_events = {}
    for event in data.get("dividends", {}).get(strategy.symbol, []):
        ex_events.setdefault(event["ex_date"], []).append(event)
    source_hash = hashlib.sha256(canonical_json(data).encode()).hexdigest()
    config = {
        "engine_version": ENGINE_VERSION, "input_semantic_sha256": source_hash,
        "strategy": asdict(strategy), "cash_yield": cash_yield,
        "initial_capital": initial, "costs": asdict(costs),
        "start": calendar[0], "end": calendar[-1],
        "cash_day_count": DAYS_PER_YEAR, "sessions_per_year": SESSIONS_PER_YEAR,
        "fractional_units": True,
        "dividend_rule": "Entitlement before ex-date opening trades, receivable until payment, buyhold reinvestment after payment date at first tradable open",
        "quarter_rule": "First market session of new calendar quarter, delayed until ETF executable open",
        "threshold_rule": "Absolute close weight deviation >= threshold; execute at subsequent tradable open",
        "one_price_bar_execution": "When daily high equals low, opening fills are conservatively rejected ex post; pending orders wait for a subsequent executable open. The close still marks NAV. This is an execution assumption, not a signal using future prices.",
    }
    config_hash = hashlib.sha256(canonical_json(config).encode()).hexdigest()
    units, cash, last_close = 0.0, initial, None
    receivables = []
    reinvest_payments = []
    daily, trades = [], []
    pending = "initial"
    previous_session = None
    previous_nav = initial
    day = date.fromisoformat(calendar[0])
    end_day = date.fromisoformat(calendar[-1])
    daily_interest_rate = (1 + cash_yield) ** (1 / DAYS_PER_YEAR) - 1
    gap_interest = gap_dividends_paid = gap_dividends_accrued = 0.0
    while day <= end_day:
        day_text = day.isoformat()
        if day_text != calendar[0]:
            interest = cash * daily_interest_rate
            cash += interest
            gap_interest += interest
        if day_text in split_events:
            ratio = split_events[day_text]
            units *= ratio
            if last_close is not None:
                last_close /= ratio
        for event in ex_events.get(day_text, []):
            amount = units * float(event["amount"])
            if amount > 0:
                receivables.append({"amount": amount, "pay_date": event["pay_date"]})
                gap_dividends_accrued += amount
        unpaid = []
        for dividend in receivables:
            if dividend["pay_date"] <= day_text:
                cash += dividend["amount"]
                gap_dividends_paid += dividend["amount"]
                reinvest_payments.append(dict(dividend))
            else:
                unpaid.append(dividend)
        receivables = unpaid
        if day_text in session_set:
            bar = bars.get(day_text)
            has_price = bool(bar and float(bar.get("Trading_Volume", 1)) > 0
                             and float(bar["open"]) > 0)
            one_price_bar = bool(has_price and "max" in bar and "min" in bar
                                 and float(bar["max"]) == float(bar["min"]))
            tradable = has_price and not one_price_bar
            ar = sum(x["amount"] for x in receivables)
            if strategy.rebalance == "quarterly" and previous_session is not None:
                old_quarter = (previous_session.year, (previous_session.month - 1) // 3)
                new_quarter = (day.year, (day.month - 1) // 3)
                if old_quarter != new_quarter and pending != "initial":
                    pending = "quarterly"
            eligible_payment = any(x["pay_date"] < day_text for x in reinvest_payments)
            execution_deferred = not tradable and (pending is not None or
                (strategy.rebalance == "buyhold" and strategy.weight == 1 and eligible_payment))
            if tradable and pending is not None:
                units, cash, trade = target_trade(units, cash, ar, float(bar["open"]),
                                                  strategy.weight, costs)
                if trade is not None:
                    trade.update(date=day_text, symbol=strategy.symbol, reason=pending)
                    trades.append(trade)
                pending = None
            if (strategy.rebalance == "buyhold" and strategy.weight == 1 and tradable
                    and eligible_payment):
                # A dividend paid today is deliberately withheld until a later open.
                blocked = sum(x["amount"] for x in reinvest_payments
                              if x["pay_date"] >= day_text)
                units, cash, trade = target_trade(units, cash, ar, float(bar["open"]),
                                                  1.0, costs, cash - blocked)
                if trade is not None:
                    trade.update(date=day_text, symbol=strategy.symbol,
                                 reason="dividend_reinvestment")
                    trades.append(trade)
                reinvest_payments = [x for x in reinvest_payments if x["pay_date"] >= day_text]
            # A one-price bar supplies a valid mark despite the no-fill assumption.
            if has_price:
                last_close = float(bar["close"])
            elif last_close is None and bar is not None:
                last_close = float(bar["close"])
            if units > 0 and last_close is None:
                raise ValueError(f"No price available to value owned units on {day_text}.")
            holdings_value = units * (last_close or 0.0)
            nav = cash + holdings_value + ar
            if nav <= 0 or not math.isfinite(nav):
                raise ArithmeticError("Portfolio NAV must remain positive.")
            weight = holdings_value / nav
            gap_days = (day - previous_session).days if previous_session is not None else 0
            rf_return = (1 + cash_yield) ** (gap_days / DAYS_PER_YEAR) - 1
            portfolio_return = nav / previous_nav - 1
            if (strategy.rebalance == "threshold" and pending is None
                    and abs(weight - strategy.weight) >= strategy.threshold - 1e-12):
                pending = "threshold"
            daily.append({
                "date": day_text, "units": units, "close": last_close,
                "cash": cash, "holdings_value": holdings_value, "receivables": ar,
                "nav": nav, "weight": weight, "return": portfolio_return,
                "rf_return": rf_return, "excess_return": portfolio_return - rf_return,
                "cash_interest": gap_interest, "dividends_accrued": gap_dividends_accrued,
                "dividends_paid": gap_dividends_paid, "tradable": tradable,
                "price_stale": not has_price, "one_price_bar": one_price_bar,
                "execution_deferred": execution_deferred,
                "pending_rebalance": pending or "",
            })
            previous_nav, previous_session = nav, day
            gap_interest = gap_dividends_paid = gap_dividends_accrued = 0.0
        day += timedelta(days=1)
    metrics = calculate_metrics(daily, trades, initial, cash_yield, costs)
    metrics.update(strategy=strategy.name, config_hash=config_hash)
    return {"config": config, "config_hash": config_hash, "daily": daily,
            "trades": trades, "metrics": metrics}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict], fallback_fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else fallback_fields)
        writer.writeheader()
        writer.writerows(rows)


def write_run(output: Path, result: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "config.json", result["config"])
    (output / "config.sha256").write_text(result["config_hash"] + "\n", encoding="ascii")
    _write_json(output / "metrics.json", result["metrics"])
    _write_csv(output / "daily.csv", result["daily"], ["date", "nav"])
    _write_csv(output / "trades.csv", result["trades"], ["date", "symbol", "reason", "side"])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--initial", type=float, default=1_000_000.0)
    parser.add_argument("--cash-yields", type=float, nargs="+", default=[0.0, 0.01, 0.02])
    parser.add_argument("--commission", type=float, default=0.001425)
    parser.add_argument("--sell-tax", type=float, default=0.001)
    parser.add_argument("--slippage", type=float, default=0.0005)
    parser.add_argument("--threshold", type=float, default=0.10)
    args = parser.parse_args(argv)
    data = json.loads(args.input.read_text(encoding="utf-8"))
    validate_input(data)
    costs = Costs(args.commission, args.sell_tax, args.slippage)
    args.out.mkdir(parents=True, exist_ok=True)
    all_metrics = []
    for cash_yield in args.cash_yields:
        scenario = f"cash_{cash_yield:.6f}".replace(".", "p").replace("-", "m")
        for strategy in standard_strategies(args.threshold):
            result = run_backtest(data, strategy, cash_yield, args.initial, costs)
            write_run(args.out / scenario / strategy.name, result)
            all_metrics.append(result["metrics"])
    _write_json(args.out / "summary.json", all_metrics)
    fields = ["strategy", "cash_yield_assumption", "start", "end", "sessions",
              "cagr", "max_drawdown", "sharpe", "sortino", "ending_nav",
              "ending_liquidation_nav", "total_trading_cost", "number_of_trades",
              "cash_interest_earned", "longest_recovery_calendar_days",
              "longest_recovery_sessions", "longest_recovery_censored",
              "longest_recovery_peak", "longest_recovery_trough",
              "longest_recovery_date", "config_hash"]
    summary_rows = []
    for metric in all_metrics:
        row = {field: metric.get(field) for field in fields}
        recovery = metric["longest_recovery"] or {}
        row.update(longest_recovery_calendar_days=recovery.get("duration_calendar_days", 0),
                   longest_recovery_sessions=recovery.get("duration_sessions", 0),
                   longest_recovery_censored=recovery.get("censored", False),
                   longest_recovery_peak=recovery.get("peak_date"),
                   longest_recovery_trough=recovery.get("trough_date"),
                   longest_recovery_date=recovery.get("recovery_date"))
        summary_rows.append(row)
    _write_csv(args.out / "summary.csv", summary_rows, fields)
    manifest = {
        "engine_version": ENGINE_VERSION,
        "engine_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_file_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "input_semantic_sha256": hashlib.sha256(canonical_json(data).encode()).hexdigest(),
        "runs": len(all_metrics), "metadata": data.get("metadata", {}),
    }
    _write_json(args.out / "manifest.json", manifest)
    print(f"Completed {len(all_metrics)} reproducible backtests: {args.out.resolve()}")


if __name__ == "__main__":
    main()
