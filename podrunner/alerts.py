#!/usr/bin/env python3
"""Valtgeist ALERTS — real-time risk/flow signals from the live fleet, pushed to Telegram.

This is the free, no-custody product: the fleet already computes, per pool, a sell-cascade
score (Hawkes branching ratio), a toxic-flow score (VPIN + signed imbalance) and a runtime
state. This module edge-triggers on those and posts a short message to a Telegram channel.

It sells INFORMATION, never an outcome. Every alert is framed as a risk signal, not a trade
call — that keeps it honest (and out of the "implied returns" ditch the marketing kit warns
about) while still being genuinely useful to anyone LPing or trading these pools.

Three signals (all edge-triggered, so a channel sees events, not a firehose):
  cascade  hawkes_n crosses up through ALERT_CASCADE_N  (sell flow feeding on itself)
  toxic    VPIN >= ALERT_VPIN while flow is sell-sided   (one-sided informed selling)
  halt     a pod goes VETOED / ASHES                     (venue refused / equity floor blown)

Config (env):
  TELEGRAM_BOT_TOKEN   BotFather token; if unset, alerts print to the console only
  TELEGRAM_CHAT_ID     channel/chat id to post into (e.g. -100123... for a channel)
  ALERT_CASCADE_N      hawkes n that fires a cascade alert   (default 0.70)
  ALERT_CASCADE_CLEAR  hawkes n that re-arms it              (default 0.40)
  ALERT_VPIN           VPIN that fires a toxic-flow alert    (default 0.80)
  ALERT_IMB            min |sell imbalance| for toxic        (default 0.20)
  ALERT_REARM_S        seconds before the same pool+signal can re-fire (default 300)

Detection (`scan`) is pure and does no I/O — it's the self-tested core. Delivery (`send`)
does the network call and is best-effort; the caller runs it off the event loop.
"""
import json
import os
import urllib.error
import urllib.request


def _fmt_price(p):
    """Format a price across the huge dynamic range of Solana pairs (pump tokens are tiny)."""
    import math
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "?"
    # NaN fails every comparison, so a plain p<=0 guard lets it through to log10 -> floor(nan)
    # -> ValueError right in the alert path. isfinite() catches NaN and +/-inf in one go.
    if not math.isfinite(p) or p <= 0:
        return "?"
    if p >= 1:
        return f"{p:,.4f}"
    # enough significant figures for sub-cent / sub-micro prices
    digits = min(12, max(4, 2 - int(math.floor(math.log10(p)))))
    return f"{p:.{digits}f}"


