from flask import Blueprint, request, jsonify
from database import get_db, get_cursor
from config import JWT_SECRET_KEY, JWT_EXPIRATION_HOURS
import bcrypt
import jwt
from datetime import datetime, timedelta
from functools import wraps
from threading import Lock

admin_bp = Blueprint("admin", __name__)

# ─── Rate limiting (brute-force protection) ────────────────────────────────
_attempts: dict = {}  # ip -> {"count": int, "locked_until": datetime|None}
_lock = Lock()
_MAX_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15


def _check_rate_limit(ip: str):
    """Returns (allowed: bool, error_msg: str)."""
    with _lock:
        now = datetime.utcnow()
        entry = _attempts.get(ip)
        if entry:
            locked_until = entry.get("locked_until")
            if locked_until and now < locked_until:
                remaining = int((locked_until - now).total_seconds() / 60) + 1
                return False, f"Trop de tentatives. Réessayez dans {remaining} minute(s)."
            if locked_until and now >= locked_until:
                _attempts[ip] = {"count": 0, "locked_until": None}
        return True, ""


def _record_failure(ip: str):
    with _lock:
        now = datetime.utcnow()
        entry = _attempts.get(ip, {"count": 0, "locked_until": None})
        count = entry["count"] + 1
        locked_until = now + timedelta(minutes=_LOCKOUT_MINUTES) if count >= _MAX_ATTEMPTS else None
        _attempts[ip] = {"count": count, "locked_until": locked_until}


def _clear_attempts(ip: str):
    with _lock:
        _attempts.pop(ip, None)


# ─── JWT auth decorator ────────────────────────────────────────────────────

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"error": "Token manquant"}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
            username = payload.get("sub", "")
            conn = get_db()
            cur = get_cursor(conn)
            cur.execute("SELECT id FROM admins WHERE username = %s", (username,))
            admin = cur.fetchone()
            cur.close()
            conn.close()
            if not admin:
                return jsonify({"error": "Utilisateur invalide"}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Session expirée, reconnectez-vous"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token invalide"}), 401
        return f(*args, **kwargs)
    return decorated


# ─── Authentification ──────────────────────────────────────────────────────

@admin_bp.route("/api/admin/login", methods=["POST"])
def login():
    ip = request.remote_addr or "unknown"

    allowed, err_msg = _check_rate_limit(ip)
    if not allowed:
        return jsonify({"error": err_msg}), 429

    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Identifiants manquants"}), 400

    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("SELECT * FROM admins WHERE username = %s", (username,))
    admin = cur.fetchone()
    cur.close()
    conn.close()

    if not admin or not bcrypt.checkpw(password.encode("utf-8"), admin["password_hash"].encode("utf-8")):
        _record_failure(ip)
        return jsonify({"error": "Identifiants incorrects"}), 401

    _clear_attempts(ip)
    token = jwt.encode(
        {
            "sub": admin["username"],
            "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        },
        JWT_SECRET_KEY,
        algorithm="HS256",
    )
    return jsonify({"token": token, "username": admin["username"]})


# ─── Gestion des équipes ───────────────────────────────────────────────────

@admin_bp.route("/api/admin/teams", methods=["GET"])
@token_required
def admin_get_teams():
    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("""
        SELECT t.id, t.name, t.captain_name, t.phone, t.logo_path, t.created_at, t.validated,
               p.id AS player_id, p.player_name, p.photo_path
        FROM teams t
        LEFT JOIN players p ON p.team_id = t.id
        ORDER BY t.created_at DESC, p.id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

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
                "validated": row["validated"],
                "players": [],
            }
        if row["player_id"]:
            teams_map[tid]["players"].append({
                "id": row["player_id"],
                "player_name": row["player_name"],
                "photo_path": row["photo_path"],
            })

    return jsonify(list(teams_map.values()))


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


# ─── Gestion des matchs ────────────────────────────────────────────────────

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
