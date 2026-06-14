import os
import json
import logging
import urllib.request
import urllib.error
from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
from threading import Lock

chat_bp = Blueprint("chat", __name__)
_logger = logging.getLogger("chat")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = "gemini-2.5-flash"
GEMINI_URL     = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

SYSTEM_PROMPT = """Tu es l'assistant officiel du Tournoi de Football de l'Étoile Universelle de Grand Bassam 2026.
Tu es amical, enthousiaste et passionné de football. Réponds TOUJOURS en français.
Ce que tu sais :
- Organisateur : Étoile Universelle de Grand Bassam, Côte d'Ivoire
- Lieu des demi-finales et finale : Terrain Arena, Grand Bassam
- Phases du tournoi : Tour 1 → Tour 2 → Tour 3 → Demi-finale → Finale
- Demi-finales prévues le 20 Juin 2026 : FC Futur Pro vs Universel Foot | Les Enfants s'Amusent vs FC Colombie
- Pour les scores en direct, le classement ou le planning, invite l'utilisateur à consulter les pages du site (Matchs, Résultats, Buteurs)
- Tu ne peux pas accéder aux données en temps réel du site
Garde tes réponses courtes et percutantes (2-4 phrases max). Utilise des emojis football avec modération."""

# ─── Rate limiting (sliding window, in-memory) ────────────────────────────────
_requests: dict = {}
_lock = Lock()
_MAX_PER_MINUTE = 5


def _check_rate_limit(ip: str) -> tuple[bool, str]:
    with _lock:
        now = datetime.utcnow()
        cutoff = now - timedelta(minutes=1)
        timestamps = [t for t in _requests.get(ip, []) if t > cutoff]
        if len(timestamps) >= _MAX_PER_MINUTE:
            return False, "Trop de messages envoyés. Patientez une minute avant de réessayer."
        timestamps.append(now)
        _requests[ip] = timestamps
        return True, ""


# ─── Route ────────────────────────────────────────────────────────────────────

@chat_bp.route("/api/chat", methods=["POST"])
def chat():
    if not GEMINI_API_KEY:
        return jsonify({"error": "Assistant non configuré"}), 503

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
            "maxOutputTokens": 350,
            "temperature": 0.75,
        },
    }

    try:
        body = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        candidates = result.get("candidates", [])
        if not candidates:
            _logger.warning("Gemini: no candidates in response")
            return jsonify({"error": "Aucune réponse générée"}), 502

        text = candidates[0]["content"]["parts"][0]["text"]
        return jsonify({"reply": text.strip()})

    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        _logger.error("Gemini HTTP %d: %s", e.code, raw[:300])
        if e.code == 429:
            return jsonify({"error": "L'assistant est momentanément surchargé. Réessayez dans quelques secondes."}), 429
        if e.code in (400, 401, 403):
            return jsonify({"error": "Clé API invalide ou non autorisée."}), 502
        return jsonify({"error": "Assistant temporairement indisponible"}), 502
    except Exception as e:
        _logger.error("Gemini error: %s", e)
        return jsonify({"error": "Assistant temporairement indisponible"}), 502
