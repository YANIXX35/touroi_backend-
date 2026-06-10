"""
Cache mémoire avec TTL et request coalescing.

Request coalescing : si 50 utilisateurs demandent la même clé simultanément
et que le cache est vide, une seule requête DB est exécutée — les 49 autres
attendent son résultat au lieu de frapper PostgreSQL chacune de leur côté.
"""
import time
from threading import Lock, Event

_store: dict = {}
_lock = Lock()
_inflight: dict = {}   # clé -> Event (requête DB en cours)


def get(key: str):
    with _lock:
        entry = _store.get(key)
        if entry and time.time() < entry["expires"]:
            return entry["value"]
    return None


def set(key: str, value, ttl_seconds: int = 60):
    with _lock:
        _store[key] = {"value": value, "expires": time.time() + ttl_seconds}


def invalidate(key: str):
    with _lock:
        _store.pop(key, None)
        _inflight.pop(key, None)


def invalidate_prefix(prefix: str):
    with _lock:
        for k in list(_store.keys()):
            if k.startswith(prefix):
                del _store[k]


def invalidate_all():
    with _lock:
        _store.clear()


def get_or_compute(key: str, fn, ttl_seconds: int = 60):
    """
    Retourne la valeur en cache, ou exécute fn() une seule fois.
    Les appels concurrents sur la même clé attendent le premier résultat
    plutôt que d'interroger PostgreSQL chacun séparément.

    Raises l'exception de fn() pour le thread "fetcher".
    Les threads "waiter" reçoivent None si le fetcher a échoué.
    """
    # Chemin rapide : cache chaud (sans verrou global)
    val = get(key)
    if val is not None:
        return val

    # Vérification sous verrou + enregistrement du vol
    with _lock:
        entry = _store.get(key)
        if entry and time.time() < entry["expires"]:
            return entry["value"]

        evt = _inflight.get(key)
        if evt is not None:
            is_fetcher = False
            wait_evt = evt
        else:
            wait_evt = Event()
            _inflight[key] = wait_evt
            is_fetcher = True

    if not is_fetcher:
        # Attendre que le fetcher finisse (max 25 s — dépasse le timeout HTTP des routes)
        wait_evt.wait(timeout=25.0)
        return get(key)   # None si le fetcher a échoué

    # Ce thread est le fetcher désigné
    try:
        value = fn()
        set(key, value, ttl_seconds)
        return value
    except Exception:
        raise
    finally:
        with _lock:
            _inflight.pop(key, None)
        wait_evt.set()   # débloquer les waiters même en cas d'erreur
