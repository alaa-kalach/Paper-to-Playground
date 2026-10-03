"""JSONL trace writer: one event per line, flushed immediately (survives crashes)."""
import json
import os
import time

_SECRET_KEYS = {"authorization", "api_key", "apikey", "key", "reasoning", "reasoning_details"}


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items() if str(k).lower() not in _SECRET_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    if isinstance(obj, str):
        key = os.environ.get("OPENROUTER_API_KEY")
        if key and key in obj:
            obj = obj.replace(key, "***")
        return obj[:4000]
    return obj


class Trace:
    def __init__(self, path, t0=None):
        self.t0 = t0 if t0 is not None else time.monotonic()
        self.path = path
        self.f = open(path, "w", encoding="utf-8")
        self.n = 0

    def elapsed(self):
        return round(time.monotonic() - self.t0, 3)

    def event(self, stage, action, result, **extra):
        rec = {"seq": self.n, "t": self.elapsed(), "stage": stage, "action": action, "result": result}
        rec.update(_scrub(extra))
        self.f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.f.flush()
        self.n += 1
        return rec

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass
