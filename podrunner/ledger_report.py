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
    calls, marks, lists, stables, retires, listeds = [], defaultdict(dict), [], [], [], []
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
            elif k == "listed":
                listeds.append(r)
            elif k == "stable":
                stables.append(r)
    return calls, marks, lists, stables, retires, listeds


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
    hours = 12.0
    positional, skip = [], False
    for i, a in enumerate(sys.argv[1:], 1):
        if skip:
            skip = False
            continue
        if a == "--hours" and i + 1 < len(sys.argv):
            hours = float(sys.argv[i + 1])
            skip = True
        elif not a.startswith("--"):
            positional.append(a)
    path = Path(positional[0]) if positional else Path(__file__).resolve().parent / "ledger.csv"
    since = time.time() - hours * 3600
    if not path.exists():
        sys.exit(f"no ledger at {path}")
    calls, marks, lists, stables, retires, listeds = load(path, since)

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
            # a cut row carrying move_pct = what it did between LISTING and the cut —
            # the flagged move itself, and the number worth putting in a receipt
            on_watch = f"listed→cut: {float(c['move_pct']):+.1f}% · " if c.get("move_pct") else ""
            print(f"         └ {on_watch}{(c.get('detail') or '')[:70]}  @ {c.get('price', '?')}")
        if judged:
            hits = sum(1 for m in judged if m <= -2)
            med = sorted(judged)[len(judged) // 2]
            print(f"\n  hit rate: {hits}/{len(judged)} dropped >2% within 15m · "
                  f"median 15m move after call: {med:+.1f}%")
    else:
        print("\nCALLS: none in window.")

    if listeds:
        held, lj = 0, []
        print(f"\nLISTED AS TRADEABLE ({len(listeds)}) — did they hold after we said calm?")
        for c in sorted(listeds, key=lambda r: float(r["ts"])):
            mk = marks.get((c["symbol"], f"{float(c['ts']):.0f}"), {})
            r15 = mk.get("15") or mk.get("5")
            if not r15 or r15.get("detail") == "no-price":
                v = "…"
            else:
                mv = float(r15["move_pct"])
                lj.append(mv)
                v = "📈 rose" if mv >= 2 else ("❌ dumped" if mv <= -2 else "✅ held")
                held += mv > -2
            print(f"  {_t(c['ts'])}  LIST {c['symbol']:<14} {v:<10}"
                  f" 5m:{fmt_move(mk, 5)}  15m:{fmt_move(mk, 15)}  60m:{fmt_move(mk, 60)}")
        if lj:
            med = sorted(lj)[len(lj) // 2]
            print(f"\n  held or rose (15m): {held}/{len(lj)} · median 15m move after listing: {med:+.1f}%")

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
