"""Realistic execution-latency model for paper mode.

Paper mode is only useful if it lies about nothing that matters. On Solana the
things that hurt a market maker are not the strategy — they're the plumbing:

  * data staleness   — the price you act on is already old (RPC/indexer lag + poll)
  * action latency   — decision -> tx landed on-chain (slots + confirmation)
  * congestion       — during a rug EVERYONE transacts; priority-fee wars make your
                       tx SLOWER and MORE LIKELY TO FAIL exactly when you need to flee
  * failed txs       — a rebalance/close that doesn't land; you're stuck another cycle

This model produces jittery, congestion-aware values for all four, tunable by env,
so a paper pod's panic-flatten faces the same headwind a live one would. Defaults are
deliberately conservative Solana-ish numbers; raise them to stress-test.

Env:
  LAT_STALENESS / LAT_STALENESS_JITTER   base + jitter seconds of price staleness
  LAT_RPC                                base RPC round-trip seconds
  LAT_ACTION / LAT_ACTION_JITTER         base + jitter seconds for a tx to land
  LAT_CONGESTION_MULT                    latency multiplier while the market is cascading
  LAT_FAIL_PCT / LAT_CONGESTION_FAIL_MULT tx-drop probability, and how much worse under congestion
"""
import os
import random


def _f(name, default):
    return float(os.environ.get(name, default))


class LatencyModel:
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.staleness = _f("LAT_STALENESS", "3.0")
        self.staleness_jitter = _f("LAT_STALENESS_JITTER", "1.5")
        self.rpc = _f("LAT_RPC", "0.15")
        self.action = _f("LAT_ACTION", "1.5")
        self.action_jitter = _f("LAT_ACTION_JITTER", "1.0")
        self.congestion_mult = _f("LAT_CONGESTION_MULT", "3.0")
        self.fail_pct = _f("LAT_FAIL_PCT", "0.05")
        self.congestion_fail_mult = _f("LAT_CONGESTION_FAIL_MULT", "4.0")
        self.drops = 0  # count of tx that failed to land this session

    def sample_staleness(self):
        return max(0.0, self.staleness + self.rng.uniform(-self.staleness_jitter, self.staleness_jitter))

    def sample_rpc(self):
        return max(0.0, self.rng.gauss(self.rpc, self.rpc * 0.4))

    def action_latency(self, congested=False):
        # base land time + a one-sided jitter tail (slow blocks happen, fast ones don't cancel)
        base = max(0.2, self.rng.gauss(self.action, self.action_jitter * 0.4)
                   + abs(self.rng.gauss(0.0, self.action_jitter)))
        return base * (self.congestion_mult if congested else 1.0)

    def dropped(self, congested=False):
        """True if a tx fails to land this attempt (must be re-issued next cycle)."""
        p = self.fail_pct * (self.congestion_fail_mult if congested else 1.0)
        hit = self.rng.random() < min(0.9, p)
        if hit:
            self.drops += 1
        return hit