class AlertBook:
    """Edge-triggers risk/flow alerts across the fleet and pushes them to Telegram/console."""

    def __init__(self, token=None, chat_id=None, cascade_n=None, cascade_clear=None,
                 vpin=None, imb=None, rearm_s=None):
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self.cascade_n = float(cascade_n if cascade_n is not None else os.environ.get("ALERT_CASCADE_N", "0.70"))
        self.cascade_clear = float(
            cascade_clear if cascade_clear is not None else os.environ.get("ALERT_CASCADE_CLEAR", "0.40"))
        self.vpin = float(vpin if vpin is not None else os.environ.get("ALERT_VPIN", "0.80"))
        self.imb = float(imb if imb is not None else os.environ.get("ALERT_IMB", "0.20"))
        self.rearm_s = float(rearm_s if rearm_s is not None else os.environ.get("ALERT_REARM_S", "300"))
        # (symbol, kind) -> "on" while the condition holds; dropped when it clears so it can re-fire.
        self._on = {}
        # (symbol, kind) -> t of last fire, for the re-arm cooldown (stops a flapping value spamming).
        self._last = {}

    @property
    def live(self):
        """True when a real Telegram channel is wired; otherwise alerts still print to console."""
        return bool(self.token and self.chat_id)

    def _rearmed(self, key, t):
        last = self._last.get(key)
        return last is None or (t - last) >= self.rearm_s

    def scan(self, pods, t):
        """Pure detection. Returns a list of newly-fired alert dicts; does NO I/O.

        pods: the live fleet (each has .symbol and .state as built by Pod._extract + flow.metrics).
        t:    integer seconds since fleet start (monotonic; the loop's n*POLL).
        """
        fired = []
        # Prune state for symbols no longer in the fleet — tokens churn constantly, and without
        # this the edge/cooldown dicts grow forever across weeks of rotation. (A symbol that
        # rotates back in later is a NEW listing; re-arming it fresh is the correct semantic.)
        live_syms = {getattr(p, "symbol", "?") for p in pods}
        for d in (self._on, self._last):
            for key in [k for k in d if k[0] not in live_syms]:
                del d[key]
        for p in pods:
            st = getattr(p, "state", None) or {}
            sym = getattr(p, "symbol", "?")
            price = st.get("price")

            # --- halt: VETOED / ASHES. Permanent, so fire exactly once, never clear/re-arm.
            rs = st.get("runtime_state")
            if rs in ("VETOED", "ASHES"):
                key = (sym, "halt")
                if key not in self._on:
                    self._on[key] = True
                    self._last[key] = t
                    reason = "venue refused" if rs == "VETOED" else "equity floor blown"
                    fired.append({"kind": "halt", "symbol": sym, "state": rs, "price": price,
                                  "text": self._halt_text(sym, rs, reason, st)})

            # --- cascade: hawkes n crosses the panic threshold; clears under cascade_clear.
            n = st.get("hawkes_n")
            self._edge(fired, sym, "cascade", t,
                       on=(n is not None and n >= self.cascade_n),
                       off=(n is None or n <= self.cascade_clear),
                       make=lambda: self._cascade_text(sym, n, price, st))

            # --- toxic: VPIN high AND flow one-sided to the sell. Clears when EITHER leg of the
            # fire condition lapses (VPIN off OR flow no longer sell-sided) — clearing on VPIN
            # alone would wedge the trigger "on" through a buy-flip and miss the next real
            # sell episode entirely.
            vp, imb = st.get("vpin"), st.get("imbalance", 0.0)
            self._edge(fired, sym, "toxic", t,
                       on=(vp is not None and vp >= self.vpin and imb is not None and imb <= -self.imb),
                       off=(vp is None or vp < self.vpin or imb is None or imb > -self.imb),
                       make=lambda: self._toxic_text(sym, vp, imb, price, st))
        return fired

    def _edge(self, fired, sym, kind, t, on, off, make):
        """Rising-edge trigger with hysteresis + re-arm cooldown, so channels get events."""
        key = (sym, kind)
        if on and key not in self._on and self._rearmed(key, t):
            self._on[key] = True
            self._last[key] = t
            fired.append({"kind": kind, "symbol": sym, "text": make()})
        elif off and key in self._on:
            del self._on[key]  # cleared — eligible to fire again once re-armed

    # ------------------------------------------------------------------ message copy (brand voice)
    def _cascade_text(self, sym, n, price, st):
        return (f"⚠ sell-cascade forming — {sym}\n"
                f"hawkes n={n:.2f} (down-moves self-exciting) · px {_fmt_price(price)}\n"
                f"risk signal, not a trade call.")

    def _toxic_text(self, sym, vp, imb, price, st):
        rate = st.get("rate_eps")
        rate_s = f" · {rate:.1f} trades/s" if isinstance(rate, (int, float)) else ""
        return (f"\U0001f6d1 toxic flow — {sym}\n"
                f"vpin={vp:.2f} · sell imbalance {imb:+.0%}{rate_s} · px {_fmt_price(price)}\n"
                f"one-sided informed selling. risk signal, not a trade call.")

    def _halt_text(self, sym, rs, reason, st):
        return (f"\U0001f6d1 pod halted — {sym}\n"
                f"state {rs} ({reason}). the strategy pulled out of this pool.")

    # ------------------------------------------------------------------ delivery (best-effort I/O)
    TEXT_MAX = 4096     # Telegram sendMessage hard limit
    CAPTION_MAX = 1024  # Telegram sendPhoto caption hard limit

    def _api(self, method, payload, tag, timeout=8):
        """One Telegram Bot API call with a single retry on 429 (honouring Retry-After).
        Never raises. Returns True on HTTP 200."""
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=data, headers={"Content-Type": "application/json"})
        for attempt in (0, 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status == 200
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 0:
                    try:
                        delay = min(float(e.headers.get("Retry-After", "3")), 30.0)
                    except (TypeError, ValueError):
                        delay = 3.0
                    print(f"[{tag}] telegram 429; retrying in {delay:.0f}s", flush=True)
                    import time
                    time.sleep(delay)
                    continue
                print(f"[{tag}] telegram {method} failed: HTTP {e.code}", flush=True)
                return False
            except (urllib.error.URLError, OSError, ValueError) as e:
                print(f"[{tag}] telegram {method} failed: {e}", flush=True)
                return False
        return False

    @staticmethod
    def _chunks(text, limit):
        """Split text into <=limit chunks, preferring block ('\\n\\n') then line boundaries."""
        if len(text) <= limit:
            return [text]
        out, cur = [], ""
        for block in text.split("\n\n"):
            while len(block) > limit:               # a single monster block: hard-split it
                out.append(block[:limit])
                block = block[limit:]
            joined = f"{cur}\n\n{block}" if cur else block
            if len(joined) > limit:
                out.append(cur)
                cur = block
            else:
                cur = joined
        if cur:
            out.append(cur)
        return out

    def post(self, text, tag="alert"):
        """Print + POST raw text to Telegram if configured. Never raises. Returns delivered?

        The shared delivery path — both single alerts (send) and the watchlist post through here.
        Long texts are split at block boundaries (Telegram hard-caps messages at 4096 chars —
        a full 10-entry watchlist with CA lines can exceed it, and would otherwise be dropped).
        """
        first = text.splitlines()[0] if text else ""
        print(f"[{tag}] {first}", flush=True)
        if not self.live:
            return False
        ok = True
        for chunk in self._chunks(text, self.TEXT_MAX - 16):
            ok = self._api("sendMessage", {
                "chat_id": self.chat_id, "text": chunk, "disable_web_page_preview": True,
            }, tag) and ok
        return ok

    def post_photo(self, png, caption, tag="alert"):
        """POST a PNG with caption via sendPhoto (multipart). Never raises. Returns delivered?

        Returns False when not live or no image — the caller then falls back to a text post.
        Captions are hard-capped by Telegram at 1024 chars; over-long ones are truncated (the
        caller's fallback-to-text path handles the full text if this ever matters).
        """
        first = caption.splitlines()[0] if caption else ""
        print(f"[{tag}] 📈 {first}", flush=True)
        if not self.live or not png:
            return False
        if len(caption) > self.CAPTION_MAX:
            caption = caption[:self.CAPTION_MAX - 1] + "…"
        try:
            boundary = "valtgeistFormBoundary7MA4YWxkTrZu0gW"
            body = b""
            for name, value in (("chat_id", str(self.chat_id)), ("caption", caption)):
                body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                         f"{value}\r\n").encode()
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                     f"filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
            body += png + b"\r\n" + f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self.token}/sendPhoto", data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            for attempt in (0, 1):
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        return r.status == 200
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt == 0:
                        try:
                            delay = min(float(e.headers.get("Retry-After", "3")), 30.0)
                        except (TypeError, ValueError):
                            delay = 3.0
                        print(f"[{tag}] telegram 429 (photo); retrying in {delay:.0f}s", flush=True)
                        import time
                        time.sleep(delay)
                        continue
                    raise
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"[{tag}] telegram photo failed: {e}", flush=True)
            return False
        return False

    def send(self, alert):
        """Print the alert, and POST to Telegram if configured. Never raises."""
        return self.post(alert.get("text", ""))

    def unfire(self, alert):
        """Roll back scan()'s edge state for one alert whose DELIVERY failed, so it re-fires
        next cycle instead of being lost forever (halt alerts fire exactly once — a dropped
        send would otherwise never be seen). Only call when .live and the send returned False."""
        key = (alert.get("symbol"), alert.get("kind"))
        self._on.pop(key, None)
        self._last.pop(key, None)


