"""Live price feed for a real Solana pool.

Pool *metadata* (the two token mints, symbol, dex, liquidity) comes once from
DexScreener. The *price* is then refreshed every poll from Jupiter's price API,
which updates per slot (~400ms) instead of DexScreener's ~30s cache — so the pod
sees real tick-level movement instead of a frozen price, and the market maker
actually has something to trade against.

Native price (base priced in the quote token, the unit the LP band works in) is
computed as usd(base) / usd(quote). stdlib only.
"""
import json
import time
import urllib.request

PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/{}"
JUP_URL = "https://lite-api.jup.ag/price/v3?ids={}"
DEX_TOKENS_URL = "https://api.dexscreener.com/tokens/v1/solana/{}"
GT_PRICE_URL = "https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{}"
UA = {"User-Agent": "valtgeist-podrunner/0.2"}


def _get_json(url, timeout):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _jup_usd(mints, timeout=15):
    """Jupiter price v3, chunked. Raises on HTTP errors (the router logs them)."""
    out = {}
    for i in range(0, len(mints), 50):
        data = _get_json(JUP_URL.format(",".join(mints[i:i + 50])), timeout)
        for k, v in data.items():
            px = (v or {}).get("usdPrice") if isinstance(v, dict) else None
            if px and float(px) > 0:
                out[k] = float(px)
    return out


def _dex_usd(mints, timeout=15):
    """DexScreener batched token prices (30/call, 300 req/min). Picks the deepest pool
    per mint; also prices a mint seen only on the QUOTE side via priceUsd/priceNative."""
    out, depth = {}, {}
    want = set(mints)
    for i in range(0, len(mints), 30):
        pairs = _get_json(DEX_TOKENS_URL.format(",".join(mints[i:i + 30])), timeout)
        for p in pairs or []:
            try:
                pu = float(p.get("priceUsd") or 0)
                pn = float(p.get("priceNative") or 0)
                liq = float(((p.get("liquidity") or {}).get("usd")) or 0)
            except (TypeError, ValueError):
                continue
            base = ((p.get("baseToken") or {}).get("address")) or ""
            quote = ((p.get("quoteToken") or {}).get("address")) or ""
            if base in want and pu > 0 and liq > depth.get(base, -1.0):
                out[base], depth[base] = pu, liq
            if quote in want and pu > 0 and pn > 0 and liq > depth.get(quote, -1.0):
                out[quote], depth[quote] = pu / pn, liq
    return out


def _gt_usd(mints, timeout=15):
    """GeckoTerminal simple token prices (30/call, ~30 req/min — last resort)."""
    out = {}
    for i in range(0, len(mints), 30):
        data = _get_json(GT_PRICE_URL.format(",".join(mints[i:i + 30])), timeout)
        prices = ((data.get("data") or {}).get("attributes") or {}).get("token_prices") or {}
        for k, v in prices.items():
            try:
                if float(v) > 0:
                    out[k] = float(v)
            except (TypeError, ValueError):
                pass
    return out


_SOURCES = (("jup", _jup_usd), ("dex", _dex_usd), ("gt", _gt_usd))
_src_down = {}   # source name -> unix ts when it may be tried again
SRC_REST_S = 60.0


def usd_prices(mints, timeout=10):
    """Batched {mint: usd} with FALLBACK: Jupiter → DexScreener → GeckoTerminal.

    A source that errors or answers empty is rested for 60s — rate-limit bans self-heal,
    and hammering a banned source keeps the ban alive (this cost us 8h of price marks on
    the first live night). Later sources only get asked for the mints still missing.
    `usd_prices.last_src` names who actually served the batch (e.g. "jup" or "dex+gt")."""
    want = [m for m in dict.fromkeys(mints) if m]
    got, used = {}, []
    now = time.time()
    for name, fn in _SOURCES:
        missing = [m for m in want if m not in got]
        if not missing:
            break
        if now < _src_down.get(name, 0.0):
            continue
        try:
            res = fn(missing, timeout) or {}
        except Exception as e:
            res = {}
            _src_down[name] = now + SRC_REST_S
            print(f"[price] {name} failed ({type(e).__name__}: {e}) — resting {SRC_REST_S:.0f}s",
                  flush=True)
        if res:
            used.append(name)
            got.update(res)
        elif _src_down.get(name, 0.0) <= now:
            # answered but priced NOTHING we asked for (a real batch always includes SOL/USDC
            # quotes) — treat as down so the next source takes over immediately next call too
            _src_down[name] = now + SRC_REST_S
            print(f"[price] {name} priced 0/{len(missing)} mints — resting {SRC_REST_S:.0f}s",
                  flush=True)
    usd_prices.last_src = "+".join(used) if used else "none"
    return got


