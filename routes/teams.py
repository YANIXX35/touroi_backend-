import io
import base64
from flask import Blueprint, request, jsonify
from database import db_conn, get_cursor
from email_service import send_registration_email
from config import ALLOWED_EXTENSIONS, MAX_PHOTO_SIZE
from cache import get as cache_get, set as cache_set, invalidate as cache_invalidate
from PIL import Image

teams_bp = Blueprint("teams", __name__)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@teams_bp.route("/api/teams", methods=["GET"])
def get_teams():
    cached = cache_get("teams_list")
    if cached is not None:
        return jsonify(cached)
    try:
        with db_conn() as conn:
            cur = get_cursor(conn)
            cur.execute("""
                SELECT t.id, t.name, t.captain_name, t.phone, t.logo_path, t.created_at,
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
                    "logo_path": row["logo_path"],
                    "created_at": str(row["created_at"]) if row["created_at"] else None,
                    "players": [],
                }
            if row["player_name"]:
                teams_map[tid]["players"].append(row["player_name"])
        result = list(teams_map.values())
        cache_set("teams_list", result, ttl_seconds=300)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        result = {
            "id": first["id"],
            "name": first["name"],
            "captain_name": first["captain_name"],
            "phone": first["phone"],
            "logo_path": first["logo_path"],
            "created_at": str(first["created_at"]) if first["created_at"] else None,
            "players": [r["player_name"] for r in rows if r["player_name"]],
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@teams_bp.route("/api/register", methods=["POST"])
def register_team():
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
