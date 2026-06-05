from flask import Blueprint, request, jsonify
from database import get_db
from config import JWT_SECRET_KEY, JWT_EXPIRATION_HOURS
import bcrypt
import jwt
from datetime import datetime, timedelta
from functools import wraps

admin_bp = Blueprint("admin", __name__)


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"error": "Token manquant"}), 401
        try:
            jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Session expirée, reconnectez-vous"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token invalide"}), 401
        return f(*args, **kwargs)
    return decorated


# --- Authentification ---

@admin_bp.route("/api/admin/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username", "")
    password = data.get("password", "")

    conn = get_db()
    admin = conn.execute(
        "SELECT * FROM admins WHERE username = ?", (username,)
    ).fetchone()
    conn.close()

    if not admin or not bcrypt.checkpw(password.encode("utf-8"), admin["password_hash"].encode("utf-8")):
        return jsonify({"error": "Identifiants incorrects"}), 401

    token = jwt.encode(
        {
            "sub": admin["username"],
            "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        },
        JWT_SECRET_KEY,
        algorithm="HS256",
    )
    return jsonify({"token": token, "username": admin["username"]})


# --- Gestion des équipes ---

@admin_bp.route("/api/admin/teams", methods=["GET"])
@token_required
def admin_get_teams():
    conn = get_db()
    teams = conn.execute(
        "SELECT * FROM teams ORDER BY created_at DESC"
    ).fetchall()

    result = []
    for t in teams:
        players = conn.execute(
            "SELECT * FROM players WHERE team_id = ?", (t["id"],)
        ).fetchall()
        result.append({**dict(t), "players": [dict(p) for p in players]})

    conn.close()
    return jsonify(result)


@admin_bp.route("/api/admin/teams/<int:team_id>", methods=["PUT"])
@token_required
def admin_update_team(team_id):
    data = request.get_json()
    validated = data.get("validated")

    conn = get_db()
    conn.execute(
        "UPDATE teams SET validated = ? WHERE id = ?", (1 if validated else 0, team_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Équipe mise à jour"})


@admin_bp.route("/api/admin/teams/<int:team_id>", methods=["DELETE"])
@token_required
def admin_delete_team(team_id):
    conn = get_db()
    conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Équipe supprimée"})


# --- Gestion des matchs ---

@admin_bp.route("/api/admin/matches", methods=["POST"])
@token_required
def admin_create_match():
    data = request.get_json()

    conn = get_db()
    cursor = conn.execute(
        """INSERT INTO matches
           (team1_id, team2_id, team1_name, team2_name, match_date, match_time, phase, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'upcoming')""",
        (
            data.get("team1_id"),
            data.get("team2_id"),
            data.get("team1_name", ""),
            data.get("team2_name", ""),
            data.get("match_date", ""),
            data.get("match_time", ""),
            data.get("phase", "Poule"),
        ),
    )
    match_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return jsonify({"message": "Match créé", "id": match_id}), 201


@admin_bp.route("/api/admin/matches/<int:match_id>", methods=["PUT"])
@token_required
def admin_update_match(match_id):
    data = request.get_json()

    fields = []
    values = []

    for field in ["team1_id", "team2_id", "team1_name", "team2_name",
                  "match_date", "match_time", "phase", "score1", "score2", "status"]:
        if field in data:
            fields.append(f"{field} = ?")
            values.append(data[field])

    if not fields:
        return jsonify({"error": "Aucune donnée à modifier"}), 400

    values.append(match_id)
    conn = get_db()
    conn.execute(f"UPDATE matches SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return jsonify({"message": "Match mis à jour"})


@admin_bp.route("/api/admin/matches/<int:match_id>", methods=["DELETE"])
@token_required
def admin_delete_match(match_id):
    conn = get_db()
    conn.execute("DELETE FROM matches WHERE id = ?", (match_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Match supprimé"})
