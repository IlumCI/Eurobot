"""$0 push-feed: Solana RPC websocket `accountSubscribe` on pool reserve vaults.

The free-tier replacement for Geyser/ShredStream: same vault-watching design as
the Rust GeyserFeed (price = quote/base for CPMM venues; a base-vault delta IS a
trade with sign and size), but transported over plain RPC websockets that free
keyless endpoints serve.

RACING: we hold one subscription per endpoint on EVERY free endpoint at once and
apply whichever copy of each update lands first (per-vault slot guard drops the
stragglers; identical duplicates are naturally inert since no delta -> no event).
Whoever is fastest right now wins, and any endpoint dying is a non-event. This is
the same trick HFT shops use across exchange feeds — free latency + redundancy.

CLMM/DLMM pools (price_capable=False from vault_discovery) yield trades but no
price — concentrated liquidity breaks the reserve-ratio math; Jupiter stays the
price source for those.

Usage:
    from vault_discovery import discover_for_pool
    from ws_feed import WsVaultFeed
    feed = WsVaultFeed(); feed.start()
    feed.watch_pool("MYPOOL", discover_for_pool(pool_addr))
    feed.latest("MYPOOL")        # -> (price|None, slot, age_ms) | None
    feed.drain_events("MYPOOL")  # -> [(ts_ms, side ±1, base_size_ui, price|0.0)]
"""
import asyncio
import base64
import json
import os
import threading
import time
from collections import deque

# Free keyless websocket endpoints, all raced concurrently. Add a personal free-tier
# endpoint (Helius/QuickNode — email signup, no card) via SOLANA_WS and it simply joins
# the race; if it's faster it wins, if it throttles the public ones carry on.
# (drpc deliberately absent: its websocket requires an API key.)
WS_CANDIDATES = [u for u in [os.environ.get("SOLANA_WS")] if u] + [
    "wss://api.mainnet-beta.solana.com",
    "wss://solana-rpc.publicnode.com",
]
MAX_EVENTS = 4096


class _Pool:
    def __init__(self, info):
        self.base_vault = info["base_vault"]
        self.quote_vault = info["quote_vault"]
        self.base_dec = info["base_decimals"]
        self.quote_dec = info["quote_decimals"]
        self.price_capable = info.get("price_capable", True)
        self.base_amt = None
        self.quote_amt = None
        self.price = None
        self.slot = 0
        self.last_ms = 0.0
        self.events = deque(maxlen=MAX_EVENTS)
        self.vault_slots = {}  # vault -> newest slot applied (race stale-guard)


class WsVaultFeed:
    def __init__(self):
        self._pools = {}          # pool_id -> _Pool
        self._vaults = {}         # vault addr -> (pool_id, is_base)
        self._lock = threading.Lock()
        self._gen = 0             # bumped on watch/unwatch -> (re)subscribe
        self.connected = 0        # number of endpoints currently connected
        self.messages = 0
        self.reconnects = 0
        self.wins = {}            # endpoint -> updates it delivered FIRST (race scoreboard)
        self._started = False

    # ---------------------------------------------------------------- public
    def watch_pool(self, pool_id, info):
        with self._lock:
            p = _Pool(info)
            self._pools[pool_id] = p
            self._vaults[p.base_vault] = (pool_id, True)
            self._vaults[p.quote_vault] = (pool_id, False)
            self._gen += 1

    def unwatch_pool(self, pool_id):
        with self._lock:
            p = self._pools.pop(pool_id, None)
            if p:
                self._vaults.pop(p.base_vault, None)
                self._vaults.pop(p.quote_vault, None)
                self._gen += 1

    def latest(self, pool_id):
        with self._lock:
            p = self._pools.get(pool_id)
            if not p or not p.last_ms:
                return None
            return (p.price, p.slot, time.time() * 1000 - p.last_ms)

    def drain_events(self, pool_id):
        with self._lock:
            p = self._pools.get(pool_id)
            if not p:
                return []
            out = list(p.events)
            p.events.clear()
            return out

    def stats(self):
        with self._lock:
            return (self.connected, self.messages, self.reconnects, len(self._pools), dict(self.wins))

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._run, name="ws-vault-feed", daemon=True).start()

    # --------------------------------------------------------------- internals
    def _apply(self, vault, data_b64, slot, source):
        raw = base64.b64decode(data_b64)
        if len(raw) < 72:
            return
        amount = int.from_bytes(raw[64:72], "little")
        now_ms = time.time() * 1000
        with self._lock:
            hit = self._vaults.get(vault)
            if not hit:
                return
            pool_id, is_base = hit
            p = self._pools[pool_id]
            # RACE GUARD: a slower endpoint delivering an older slot must not rewind state
            # (that would fabricate a reverse trade). Same-slot repeats with the same amount
            # are inert (no delta); same-slot different amounts are later intra-slot states.
            if slot < p.vault_slots.get(vault, 0):
                return
            was_new = slot > p.vault_slots.get(vault, 0)
            p.vault_slots[vault] = slot
            prev_base = p.base_amt
            prev_amt = p.base_amt if is_base else p.quote_amt
            if is_base:
                p.base_amt = amount
            else:
                p.quote_amt = amount
            if was_new or prev_amt != amount:  # count only informative updates on the scoreboard
                self.wins[source] = self.wins.get(source, 0) + 1
            p.slot, p.last_ms = slot, now_ms
            if p.base_amt and p.quote_amt and p.price_capable:
                p.price = (p.quote_amt / 10 ** p.quote_dec) / (p.base_amt / 10 ** p.base_dec)
            # one event per swap: react to BASE-vault deltas only (quote leg would double-count)
            if is_base and prev_base is not None and prev_base != amount:
                delta_ui = (amount - prev_base) / 10 ** p.base_dec
                p.events.append((now_ms, 1 if delta_ui < 0 else -1, abs(delta_ui), p.price or 0.0))

    def _run(self):
        asyncio.run(self._loop())

    async def _loop(self):
        # RACE: one independent, self-reconnecting subscriber per endpoint, all at once.
        await asyncio.gather(*[self._endpoint_loop(url) for url in WS_CANDIDATES])

    async def _endpoint_loop(self, url):
        import websockets
        short = url.split("//")[1].split("/")[0]
        backoff = 1
        while True:
            t_up = 0.0
            try:
                async with websockets.connect(url, ping_interval=20, max_size=None,
                                              user_agent_header="valtgeist-podrunner/0.3") as ws:
                    with self._lock:
                        self.connected += 1
                    t_up = time.time()
                    try:
                        await self._session(ws, short)
                    finally:
                        with self._lock:
                            self.connected -= 1
            except Exception as e:
                self.reconnects += 1
                print(f"[ws-feed] {short}: {type(e).__name__}; retrying in {backoff}s", flush=True)
            # reset backoff only after a session that actually LASTED — an endpoint that
            # accepts the handshake then instantly drops must still back off, not spam.
            if t_up and time.time() - t_up > 10:
                backoff = 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _session(self, ws, source):
        sub_ids = {}      # rpc request id -> vault addr
        sub_map = {}      # subscription id -> vault addr
        vault_sub = {}    # vault addr -> subscription id (needed to unsubscribe on rotation)
        subscribed = set()
        seen_gen = -1
        next_id = 1
        bad_frames = 0
        while True:
            # (re)subscribe any vaults added since last check — rotation-friendly
            with self._lock:
                gen_now, want = self._gen, dict(self._vaults)
            if gen_now != seen_gen:
                for vault in want:
                    if vault in subscribed:
                        continue
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": next_id, "method": "accountSubscribe",
                        "params": [vault, {"encoding": "base64", "commitment": "processed"}],
                    }))
                    sub_ids[next_id] = vault
                    subscribed.add(vault)
                    next_id += 1
                # UNSUBSCRIBE vaults that rotation dropped: public RPCs cap subscriptions per
                # socket, so leaking dead subs eventually gets the connection throttled/closed
                # (periodic feed blackouts under a churning fleet).
                for vault in list(subscribed - set(want)):
                    subscribed.discard(vault)
                    sid = vault_sub.pop(vault, None)
                    if sid is not None:
                        sub_map.pop(sid, None)
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": next_id,
                            "method": "accountUnsubscribe", "params": [sid],
                        }))
                        next_id += 1
                seen_gen = gen_now
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
            except asyncio.TimeoutError:
                continue
            self.messages += 1
            # one malformed/unexpected frame must NOT kill the socket — tearing down the
            # session on a stray KeyError meant reconnect+resubscribe churn all night
            # (api.mainnet-beta sends odd frames under throttling). Skip the frame, log rarely.
            try:
                if "id" in msg and msg["id"] in sub_ids:          # subscribe confirmation
                    if "result" in msg:
                        vault = sub_ids[msg["id"]]
                        if vault in subscribed:
                            sub_map[msg["result"]] = vault
                            vault_sub[vault] = msg["result"]
                        else:
                            # confirmation for a vault we already dropped -> unsubscribe right away
                            await ws.send(json.dumps({
                                "jsonrpc": "2.0", "id": next_id,
                                "method": "accountUnsubscribe", "params": [msg["result"]],
                            }))
                            next_id += 1
                elif msg.get("method") == "accountNotification":
                    pr = msg["params"]
                    vault = sub_map.get(pr["subscription"])
                    if vault:
                        val = pr["result"]["value"]
                        self._apply(vault, val["data"][0], pr["result"]["context"]["slot"], source)
            except (KeyError, TypeError, IndexError) as e:
                bad_frames += 1
                if bad_frames == 1 or bad_frames % 100 == 0:
                    print(f"[ws-feed] {source}: bad frame #{bad_frames} "
                          f"({type(e).__name__} {e}): {str(msg)[:160]}", flush=True)
