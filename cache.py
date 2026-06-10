"""
Cache Redis (partage entre workers) avec fallback memoire.
Si REDIS_URL est defini, utilise Redis. Sinon, cache en memoire local.
"""
import os
import time
import json
from threading import Lock

# ─── Tentative de connexion Redis ─────────────────────────────────────────────
_redis = None
try:
    import redis as _redis_lib
    _url = os.environ.get("REDIS_URL")
    if _url:
        _redis = _redis_lib.from_url(_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        _redis.ping()
        print("[cache] Redis connecte OK")
except Exception as _e:
    print(f"[cache] Redis indisponible, cache memoire actif ({_e})")
    _redis = None

# ─── Cache memoire (fallback) ──────────────────────────────────────────────────
_store: dict = {}
_lock = Lock()


def get(key: str):
    if _redis:
        try:
            raw = _redis.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception:
            pass
    with _lock:
        entry = _store.get(key)
        if entry and time.time() < entry["expires"]:
            return entry["value"]
        return None


def set(key: str, value, ttl_seconds: int = 60):
    if _redis:
        try:
            _redis.setex(key, ttl_seconds, json.dumps(value, default=str))
            return
        except Exception:
            pass
    with _lock:
        _store[key] = {"value": value, "expires": time.time() + ttl_seconds}


def invalidate(key: str):
    if _redis:
        try:
            _redis.delete(key)
            return
        except Exception:
            pass
    with _lock:
        _store.pop(key, None)


def invalidate_prefix(prefix: str):
    if _redis:
        try:
            keys = _redis.keys(f"{prefix}*")
            if keys:
                _redis.delete(*keys)
            return
        except Exception:
            pass
    with _lock:
        to_delete = [k for k in _store if k.startswith(prefix)]
        for k in to_delete:
            del _store[k]


def invalidate_all():
    if _redis:
        try:
            _redis.flushdb()
            return
        except Exception:
            pass
    with _lock:
        _store.clear()
