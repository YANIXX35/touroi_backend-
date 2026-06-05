import os
from flask import Blueprint, request, jsonify
from database import get_db
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
    teams = conn.execute(
        "SELECT * FROM teams WHERE validated = 1 ORDER BY name"
    ).fetchall()

    result = []
    for t in teams:
        players = conn.execute(
            "SELECT player_name FROM players WHERE team_id = ?", (t["id"],)
        ).fetchall()
        result.append({
            "id": t["id"],
            "name": t["name"],
            "captain_name": t["captain_name"],
            "phone": t["phone"],
            "logo_path": t["logo_path"],
            "created_at": t["created_at"],
            "players": [p["player_name"] for p in players],
        })

    conn.close()
    return jsonify(result)


@teams_bp.route("/api/teams/<int:team_id>", methods=["GET"])
def get_team(team_id):
    conn = get_db()
    team = conn.execute(
        "SELECT * FROM teams WHERE id = ? AND validated = 1", (team_id,)
    ).fetchone()

    if not team:
        conn.close()
        return jsonify({"error": "Équipe non trouvée"}), 404

    players = conn.execute(
        "SELECT player_name FROM players WHERE team_id = ?", (team_id,)
    ).fetchall()

    conn.close()
    return jsonify({
        **dict(team),
        "players": [p["player_name"] for p in players],
    })


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

    existing = conn.execute(
        "SELECT id FROM teams WHERE name = ?", (name,)
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "Ce nom d'équipe est déjà pris"}), 409

    cursor = conn.execute(
        "INSERT INTO teams (name, captain_name, phone, logo_path) VALUES (?, ?, ?, ?)",
        (name, captain_name, phone, logo_path),
    )
    team_id = cursor.lastrowid

    for player_name in players:
        name_clean = player_name.strip() if isinstance(player_name, str) else player_name.get("player_name", "").strip()
        if name_clean:
            conn.execute(
                "INSERT INTO players (team_id, player_name) VALUES (?, ?)",
                (team_id, name_clean),
            )

    conn.commit()

    team_row = conn.execute(
        "SELECT * FROM teams WHERE id = ?", (team_id,)
    ).fetchone()
    conn.close()

    send_registration_email(dict(team_row), players)

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
