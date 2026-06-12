import io
import base64
import threading
from flask import Blueprint, request, jsonify, Response, send_from_directory
from database import db_conn, get_cursor
from email_service import send_registration_email
from config import ALLOWED_EXTENSIONS, MAX_PHOTO_SIZE, UPLOAD_FOLDER, REGISTRATION_DEADLINE
from datetime import datetime
from cache import invalidate as cache_invalidate
import cache as _cache
from PIL import Image

teams_bp = Blueprint("teams", __name__)

# Cache mémoire pour les logos décodés — chargés à la demande, max 25 entrées.
_logo_cache: dict = {}   # {team_id: {"bytes": bytes, "mime": str} | None}
_logo_lock = threading.Lock()
_LOGO_CACHE_MAX = 25     # limite pour éviter la saturation mémoire


def _logo_cache_set(tid, value):
    """Insère dans le cache logos en évictant la plus ancienne entrée si plein."""
    with _logo_lock:
        if tid not in _logo_cache and len(_logo_cache) >= _LOGO_CACHE_MAX:
            _logo_cache.pop(next(iter(_logo_cache)), None)
        _logo_cache[tid] = value


def _prewarm_logos():
    """Charge tous les logos validés en mémoire au démarrage du worker."""
    try:
        with db_conn() as conn:
            cur = get_cursor(conn)
            cur.execute(
                "SELECT id, logo_path FROM teams "
                "WHERE validated = 1 AND logo_path IS NOT NULL AND logo_path != ''"
            )
            rows = cur.fetchall()
            cur.close()
        for row in rows:
            tid = row["id"]
            logo = row["logo_path"]
            if logo.startswith("data:"):
                header, b64data = logo.split(",", 1)
                mime = header.split(":")[1].split(";")[0]
                img_bytes = base64.b64decode(b64data)
                with _logo_lock:
                    _logo_cache[tid] = {"bytes": img_bytes, "mime": mime}
    except Exception:
        pass


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ─── Fetch (module-level, réutilisable pour le pré-chauffe) ──────────────────

def _fetch_teams_list():
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("""
            SELECT t.id, t.name, t.captain_name, t.phone,
                   t.logo_path IS NOT NULL AND t.logo_path != '' AS has_logo,
                   t.created_at,
                   p.player_name
            FROM teams t
            LEFT JOIN players p ON p.team_id = t.id
            WHERE t.validated = 1
            ORDER BY t.name, p.id
        """)
        rows = cur.fetchall()
        cur.close()

    teams_map: dict = {}
    for row in rows:
        tid = row["id"]
        if tid not in teams_map:
            teams_map[tid] = {
                "id": tid,
                "name": row["name"],
                "captain_name": row["captain_name"],
                "phone": row["phone"],
                # URL légère au lieu du base64 — le navigateur charge l'image à la demande
                "logo_path": f"/api/teams/{tid}/logo" if row["has_logo"] else None,
                "created_at": str(row["created_at"]) if row["created_at"] else None,
                "players": [],
            }
        if row["player_name"]:
            teams_map[tid]["players"].append(row["player_name"])
    return list(teams_map.values())


# ─── Routes publiques ─────────────────────────────────────────────────────────

@teams_bp.route("/api/teams", methods=["GET"])
def get_teams():
    try:
        result = _cache.get_or_compute("teams_list", _fetch_teams_list, ttl_seconds=300)
        if result is None:
            return jsonify({"error": "Service temporairement indisponible"}), 503
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@teams_bp.route("/api/teams/<int:team_id>/logo", methods=["GET"])
def get_team_logo(team_id):
    """Sert le logo d'une équipe comme image avec cache navigateur 7 jours.
    Sert depuis la mémoire après le premier accès — aucune requête DB."""
    # Chemin rapide : cache mémoire (pas de DB, pas de décodage base64)
    with _logo_lock:
        if team_id in _logo_cache:
            entry = _logo_cache[team_id]
            if entry is None:
                return "", 404
            resp = Response(entry["bytes"], mimetype=entry["mime"])
            resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
            return resp

    # Premier accès : charger depuis la DB et mettre en cache
    try:
        with db_conn() as conn:
            cur = get_cursor(conn)
            cur.execute("SELECT logo_path FROM teams WHERE id = %s", (team_id,))
            row = cur.fetchone()
            cur.close()
    except Exception:
        return "", 503

    if not row or not row["logo_path"]:
        _logo_cache_set(team_id, None)
        return "", 404

    logo = row["logo_path"]
    try:
        if logo.startswith("data:"):
            header, b64data = logo.split(",", 1)
            mime = header.split(":")[1].split(";")[0]
            img_bytes = base64.b64decode(b64data)
            entry = {"bytes": img_bytes, "mime": mime}
            _logo_cache_set(team_id, entry)
            resp = Response(img_bytes, mimetype=mime)
        else:
            # Logo stocké comme fichier (legacy) — le fichier n'existe plus
            # sur le filesystem éphémère de Render : marquer comme absent
            _logo_cache_set(team_id, None)
            return "", 404
        resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return resp
    except Exception:
        _logo_cache_set(team_id, None)
        return "", 404


