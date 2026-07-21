#!/usr/bin/env python3
"""Judge a soak run: turn the per-cycle CSV into a per-token and per-bucket verdict.

  python3 soak_report.py <soak.csv>

Per-token performance is measured in the QUOTE token (pnl_q / start), not USD, so a
SOL-quoted pool's result reflects the strategy's edge and not SOL/USD drift. Portfolio
totals are in USD. The bucket tables are the point: they say which selection thresholds
actually paid, so the filter hypothesis (SEL_*) can be tuned from data, not vibes.
"""
import csv
import sys
from collections import defaultdict

TURN_BUCKETS = [(0, 1, "<1"), (1, 3, "1–3"), (3, 10, "3–10"), (10, 1e9, "10+")]
VOL_BUCKETS = [(0, 5, "<5%"), (5, 15, "5–15%"), (15, 30, "15–30%"), (30, 1e9, "30%+")]
AGE_BUCKETS = [(0, 6, "<6h"), (6, 24, "6–24h"), (24, 1e9, "24h+")]


def bucket(v, buckets):
    for lo, hi, label in buckets:
        if lo <= v < hi:
            return label
    return "?"


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def load(path):
    rows = defaultdict(list)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows[r["symbol"]].append(r)
    return rows


def summarize(rows):
    """One summary per token from its time series."""
    out = []
    for sym, rs in rows.items():
        first, last = rs[0], rs[-1]
        pnl_q = fnum(last["pnl_q"])
        start_q = fnum(last["eq_q"]) - pnl_q
        pnl_pct = (pnl_q / start_q * 100) if start_q else 0.0
        eqs = [fnum(r["eq_q"]) for r in rs]
        dd = (min(eqs) - start_q) / start_q * 100 if start_q else 0.0
        out.append({
            "symbol": sym,
            "turnover": fnum(last["turnover"]), "vol_h1": fnum(last["vol_h1"]), "age_h": fnum(last["age_h"]),
            "liq": fnum(last["liq"]),
            "pnl_pct": pnl_pct, "pnl_q": pnl_q, "fees_q": fnum(last["fees_q"]),
            "usd_pnl": fnum(last["usd_pnl"]), "usd_fees": fnum(last["usd_fees"]),
            "dd_pct": dd, "perch": sum(1 for r in rs if r["state"] == "PERCHED"),
            "state": last["state"], "cycles": len(rs),
        })
    return sorted(out, key=lambda x: x["pnl_pct"], reverse=True)


def bucket_table(name, toks, key, buckets):
    agg = defaultdict(lambda: {"n": 0, "wins": 0, "sum_pct": 0.0, "sum_usd": 0.0})
    for t in toks:
        b = bucket(t[key], buckets)
        a = agg[b]
        a["n"] += 1
        a["wins"] += 1 if t["pnl_pct"] > 0 else 0
        a["sum_pct"] += t["pnl_pct"]
        a["sum_usd"] += t["usd_pnl"]
    print(f"\n  by {name}:")
    print(f"    {'bucket':8} {'n':>3} {'win%':>6} {'avg pnl%':>9} {'net $':>10}")
    for _, _, label in buckets:
        a = agg.get(label)
        if not a or a["n"] == 0:
            continue
        print(f"    {label:8} {a['n']:>3} {100*a['wins']/a['n']:>5.0f}% "
              f"{a['sum_pct']/a['n']:>8.2f}% {a['sum_usd']:>10.2f}")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python3 soak_report.py <soak.csv>")
    toks = summarize(load(sys.argv[1]))
    if not toks:
        sys.exit("no rows")

    net_usd = sum(t["usd_pnl"] for t in toks)
    fees_usd = sum(t["usd_fees"] for t in toks)
    wins = sum(1 for t in toks if t["pnl_pct"] > 0)

    print("=" * 74)
    print(f"SOAK VERDICT — {len(toks)} tokens · win {100*wins/len(toks):.0f}% · "
          f"net ${net_usd:+.2f} · fees ${fees_usd:.2f}")
    print("=" * 74)
    print(f"\n  {'token':16} {'turn':>5} {'volh1':>6} {'age':>5} {'pnl%':>7} {'dd%':>7} {'feesQ':>8} {'perch':>5} {'state':>8}")
    for t in toks:
        print(f"  {t['symbol']:16} {t['turnover']:>5.2f} {t['vol_h1']:>5.1f}% {t['age_h']:>4.0f}h "
              f"{t['pnl_pct']:>+6.2f}% {t['dd_pct']:>+6.2f}% {t['fees_q']:>8.5f} {t['perch']:>5} {t['state']:>8}")

    bucket_table("turnover", toks, "turnover", TURN_BUCKETS)
    bucket_table("volatility (h1)", toks, "vol_h1", VOL_BUCKETS)
    bucket_table("age", toks, "age_h", AGE_BUCKETS)
    print("\n  Read the buckets: the ones with high win% AND positive avg pnl% are your")
    print("  filter thresholds. Tighten SEL_* toward them and re-soak.")


if __name__ == "__main__":
    main()
