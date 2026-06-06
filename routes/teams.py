import os
from flask import Blueprint, request, jsonify
from database import get_db, get_cursor
from email_service import send_registration_email
from config import UPLOAD_FOLDER, ALLOWED_EXTENSIONS, MAX_PHOTO_SIZE
from PIL import Image
import uuid

teams_bp = Blueprint("teams", __name__)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@teams_bp.route("/api/teams", methods=["GET"])
def get_teams():
    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("SELECT * FROM teams WHERE validated = 1 ORDER BY name")
    teams = cur.fetchall()

    result = []
    for t in teams:
        cur.execute("SELECT player_name FROM players WHERE team_id = %s", (t["id"],))
        players = cur.fetchall()
        result.append({
            "id": t["id"],
            "name": t["name"],
            "captain_name": t["captain_name"],
            "phone": t["phone"],
            "logo_path": t["logo_path"],
            "created_at": str(t["created_at"]) if t["created_at"] else None,
            "players": [p["player_name"] for p in players],
        })

    cur.close()
    conn.close()
    return jsonify(result)


@teams_bp.route("/api/teams/<int:team_id>", methods=["GET"])
def get_team(team_id):
    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("SELECT * FROM teams WHERE id = %s AND validated = 1", (team_id,))
    team = cur.fetchone()

    if not team:
        cur.close()
        conn.close()
        return jsonify({"error": "Équipe non trouvée"}), 404

    cur.execute("SELECT player_name FROM players WHERE team_id = %s", (team_id,))
    players = cur.fetchall()

    result = dict(team)
    result["created_at"] = str(result["created_at"]) if result["created_at"] else None
    result["players"] = [p["player_name"] for p in players]

    cur.close()
    conn.close()
    return jsonify(result)


@teams_bp.route("/api/register", methods=["POST"])
def register_team():
    data = request.get_json()

    if not data:
        return jsonify({"error": "Données manquantes"}), 400

    name = data.get("name", "").strip()
    captain_name = data.get("captain_name", "").strip()
    phone = data.get("phone", "").strip()
    logo_path = data.get("logo_path", None)
    players = data.get("players", [])

    if not name or not captain_name or not phone:
        return jsonify({"error": "Nom, capitaine et téléphone sont obligatoires"}), 400

    if len(players) == 0:
        return jsonify({"error": "Ajoutez au moins un joueur"}), 400

    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("SELECT id FROM teams WHERE name = %s", (name,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({"error": "Ce nom d'équipe est déjà pris"}), 409

    cur.execute(
        "INSERT INTO teams (name, captain_name, phone, logo_path) VALUES (%s, %s, %s, %s) RETURNING id",
        (name, captain_name, phone, logo_path),
    )
    team_id = cur.fetchone()["id"]

    for player_name in players:
        name_clean = player_name.strip() if isinstance(player_name, str) else player_name.get("player_name", "").strip()
        if name_clean:
            cur.execute(
                "INSERT INTO players (team_id, player_name) VALUES (%s, %s)",
                (team_id, name_clean),
            )

    conn.commit()

    cur.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
    team_row = dict(cur.fetchone())
    team_row["created_at"] = str(team_row["created_at"]) if team_row["created_at"] else None

    cur.close()
    conn.close()

    send_registration_email(team_row, players)

    return jsonify({"message": "Inscription réussie ! En attente de validation.", "team_id": team_id}), 201


@teams_bp.route("/api/uploads/logo", methods=["POST"])
def upload_logo():
    if "logo" not in request.files:
        return jsonify({"error": "Aucun fichier envoyé"}), 400

    file = request.files["logo"]
    if file.filename == "" or not allowed_file(file.filename):
        return jsonify({"error": "Format non autorisé (jpg, png, webp)"}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"logo_{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    img = Image.open(file)
    img.thumbnail(MAX_PHOTO_SIZE)
    img.save(filepath)

    return jsonify({"logo_path": filename}), 201
