"""Hard limits from the handout plus our own tighter soft limits."""
import os
import time

HARD_CALLS = 10            # API requests incl. retries
HARD_COMPLETION = 30_000   # completion tokens per case
HARD_SECONDS = 600         # wall clock per case


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Budget:
    def __init__(self, t0):
        self.t0 = t0
        self.max_calls = min(_env_int("P2P_MAX_CALLS", 5), HARD_CALLS)
        self.max_completion = min(_env_int("P2P_MAX_COMPLETION", 14_000), HARD_COMPLETION)
        # leave headroom for checks, render and process exit
        self.deadline_s = min(_env_int("P2P_DEADLINE_S", 420), HARD_SECONDS - 60)
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.cached_tokens = 0

    # --- accounting -------------------------------------------------------
    def elapsed(self):
        return time.monotonic() - self.t0

    def remaining_s(self):
        return self.deadline_s - self.elapsed()

    def remaining_completion(self):
        return self.max_completion - self.completion_tokens

    def record_call(self):
        self.calls += 1

    def record_usage(self, usage):
        usage = usage or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        det = usage.get("completion_tokens_details") or {}
        self.reasoning_tokens += int(det.get("reasoning_tokens") or 0)
        pdet = usage.get("prompt_tokens_details") or {}
        self.cached_tokens += int(pdet.get("cached_tokens") or 0)

    # --- gates --------------------------------------------------------------
    def can_call(self, min_tokens=800, min_seconds=25, reserve_calls=0):
        """reserve_calls: keep this many calls for later stages (e.g. emergency)."""
        if self.calls + 1 + reserve_calls > self.max_calls:
            return False, "call budget exhausted"
        if self.remaining_completion() < min_tokens:
            return False, "completion token budget exhausted"
        if self.remaining_s() < min_seconds:
            return False, "time budget exhausted"
        return True, "ok"

    def max_tokens_for(self, wanted):
        return max(256, min(wanted, self.remaining_completion()))

    def timeout_for(self, wanted=90):
        return max(10, min(wanted, self.remaining_s() - 5))

    def summary(self):
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "elapsed_s": round(self.elapsed(), 3),
        }
