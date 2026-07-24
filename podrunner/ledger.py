#!/usr/bin/env python3
"""Track-record LEDGER — every public call the bot makes, and what the market did next.

Append-only CSV. Rows:
  cut     a watchlist CUT call     (symbol, reason, price at call, CA)
  retire  a fleet exit             (toxic-flow / trail-stop / dwell / VETOED / ASHES / stale / sick)
  mark    a follow-up price check  (+5m / +15m / +60m after a cut/toxic call, % move vs the call)
  list    a posted watchlist       (membership snapshot)
  stable  a token-of-the-hour post

The marks are the point: they turn "trust me bro" into a verifiable record — median drop after
our calls, hit rate, and the misses kept honestly alongside the hits. Follow-up prices come from
one batched Jupiter call when marks fall due (a few per hour, negligible load). Pending marks
survive restarts via a JSON sidecar.

Thread-safe: record_* runs on the loop thread, poll() in a worker thread.
"""
import csv
import json
import os
import threading
import time
from pathlib import Path

HORIZONS_MIN = (5, 15, 60)


class Ledger:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get(
            "LEDGER_CSV", str(Path(__file__).resolve().parent / "ledger.csv")))
        self.side = self.path.with_suffix(".pending.json")
        self._lock = threading.Lock()
        self.pending = []          # [{due, ref_ts, sym, ca, base_mint, quote_mint, price0, horizon_m}]
        self._next_due = None
        self._fail_until = 0.0     # backoff gate after a failed price poll (no 3s hammering)
        if not self.path.exists():
            try:
                with open(self.path, "w", newline="") as f:
                    csv.writer(f).writerow(
                        ["ts", "kind", "symbol", "detail", "price", "ca", "ref_ts", "horizon_m", "move_pct"])
            except OSError:
                pass
        try:
            self.pending = json.loads(self.side.read_text())
        except Exception:
            self.pending = []
        self._recalc_due()

    # ------------------------------------------------------------------ internals
    def _recalc_due(self):
        self._next_due = min((p["due"] for p in self.pending), default=None)

    def _save_side(self):
        try:
            self.side.write_text(json.dumps(self.pending))
        except OSError:
            pass

    def _row(self, kind, symbol="", detail="", price="", ca="", ref_ts="", horizon_m="", move_pct="",
             ts=None):
        try:
            with open(self.path, "a", newline="") as f:
                csv.writer(f).writerow([
                    f"{ts if ts is not None else time.time():.0f}", kind, symbol, detail,
                    price if price == "" else f"{float(price):.10g}",
                    ca or "", ref_ts, horizon_m, move_pct])
        except (OSError, ValueError):
            pass

    def _schedule_marks(self, ref_ts, sym, ca, base_mint, quote_mint, price0):
        if not (base_mint and quote_mint and price0 and price0 > 0):
            return  # can't follow up without mints; the call row still stands
        for h in HORIZONS_MIN:
            self.pending.append({"due": ref_ts + h * 60, "ref_ts": ref_ts, "sym": sym, "ca": ca,
                                 "base_mint": base_mint, "quote_mint": quote_mint,
                                 "price0": float(price0), "horizon_m": h})
        self._save_side()
        self._recalc_due()

    # ------------------------------------------------------------------ recording (loop thread)
    def record_cut(self, sym, reason, price, ca, base_mint=None, quote_mint=None):
        now = time.time()
        with self._lock:
            self._row("cut", sym, reason, price or "", ca, ts=now)
            try:
                self._schedule_marks(now, sym, ca, base_mint, quote_mint, float(price or 0))
            except (TypeError, ValueError):
                pass

    def record_retire(self, sym, reason, price, ca, base_mint=None, quote_mint=None):
        """Fleet exits. Toxic-flow exits get follow-up marks too — they're implicit 'we got out
        before the dump' claims and deserve the same scrutiny as public cuts."""
        now = time.time()
        with self._lock:
            self._row("retire", sym, reason, price or "", ca, ts=now)
            if "toxic" in (reason or ""):
                try:
                    self._schedule_marks(now, sym, ca, base_mint, quote_mint, float(price or 0))
                except (TypeError, ValueError):
                    pass

    def record_list(self, symbols):
        with self._lock:
            self._row("list", detail="|".join(symbols))

    def record_stable(self, sym, detail="", price="", ca=""):
        with self._lock:
            self._row("stable", sym, detail, price or "", ca)

    # ------------------------------------------------------------------ follow-ups (worker thread)
    def due_now(self, now=None):
        now = now or time.time()
        return self._next_due is not None and now >= self._next_due and now >= self._fail_until

    def poll(self, now=None, price_fn=None):
        """Resolve due marks with ONE batched price call. price_fn(mints)->{mint: usd} is
        injectable for tests; defaults to Jupiter.

        A failed/empty price call sets a 60s backoff gate — retrying every loop cycle
        while rate-limited just keeps the limit tripped (this killed all marks for 8h
        on the first live night)."""
        now = now or time.time()
        with self._lock:
            due = [p for p in self.pending if p["due"] <= now]
            if not due:
                return 0
        if price_fn is None:
            # multi-source router: Jupiter → DexScreener → GeckoTerminal, with per-source
            # rest windows and its own failure logging (chunking handled inside)
            from live_feed import usd_prices
            price_fn = usd_prices
        mints = sorted({m for p in due for m in (p["base_mint"], p["quote_mint"])})
        usd, err = {}, None
        try:
            usd = price_fn(mints) or {}
        except Exception as exc:
            err = exc
        if not usd:
            why = f"{type(err).__name__}: {err}" if err else "no source returned prices"
            print(f"[ledger] price poll failed ({why}) — backing off 60s", flush=True)
            self._fail_until = now + 60.0
        resolved = 0
        with self._lock:
            for p in due:
                b, q = usd.get(p["base_mint"]), usd.get(p["quote_mint"])
                if b and q and q > 0 and p["price0"] > 0:
                    px = b / q
                    move = (px / p["price0"] - 1.0) * 100.0
                    self._row("mark", p["sym"], "", px, p["ca"],
                              ref_ts=f"{p['ref_ts']:.0f}", horizon_m=p["horizon_m"],
                              move_pct=f"{move:+.2f}", ts=now)
                    resolved += 1
                elif now - p["due"] < 900:
                    continue  # price source hiccup: keep it pending, retry within 15 min
                else:
                    self._row("mark", p["sym"], "no-price", "", p["ca"],
                              ref_ts=f"{p['ref_ts']:.0f}", horizon_m=p["horizon_m"], ts=now)
                self.pending.remove(p)
            self._save_side()
            self._recalc_due()
        return resolved


