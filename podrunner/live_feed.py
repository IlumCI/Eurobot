"""Live price feed for a real Solana pool, via DexScreener (free, no key).

The pod runtime reads a real pool's live price through this so the paper-mode
demo reacts to genuine market conditions. Only stdlib — keeps the runtime's
dependency surface to exactly what the controller already needs.
"""
import json
import urllib.request

PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/{}"


class LiveFeed:
    """Polls one Solana pool. Returns price in the QUOTE token (priceNative),
    which is the unit the LP band works in, plus the base mint and fee/liq context."""

    def __init__(self, pool_address, timeout=15):
        self.pool_address = pool_address
        self.timeout = timeout
        self._last = None  # last good reading, so a transient blip holds price instead of crashing

    def fetch(self):
        url = PAIR_URL.format(self.pool_address)
        req = urllib.request.Request(url, headers={"User-Agent": "valtgeist-podrunner/0.1"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.load(r)
        pairs = data.get("pairs") or []
        if not pairs:
            if self._last:
                return self._last
            raise RuntimeError(f"DexScreener returned no pair for pool {self.pool_address}")
        p = pairs[0]
        price = float(p.get("priceNative") or 0)
        if price <= 0:
            if self._last:
                return self._last
            raise RuntimeError("pool returned a non-positive price")
        base = p.get("baseToken", {}) or {}
        quote = p.get("quoteToken", {}) or {}
        info = {
            "price": price,
            "base_mint": base.get("address", ""),
            "symbol": f"{base.get('symbol', '?')}-{quote.get('symbol', '?')}",
            "dex": p.get("dexId", "?"),
            "liquidity_usd": (p.get("liquidity") or {}).get("usd"),
        }
        self._last = info
        return info