if __name__ == "__main__":
    import sys
    if "--send-test" in sys.argv:
        # smoke-test the delivery pipe end-to-end: build one alert and actually send it, so you
        # can confirm the Telegram bot/channel is wired before waiting on a real market event.
        _book = AlertBook()
        _ok = _book.send({"kind": "toxic", "symbol": "TEST-USDC", "text": (
            "\U0001f6d1 toxic flow — TEST-USDC\n"
            "vpin=0.91 · sell imbalance -70% · 5.0 trades/s · px 0.0000218\n"
            "one-sided informed selling. risk signal, not a trade call.\n"
            "— valtgeist alerts delivery test —")})
        if _book.live:
            print("telegram:", "delivered ✓" if _ok else "FAILED (see error above)")
        else:
            print("no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID set — printed to console only.")
        sys.exit(0 if (_ok or not _book.live) else 1)

    # self-test: prove edge-triggering (one fire per crossing, re-arm after clear) with no network.
    class _P:
        def __init__(self, symbol, state):
            self.symbol, self.state = symbol, state

    book = AlertBook(token="", chat_id="", cascade_n=0.7, cascade_clear=0.4, vpin=0.8, imb=0.2, rearm_s=100)

    # cascade rises -> fires once; stays high -> silent; clears; rises again after re-arm -> fires.
    def cascade(n):
        return [_P("BONK-USDC", {"runtime_state": "FLYING", "hawkes_n": n, "price": 0.00002181})]

    f1 = book.scan(cascade(0.30), t=0)      # calm
    f2 = book.scan(cascade(0.82), t=10)     # crossing up -> FIRE
    f3 = book.scan(cascade(0.85), t=20)     # still hot -> silent
    f4 = book.scan(cascade(0.10), t=30)     # clears (re-arms)
    f5 = book.scan(cascade(0.90), t=40)     # re-crossed but inside re-arm window -> silent
    f6 = book.scan(cascade(0.90), t=200)    # past re-arm -> FIRE
    assert not f1 and len(f2) == 1 and not f3 and not f4 and not f5 and len(f6) == 1, \
        [len(x) for x in (f1, f2, f3, f4, f5, f6)]
    assert f2[0]["kind"] == "cascade"

    # toxic needs high vpin AND sell-sided imbalance.
    tox = [_P("WIF-SOL", {"runtime_state": "FLYING", "vpin": 0.9, "imbalance": -0.6,
                          "rate_eps": 4.2, "price": 1.83})]
    ft = book.scan(tox, t=1000)
    assert len(ft) == 1 and ft[0]["kind"] == "toxic", ft
    # high vpin but BUY-sided -> not toxic (this product warns on sell pressure, not activity)
    buy = [_P("WIF-SOL", {"runtime_state": "FLYING", "vpin": 0.95, "imbalance": +0.7, "price": 1.9})]
    assert book.scan(buy, t=2000) == []

    # halt fires exactly once, ever (permanent state).
    halt = [_P("SCAM-USDC", {"runtime_state": "ASHES", "price": 0.0})]
    fh1 = book.scan(halt, t=3000)
    fh2 = book.scan(halt, t=4000)
    assert len(fh1) == 1 and fh1[0]["kind"] == "halt" and fh2 == [], (fh1, fh2)

    print(book._cascade_text("BONK-USDC", 0.82, 0.00002181, {}))
    print(book._toxic_text("WIF-SOL", 0.9, -0.6, 1.83, {"rate_eps": 4.2}))
    print("alerts self-test OK")
