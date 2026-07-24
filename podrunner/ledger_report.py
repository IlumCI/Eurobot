#!/usr/bin/env python3
"""Render the ledger as a clean morning report: every call, what happened after, honest verdicts.

Usage:  python3 ledger_report.py [ledger.csv] [--hours 12]
Verdict: a call "hit" if the token dropped >2% within 15 minutes of the call; "bounced" if it
rose >2%; flat otherwise. Misses are shown as prominently as hits — that's the product.
"""
import csv
import sys
import time
from collections import defaultdict
from pathlib import Path


def _t(ts):
    return time.strftime("%H:%M", time.gmtime(float(ts)))


def load(path, since_ts):
    calls, marks, lists, stables, retires = [], defaultdict(dict), [], [], []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                ts = float(r["ts"])
            except (TypeError, ValueError):
                continue
            if ts < since_ts and r["kind"] != "mark":
                continue
            k = r["kind"]
            if k == "cut":
                calls.append(r)
            elif k == "retire":
                retires.append(r)
                if "toxic" in (r.get("detail") or ""):
                    calls.append(r)  # toxic exits are implicit calls; judge them too
            elif k == "mark" and r.get("ref_ts"):
                marks[(r["symbol"], r["ref_ts"])][r["horizon_m"]] = r
            elif k == "list":
                lists.append(r)
            elif k == "stable":
                stables.append(r)
    return calls, marks, lists, stables, retires


def fmt_move(mk, h):
    r = mk.get(str(h))
    if not r:
        return "  (pending)"
    if r.get("detail") == "no-price":
        return "  (no data)"
    return f"{float(r['move_pct']):+7.1f}%"


def verdict(mk):
    r = mk.get("15") or mk.get("5")
    if not r or r.get("detail") == "no-price":
        return "…", None
    mv = float(r["move_pct"])
    if mv <= -2:
        return "✅ dropped", mv
    if mv >= 2:
        return "❌ bounced", mv
    return "≈ flat", mv


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = Path(args[0]) if args else Path(__file__).resolve().parent / "ledger.csv"
    hours = 12.0
    for i, a in enumerate(sys.argv):
        if a == "--hours" and i + 1 < len(sys.argv):
            hours = float(sys.argv[i + 1])
    since = time.time() - hours * 3600
    if not path.exists():
        sys.exit(f"no ledger at {path}")
    calls, marks, lists, stables, retires = load(path, since)

    print(f"VALTGEIST LEDGER REPORT — last {hours:.0f}h (times UTC)")
    print("=" * 64)

    judged = []
    if calls:
        print(f"\nCALLS ({len(calls)}) — cut/toxic exits, with what the market did after:")
        for c in sorted(calls, key=lambda r: float(r["ts"])):
            mk = marks.get((c["symbol"], f"{float(c['ts']):.0f}"), {})
            v, mv = verdict(mk)
            if mv is not None:
                judged.append(mv)
            kind = "CUT " if c["kind"] == "cut" else "EXIT"
            print(f"  {_t(c['ts'])}  {kind} {c['symbol']:<14} {v:<10}"
                  f" 5m:{fmt_move(mk, 5)}  15m:{fmt_move(mk, 15)}  60m:{fmt_move(mk, 60)}")
            print(f"         └ {(c.get('detail') or '')[:70]}  @ {c.get('price', '?')}")
        if judged:
            hits = sum(1 for m in judged if m <= -2)
            med = sorted(judged)[len(judged) // 2]
            print(f"\n  hit rate: {hits}/{len(judged)} dropped >2% within 15m · "
                  f"median 15m move after call: {med:+.1f}%")
    else:
        print("\nCALLS: none in window.")

    if retires:
        why = defaultdict(int)
        for r in retires:
            why[(r.get("detail") or "?").split(" ")[0].split("(")[0]] += 1
        wl = " · ".join(f"{k}×{n}" for k, n in sorted(why.items(), key=lambda x: -x[1]))
        print(f"\nFLEET EXITS ({len(retires)}): {wl}")

    if lists:
        churn = 0
        prev = None
        for row in lists:
            cur = set((row.get("detail") or "").split("|"))
            if prev is not None:
                churn += len(cur ^ prev)
            prev = cur
        last = (lists[-1].get("detail") or "").replace("|", ", ")
        print(f"\nWATCHLISTS posted: {len(lists)} · membership changes: {churn}")
        print(f"  latest: {last[:90]}")

    if stables:
        for s in stables:
            print(f"\nTOKEN OF THE HOUR: {_t(s['ts'])} {s['symbol']} {s.get('detail', '')}")

    print()


if __name__ == "__main__":
    main()
