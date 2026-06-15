import os
import json
import logging
import urllib.request
import urllib.error
from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
from threading import Lock, Semaphore

chat_bp = Blueprint("chat", __name__)
_logger = logging.getLogger("chat")

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL   = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

SYSTEM_PROMPT = """Tu es un assistant intelligent et polyvalent, disponible sur le site du Tournoi de Football de l'Étoile Universelle de Grand Bassam 2026.
Tu réponds TOUJOURS en français, de manière claire, amicale et utile.
Tu peux répondre à TOUTES les questions, qu'elles soient liées au tournoi, au football, à la géographie, à la culture, aux sciences, ou à n'importe quel autre sujet.

Ce que tu sais sur le tournoi :
- Organisateur : Étoile Universelle de Grand Bassam, Côte d'Ivoire
- Lieu des demi-finales et finale : Terrain Arena, Grand Bassam
- Phases : Tour 1 → Tour 2 → Tour 3 → Demi-finale → Finale
- Demi-finales prévues le 20 Juin 2026 : FC Futur Pro vs Universel Foot | Les Enfants s'Amusent vs FC Colombie
- Pour les scores en direct, le classement ou le planning, invite l'utilisateur à consulter les pages du site (Matchs, Résultats, Buteurs)

Garde tes réponses concises (3-5 phrases max). Utilise des emojis avec modération."""

# ─── Rotation de clés Gemini ──────────────────────────────────────────────────
# Charge toutes les clés disponibles (GEMINI_API_KEY_1 … _5, ou GEMINI_API_KEY)
def _load_keys() -> list[str]:
    keys = []
    for i in range(1, 6):
        k = os.environ.get(f"GEMINI_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    if not keys:
        # compatibilité avec l'ancienne variable
        k = os.environ.get("GEMINI_API_KEY", "").strip()
        if k:
            keys.append(k)
    return keys

GEMINI_KEYS: list[str] = _load_keys()

_key_lock      = Lock()
_key_cooldowns: dict[int, datetime] = {}   # idx → cooldown_until
_key_cursor    = 0                          # dernier index utilisé


def _get_available_key() -> tuple[int, str] | tuple[None, None]:
    """Retourne (index, clé) d'une clé disponible, ou (None, None) si toutes épuisées."""
    global _key_cursor
    with _key_lock:
        now = datetime.utcnow()
        n   = len(GEMINI_KEYS)
        for offset in range(n):
            idx = (_key_cursor + offset) % n
            cd  = _key_cooldowns.get(idx)
            if cd is None or now >= cd:
                _key_cursor = (idx + 1) % n
                return idx, GEMINI_KEYS[idx]
        return None, None


def _mark_key_exhausted(idx: int) -> None:
    """Met la clé en cooldown 62 secondes (légèrement > 1 min de quota Gemini)."""
    with _key_lock:
        _key_cooldowns[idx] = datetime.utcnow() + timedelta(seconds=62)
        _logger.warning("Clé Gemini #%d mise en cooldown 62s", idx)


# ─── Rate limiting par IP (sliding window) ────────────────────────────────────
_requests: dict = {}
_lock = Lock()
_MAX_PER_MINUTE = 8


def _check_rate_limit(ip: str) -> tuple[bool, str]:
    with _lock:
        now    = datetime.utcnow()
        cutoff = now - timedelta(minutes=1)
        timestamps = [t for t in _requests.get(ip, []) if t > cutoff]
        if len(timestamps) >= _MAX_PER_MINUTE:
            return False, "Trop de messages envoyés. Patientez une minute avant de réessayer."
        timestamps.append(now)
        _requests[ip] = timestamps
        return True, ""


# ─── Sémaphore global ─────────────────────────────────────────────────────────
_gemini_sem = Semaphore(10)   # jusqu'à 10 appels Gemini simultanés (1 par clé)
_SEM_TIMEOUT = 15


# ─── Route ────────────────────────────────────────────────────────────────────

@chat_bp.route("/api/chat", methods=["POST"])
def chat():
    if not GEMINI_KEYS:
        return jsonify({"error": "Assistant non configuré (aucune clé API)"}), 503

    ip = request.remote_addr or "unknown"
    allowed, err = _check_rate_limit(ip)
    if not allowed:
        return jsonify({"error": err}), 429

    data    = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []

    if not message:
        return jsonify({"error": "Message vide"}), 400
    if len(message) > 500:
        return jsonify({"error": "Message trop long (500 caractères max)"}), 400

    # Construire le contexte de conversation (6 derniers messages max)
    contents = []
    for h in history[-6:]:
        role = h.get("role", "user")
        text = (h.get("text") or "").strip()
        if role in ("user", "model") and text:
            contents.append({"role": role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 700,
            "temperature": 0.75,
        },
    }

    # ── File d'attente ────────────────────────────────────────────────────────
    acquired = _gemini_sem.acquire(blocking=True, timeout=_SEM_TIMEOUT)
    if not acquired:
        return jsonify({
            "error": "L'assistant est très sollicité en ce moment. Réessayez dans quelques secondes 🙏"
        }), 503

    try:
        body = json.dumps(payload).encode("utf-8")

        # ── Rotation automatique des clés ─────────────────────────────────────
        # On essaie toutes les clés disponibles avant d'abandonner
        last_error = None
        for _attempt in range(len(GEMINI_KEYS)):
            key_idx, api_key = _get_available_key()
            if api_key is None:
                return jsonify({
                    "error": "L'assistant est momentanément surchargé. Réessayez dans une minute ⏳"
                }), 503

            req = urllib.request.Request(
                f"{GEMINI_URL}?key={api_key}",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                candidates = result.get("candidates", [])
                if not candidates:
                    _logger.warning("Gemini clé #%d : aucun candidat", key_idx)
                    return jsonify({"error": "Aucune réponse générée"}), 502

                text = candidates[0]["content"]["parts"][0]["text"]
                return jsonify({"reply": text.strip()})

            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", errors="replace")
                _logger.error("Gemini clé #%d HTTP %d: %s", key_idx, e.code, raw[:200])

                if e.code == 429:
                    # Cette clé est épuisée → cooldown et on essaie la suivante
                    _mark_key_exhausted(key_idx)
                    last_error = "429"
                    continue   # prochain tour de boucle = prochaine clé

                if e.code in (400, 401, 403):
                    return jsonify({"error": "Clé API invalide ou non autorisée."}), 502
                return jsonify({"error": "Assistant temporairement indisponible"}), 502

            except Exception as e:
                _logger.error("Gemini clé #%d erreur: %s", key_idx, e)
                last_error = str(e)
                break

        # Toutes les clés ont renvoyé 429
        if last_error == "429":
            return jsonify({
                "error": "L'assistant est momentanément surchargé. Réessayez dans une minute ⏳"
            }), 503
        return jsonify({"error": "Assistant temporairement indisponible"}), 502

    finally:
        _gemini_sem.release()