if __name__ == "__main__":
    # self-test: record -> marks fall due -> injected prices resolve them -> rows on disk.
    import tempfile
    d = tempfile.mkdtemp()
    led = Ledger(Path(d) / "ledger.csv")
    led.record_cut("KET-SOL", "toxic sell flow (vpin 0.97)", 0.0123, "KETca", "mintB", "mintQ")
    led.record_list(["A-SOL", "B-SOL"])
    led.record_stable("JUP-SOL", "24h +3%", 1.23, "JUPca")
    led.record_retire("BOP-SOL", "toxic-flow vpin=0.98 imb=-0.31", 0.5, "BOPca", "mintB2", "mintQ")
    assert len(led.pending) == 6  # 3 horizons x (1 cut + 1 toxic retire)
    assert not led.due_now(time.time())

    fake_now = time.time() + 16 * 60  # +16min: the 5m and 15m marks are due, 60m not yet
    assert led.due_now(fake_now)
    n = led.poll(now=fake_now, price_fn=lambda mints: {"mintB": 0.9, "mintB2": 0.6, "mintQ": 100.0})
    assert n == 4, n                      # 2 tokens x 2 horizons resolved
    assert len(led.pending) == 2          # the two 60m marks remain

    rows = list(csv.reader(open(led.path)))
    kinds = [r[1] for r in rows[1:]]
    assert kinds.count("mark") == 4 and "cut" in kinds and "retire" in kinds
    # KET called at 0.0123, marked at 0.9/100 = 0.009 -> about -26.8%
    ket = [r for r in rows if r[1] == "mark" and r[2] == "KET-SOL"][0]
    assert ket[8].startswith("-26."), ket
    # restart survival: a new Ledger on the same path reloads pending marks
    led2 = Ledger(led.path)
    assert len(led2.pending) == 2
    print("ledger self-test OK")
