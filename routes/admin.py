from flask import Blueprint, request, jsonify
from database import get_db, get_cursor
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
    cur = get_cursor(conn)
    cur.execute("SELECT * FROM admins WHERE username = %s", (username,))
    admin = cur.fetchone()
    cur.close()
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
    cur = get_cursor(conn)

    cur.execute("SELECT * FROM teams ORDER BY created_at DESC")
    teams = cur.fetchall()

    result = []
    for t in teams:
        cur.execute("SELECT * FROM players WHERE team_id = %s", (t["id"],))
        players = cur.fetchall()
        row = dict(t)
        row["created_at"] = str(row["created_at"]) if row["created_at"] else None
        row["players"] = [dict(p) for p in players]
        result.append(row)

    cur.close()
    conn.close()
    return jsonify(result)


@admin_bp.route("/api/admin/teams/<int:team_id>", methods=["PUT"])
@token_required
def admin_update_team(team_id):
    data = request.get_json()
    validated = data.get("validated")

    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(
        "UPDATE teams SET validated = %s WHERE id = %s",
        (1 if validated else 0, team_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"message": "Équipe mise à jour"})


@admin_bp.route("/api/admin/teams/<int:team_id>", methods=["DELETE"])
@token_required
def admin_delete_team(team_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("DELETE FROM teams WHERE id = %s", (team_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"message": "Équipe supprimée"})


# --- Gestion des matchs ---

@admin_bp.route("/api/admin/matches", methods=["POST"])
@token_required
def admin_create_match():
    data = request.get_json()

    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(
        """INSERT INTO matches
           (team1_id, team2_id, team1_name, team2_name, match_date, match_time, phase, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'upcoming')
           RETURNING id""",
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
    match_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
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
            fields.append(f"{field} = %s")
            values.append(data[field])

    if not fields:
        return jsonify({"error": "Aucune donnée à modifier"}), 400

    values.append(match_id)
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(f"UPDATE matches SET {', '.join(fields)} WHERE id = %s", values)
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"message": "Match mis à jour"})


@admin_bp.route("/api/admin/matches/<int:match_id>", methods=["DELETE"])
@token_required
def admin_delete_match(match_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("DELETE FROM matches WHERE id = %s", (match_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"message": "Match supprimé"})
