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
import urllib.request

PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/{}"
JUP_URL = "https://lite-api.jup.ag/price/v3?ids={}"
UA = {"User-Agent": "valtgeist-podrunner/0.2"}


def _get_json(url, timeout):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def jupiter_usd_prices(mints, timeout=15):
    """Batched USD prices for many mints in ONE Jupiter call → {mint: usdPrice}.

    Lets a fleet price its whole book with a single request per tick instead of one per
    pod, which is the difference between staying under Jupiter's rate limit and hammering
    it. Missing/failed mints are simply absent from the returned dict (caller falls back)."""
    ids = ",".join(m for m in mints if m)
    if not ids:
        return {}
    try:
        data = _get_json(JUP_URL.format(ids), timeout)
    except Exception:
        return {}
    return {k: (v or {}).get("usdPrice") for k, v in data.items() if isinstance(v, dict)}


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

    def _jupiter(self, base_mint, quote_mint):
        """Returns (native, quote_usd): native = usd(base)/usd(quote) (base priced in
        quote units); quote_usd = USD per quote token (lets a fleet sum mixed quotes)."""
        data = _get_json(JUP_URL.format(f"{base_mint},{quote_mint}"), self.timeout)
        b = (data.get(base_mint) or {}).get("usdPrice")
        q = (data.get(quote_mint) or {}).get("usdPrice")
        native = float(b) / float(q) if b and q and q > 0 else None
        return native, (float(q) if q else None)

    def fetch(self):
        meta = self._meta or self._load_meta()

        price, quote_usd = None, None
        try:
            price, quote_usd = self._jupiter(meta["base_mint"], meta["quote_mint"])
        except Exception:
            price = None

        # Fallbacks: DexScreener's (coarse) native price, then the last good reading.
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