@teams_bp.route("/api/teams/<int:team_id>", methods=["GET"])
def get_team(team_id):
    try:
        with db_conn() as conn:
            cur = get_cursor(conn)
            cur.execute("""
                SELECT t.id, t.name, t.captain_name, t.phone, t.logo_path, t.created_at,
                       p.player_name
                FROM teams t
                LEFT JOIN players p ON p.team_id = t.id
                WHERE t.id = %s AND t.validated = 1
                ORDER BY p.id
            """, (team_id,))
            rows = cur.fetchall()
            cur.close()

        if not rows:
            return jsonify({"error": "Équipe non trouvée"}), 404
        first = rows[0]
        return jsonify({
            "id": first["id"],
            "name": first["name"],
            "captain_name": first["captain_name"],
            "phone": first["phone"],
            "logo_path": first["logo_path"],
            "created_at": str(first["created_at"]) if first["created_at"] else None,
            "players": [r["player_name"] for r in rows if r["player_name"]],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@teams_bp.route("/api/registration-status", methods=["GET"])
def registration_status():
    is_open = datetime.utcnow() < REGISTRATION_DEADLINE
    return jsonify({
        "open": is_open,
        "deadline": REGISTRATION_DEADLINE.isoformat(),
        "message": "" if is_open else "Les inscriptions sont définitivement fermées depuis le 12 juin 2026 à 22h00.",
    })


@teams_bp.route("/api/register", methods=["POST"])
def register_team():
    if datetime.utcnow() >= REGISTRATION_DEADLINE:
        return jsonify({
            "error": "Les inscriptions sont fermées depuis le 12 juin 2026 à 22h00. Le tournoi commence le 13 juin."
        }), 403

    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "Données manquantes"}), 400

        name = (data.get("name") or "").strip()
        captain_name = (data.get("captain_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        logo_path = data.get("logo_path") or None
        players = data.get("players") or []

        if not name or not captain_name or not phone:
            return jsonify({"error": "Nom, capitaine et téléphone sont obligatoires"}), 400
        if len(name) > 50:
            return jsonify({"error": "Le nom de l'équipe ne doit pas dépasser 50 caractères"}), 400
        if len(captain_name) > 80:
            return jsonify({"error": "Le nom du capitaine ne doit pas dépasser 80 caractères"}), 400
        if len(players) == 0:
            return jsonify({"error": "Ajoutez au moins un joueur"}), 400

        with db_conn() as conn:
            cur = get_cursor(conn)

            cur.execute("SELECT id FROM teams WHERE name = %s", (name,))
            if cur.fetchone():
                cur.close()
                return jsonify({"error": "Ce nom d'équipe est déjà pris"}), 409

            cur.execute("SELECT id FROM teams WHERE phone = %s", (phone,))
            if cur.fetchone():
                cur.close()
                return jsonify({"error": "Ce numéro de téléphone est déjà utilisé pour une inscription. Chaque équipe doit avoir un numéro unique."}), 409

            cur.execute(
                "INSERT INTO teams (name, captain_name, phone, logo_path) VALUES (%s, %s, %s, %s) RETURNING id",
                (name, captain_name, phone, logo_path),
            )
            team_id = cur.fetchone()["id"]

            for player in players:
                if isinstance(player, str):
                    name_clean = player.strip()
                    photo_path = None
                else:
                    name_clean = (player.get("player_name") or "").strip()
                    photo_path = player.get("photo_path") or None
                if name_clean:
                    if len(name_clean) > 50:
                        cur.close()
                        return jsonify({"error": f"Nom de joueur trop long (50 car. max) : {name_clean[:30]}"}), 400
                    cur.execute(
                        "INSERT INTO players (team_id, player_name, photo_path) VALUES (%s, %s, %s)",
                        (team_id, name_clean, photo_path),
                    )

            conn.commit()
            cache_invalidate("teams_list")

            cur.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
            team_row = dict(cur.fetchone())
            team_row["created_at"] = str(team_row["created_at"]) if team_row["created_at"] else None
            cur.close()

        import threading
        threading.Thread(
            target=send_registration_email,
            args=(team_row, list(players)),
            daemon=True,
        ).start()

        return jsonify({"message": "Inscription réussie ! En attente de validation.", "team_id": team_id}), 201

    except Exception as e:
        return jsonify({"error": f"Erreur serveur : {str(e)}"}), 500


@teams_bp.route("/api/uploads/logo", methods=["POST"])
def upload_logo():
    try:
        if "logo" not in request.files:
            return jsonify({"error": "Aucun fichier envoyé"}), 400
        file = request.files["logo"]
        if file.filename == "" or not allowed_file(file.filename):
            return jsonify({"error": "Format non autorisé (jpg, png, webp)"}), 400
        img = Image.open(file)
        img.thumbnail(MAX_PHOTO_SIZE)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=65)
        data_url = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")
        return jsonify({"logo_path": data_url}), 201
    except Exception as e:
        return jsonify({"error": f"Erreur upload : {str(e)}"}), 500
