"""
Cache memoire simple avec TTL (Time To Live).
Evite de refaire les memes requetes SQL a chaque visite.
"""
import time
from threading import Lock

_store: dict = {}
_lock = Lock()


def get(key: str):
    """Retourne la valeur en cache si elle n'a pas expire, sinon None."""
    with _lock:
        entry = _store.get(key)
        if entry and time.time() < entry["expires"]:
            return entry["value"]
        return None


def set(key: str, value, ttl_seconds: int = 60):
    """Met en cache une valeur pour ttl_seconds secondes."""
    with _lock:
        _store[key] = {"value": value, "expires": time.time() + ttl_seconds}


def invalidate(key: str):
    """Supprime une cle du cache (apres une modification)."""
    with _lock:
        _store.pop(key, None)


def invalidate_prefix(prefix: str):
    """Supprime toutes les cles dont le nom commence par prefix."""
    with _lock:
        to_delete = [k for k in _store if k.startswith(prefix)]
        for k in to_delete:
            del _store[k]


def invalidate_all():
    """Vide tout le cache."""
    with _lock:
        _store.clear()