usd_prices.last_src = "jup"


def jupiter_usd_prices(mints, timeout=15):
    """Back-compat name — now routes through the multi-source fallback chain."""
    return usd_prices(list(mints), timeout)


class LiveFeed:
    """One Solana pool. `.fetch()` returns {price, base_mint, symbol, dex,
    liquidity_usd}; price is the base in quote units, live from Jupiter."""

    def __init__(self, pool_address, timeout=15):
        self.pool_address = pool_address
        self.timeout = timeout
        self._meta = None   # pool metadata (fetched once from DexScreener)
        self._last = None   # last good full reading, to survive a transient blip

    def _load_meta(self):
        data = _get_json(PAIR_URL.format(self.pool_address), self.timeout)
        pairs = data.get("pairs") or []
        if not pairs:
            raise RuntimeError(f"DexScreener returned no pair for pool {self.pool_address}")
        p = pairs[0]
        base = p.get("baseToken", {}) or {}
        quote = p.get("quoteToken", {}) or {}
        self._meta = {
            "base_mint": base.get("address", ""),
            "quote_mint": quote.get("address", ""),
            "symbol": f"{base.get('symbol', '?')}-{quote.get('symbol', '?')}",
            "dex": p.get("dexId", "?"),
            "liquidity_usd": (p.get("liquidity") or {}).get("usd"),
            "seed_price": float(p.get("priceNative") or 0),
        }
        return self._meta

    def _spot(self, base_mint, quote_mint):
        """Returns (native, quote_usd): native = usd(base)/usd(quote) (base priced in
        quote units); quote_usd = USD per quote token (lets a fleet sum mixed quotes).
        Goes through the multi-source router, so a rate-limited Jupiter is skipped
        instantly instead of re-tried on every pod every tick."""
        usd = usd_prices([base_mint, quote_mint], self.timeout)
        b, q = usd.get(base_mint), usd.get(quote_mint)
        native = float(b) / float(q) if b and q and q > 0 else None
        return native, (float(q) if q else None)

    def fetch(self):
        meta = self._meta or self._load_meta()

        price, quote_usd = None, None
        try:
            price, quote_usd = self._spot(meta["base_mint"], meta["quote_mint"])
        except Exception:
            price = None

        # Fallbacks: DexScreener's (coarse) pool-native price, then the last good reading.
        if not price or price <= 0:
            try:
                data = _get_json(PAIR_URL.format(self.pool_address), self.timeout)
                p = (data.get("pairs") or [{}])[0]
                price = float(p.get("priceNative") or 0)
                pu, pn = float(p.get("priceUsd") or 0), price
                if pu > 0 and pn > 0:
                    quote_usd = pu / pn  # USD per quote token
            except Exception:
                price = 0.0
        if not price or price <= 0:
            if self._last:
                return self._last
            price = meta["seed_price"]
        if price <= 0:
            raise RuntimeError("no positive price from Jupiter or DexScreener")

        info = {
            "price": price,
            "base_mint": meta["base_mint"],
            "quote_mint": meta["quote_mint"],
            "symbol": meta["symbol"],
            "dex": meta["dex"],
            "liquidity_usd": meta["liquidity_usd"],
            "quote_usd": quote_usd,
        }
        self._last = info
        return info
