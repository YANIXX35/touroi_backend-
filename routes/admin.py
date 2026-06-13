from flask import Blueprint, request, jsonify
from database import db_conn, get_cursor
from config import JWT_SECRET_KEY, JWT_EXPIRATION_HOURS, UPLOAD_FOLDER, ALLOWED_EXTENSIONS, MAX_PHOTO_SIZE
from cache import invalidate as cache_invalidate
import bcrypt
import jwt
from datetime import datetime, timedelta
from functools import wraps
from threading import Lock
import os
import io
import base64

admin_bp = Blueprint("admin", __name__)

# ─── Rate limiting ─────────────────────────────────────────────────────────────
_attempts: dict = {}
_lock = Lock()
_MAX_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15


def _check_rate_limit(ip: str):
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


# ─── JWT auth decorator ────────────────────────────────────────────────────────

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"error": "Token manquant"}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
            username = payload.get("sub", "")
            with db_conn() as conn:
                cur = get_cursor(conn)
                cur.execute("SELECT id FROM admins WHERE username = %s", (username,))
                admin = cur.fetchone()
                cur.close()
            if not admin:
                return jsonify({"error": "Utilisateur invalide"}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Session expirée, reconnectez-vous"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token invalide"}), 401
        return f(*args, **kwargs)
    return decorated


# ─── Authentification ──────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/refresh", methods=["POST"])
@token_required
def refresh_token():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        username = payload.get("sub", "")
        new_token = jwt.encode(
            {"sub": username, "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)},
            JWT_SECRET_KEY, algorithm="HS256",
        )
        return jsonify({"token": new_token})
    except Exception:
        return jsonify({"error": "Token invalide"}), 401


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

    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("SELECT * FROM admins WHERE username = %s", (username,))
        admin = cur.fetchone()
        cur.close()

    if not admin or not bcrypt.checkpw(password.encode("utf-8"), admin["password_hash"].encode("utf-8")):
        _record_failure(ip)
        return jsonify({"error": "Identifiants incorrects"}), 401

    _clear_attempts(ip)
    token = jwt.encode(
        {"sub": admin["username"], "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)},
        JWT_SECRET_KEY, algorithm="HS256",
    )
    return jsonify({"token": token, "username": admin["username"]})


# ─── Équipes ───────────────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/teams", methods=["GET"])
@token_required
def admin_get_teams():
    include_photos = request.args.get("photos", "0") == "1"
    with db_conn() as conn:
        cur = get_cursor(conn)
        if include_photos:
            cur.execute("""
                SELECT t.id, t.name, t.captain_name, t.phone, t.logo_path, t.created_at, t.validated,
                       p.id AS player_id, p.player_name, p.photo_path
                FROM teams t
                LEFT JOIN players p ON p.team_id = t.id
                ORDER BY t.created_at DESC, p.id
            """)
        else:
            cur.execute("""
                SELECT t.id, t.name, t.captain_name, t.phone, t.logo_path, t.created_at, t.validated,
                       p.id AS player_id, p.player_name,
                       p.photo_path IS NOT NULL AND p.photo_path != '' AS has_photo
                FROM teams t
                LEFT JOIN players p ON p.team_id = t.id
                ORDER BY t.created_at DESC, p.id
            """)
        rows = cur.fetchall()
        cur.close()

    teams_map: dict = {}
    for row in rows:
        tid = row["id"]
        if tid not in teams_map:
            teams_map[tid] = {
                "id": tid, "name": row["name"], "captain_name": row["captain_name"],
                "phone": row["phone"], "logo_path": row["logo_path"],
                "created_at": str(row["created_at"]) if row["created_at"] else None,
                "validated": row["validated"], "players": [],
            }
        if row["player_id"]:
            if include_photos:
                teams_map[tid]["players"].append({
                    "id": row["player_id"], "player_name": row["player_name"], "photo_path": row["photo_path"],
                })
            else:
                teams_map[tid]["players"].append({
                    "id": row["player_id"], "player_name": row["player_name"],
                    "photo_path": None, "has_photo": bool(row["has_photo"]),
                })
    return jsonify(list(teams_map.values()))


@admin_bp.route("/api/admin/teams/<int:team_id>", methods=["PUT"])
@token_required
def admin_update_team(team_id):
    data = request.get_json()
    validated = data.get("validated")
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("UPDATE teams SET validated = %s WHERE id = %s", (1 if validated else 0, team_id))
        conn.commit()
        cur.close()
    cache_invalidate("teams_list")
    return jsonify({"message": "Équipe mise à jour"})


@admin_bp.route("/api/admin/teams/<int:team_id>", methods=["DELETE"])
@token_required
def admin_delete_team(team_id):
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("DELETE FROM teams WHERE id = %s", (team_id,))
        conn.commit()
        cur.close()
    cache_invalidate("teams_list")
    return jsonify({"message": "Équipe supprimée"})


# ─── Matchs ────────────────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/matches", methods=["POST"])
@token_required
def admin_create_match():
    data = request.get_json()
    t1 = (data.get("team1_name") or "").strip()
    t2 = (data.get("team2_name") or "").strip()
    match_date = (data.get("match_date") or "").strip()
    match_time = (data.get("match_time") or "").strip()
    if not t1 or not t2:
        return jsonify({"error": "Les noms des deux équipes sont requis"}), 400

    terrain = (data.get("terrain") or "").strip() or None
    status  = data.get("status", "upcoming")
    if status not in ("upcoming", "ongoing", "finished"):
        status = "upcoming"

    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute(
            """SELECT id FROM matches
               WHERE LOWER(team1_name) = LOWER(%s) AND LOWER(team2_name) = LOWER(%s)
                 AND match_date = %s AND match_time = %s""",
            (t1, t2, match_date, match_time),
        )
        if cur.fetchone():
            cur.close()
            return jsonify({"error": "Ce match existe déjà (mêmes équipes, même date et heure)"}), 409
        # Numéro automatique : MAX existant + 1
        cur.execute("SELECT COALESCE(MAX(match_number), 0) + 1 AS next_num FROM matches")
        next_num = cur.fetchone()["next_num"]
        cur.execute(
            """INSERT INTO matches
               (team1_id, team2_id, team1_name, team2_name, match_date, match_time, phase, status, terrain, match_number)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (data.get("team1_id"), data.get("team2_id"), t1, t2, match_date, match_time,
             data.get("phase", "Poule"), status, terrain, next_num),
        )
        match_id = cur.fetchone()["id"]
        conn.commit()
        cur.close()
    cache_invalidate("matches_list")
    cache_invalidate("results")
    return jsonify({"message": "Match créé", "id": match_id}), 201


@admin_bp.route("/api/admin/matches/<int:match_id>", methods=["PUT"])
@token_required
def admin_update_match(match_id):
    data = request.get_json()
    fields, values = [], []
    for field in ["team1_id", "team2_id", "team1_name", "team2_name",
                  "match_date", "match_time", "phase", "score1", "score2", "status", "terrain", "match_number"]:
        if field in data:
            fields.append(f"{field} = %s")
            values.append(data[field])
    if not fields:
        return jsonify({"error": "Aucune donnée à modifier"}), 400
    values.append(match_id)
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute(f"UPDATE matches SET {', '.join(fields)} WHERE id = %s", values)
        conn.commit()
        cur.close()
    cache_invalidate("matches_list")
    cache_invalidate("results")
    return jsonify({"message": "Match mis à jour"})


@admin_bp.route("/api/admin/matches/<int:match_id>", methods=["DELETE"])
@token_required
def admin_delete_match(match_id):
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("DELETE FROM matches WHERE id = %s", (match_id,))
        conn.commit()
        cur.close()
    cache_invalidate("matches_list")
    cache_invalidate("results")
    cache_invalidate("top_scorers")
    return jsonify({"message": "Match supprimé"})


# ─── Bulk status (page secrète /yk) ───────────────────────────────────────────

@admin_bp.route("/api/bulk-status", methods=["POST"])
def bulk_status_update():
    data = request.get_json(silent=True) or {}
    if data.get("key") != "YK2026":
        return jsonify({"error": "Non autorisé"}), 403
    status = data.get("status")
    if status not in ("upcoming", "ongoing", "finished"):
        return jsonify({"error": "Statut invalide"}), 400
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("UPDATE matches SET status = %s", (status,))
        count = cur.rowcount
        conn.commit()
        cur.close()
    cache_invalidate("matches_list")
    cache_invalidate("results")
    return jsonify({"message": f"{count} matchs mis à jour", "count": count})


@admin_bp.route("/api/match-create-yk", methods=["POST"])
def match_create_yk():
    data = request.get_json(silent=True) or {}
    if data.get("key") != "YK2026":
        return jsonify({"error": "Non autorisé"}), 403
    t1_name = (data.get("team1_name") or "").strip()
    t2_name = (data.get("team2_name") or "").strip()
    if not t1_name or not t2_name:
        return jsonify({"error": "Équipes requises"}), 400
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("SELECT id FROM teams WHERE LOWER(name) = LOWER(%s)", (t1_name,))
        t1 = cur.fetchone()
        cur.execute("SELECT id FROM teams WHERE LOWER(name) = LOWER(%s)", (t2_name,))
        t2 = cur.fetchone()
        cur.execute("SELECT COALESCE(MAX(match_number), 0) + 1 AS next_num FROM matches")
        next_num = cur.fetchone()["next_num"]
        cur.execute(
            """INSERT INTO matches
               (team1_id, team2_id, team1_name, team2_name, match_date, match_time, phase, status, terrain, match_number)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'upcoming',%s,%s) RETURNING id""",
            (t1["id"] if t1 else None, t2["id"] if t2 else None,
             t1_name, t2_name,
             data.get("match_date") or None,
             data.get("match_time") or None,
             data.get("phase", "Tour 2"),
             data.get("terrain") or None,
             next_num)
        )
        match_id = cur.fetchone()["id"]
        conn.commit()
        cur.close()
    cache_invalidate("matches_list")
    cache_invalidate("results")
    return jsonify({"message": "Match créé", "id": match_id}), 201


@admin_bp.route("/api/team-create-yk", methods=["POST"])
def team_create_yk():
    """Crée une équipe stub avec nom + logo (clé YK, pas de JWT requis)."""
    data = request.get_json(silent=True) or {}
    if data.get("key") != "YK2026":
        return jsonify({"error": "Non autorisé"}), 403
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name requis"}), 400
    logo_path = data.get("logo_path") or None
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("SELECT id FROM teams WHERE LOWER(name) = LOWER(%s)", (name,))
        existing = cur.fetchone()
        if existing:
            # L'equipe existe deja : on met juste a jour le logo si fourni
            if logo_path:
                cur.execute("UPDATE teams SET logo_path = %s WHERE id = %s", (logo_path, existing["id"]))
                conn.commit()
            cur.close()
            return jsonify({"message": "Equipe existante mise à jour", "id": existing["id"]})
        cur.execute(
            "INSERT INTO teams (name, captain_name, phone, logo_path, validated) VALUES (%s,%s,%s,%s,1) RETURNING id",
            (name, "—", "0000000000", logo_path)
        )
        team_id = cur.fetchone()["id"]
        conn.commit()
        cur.close()
    cache_invalidate("teams_list")
    return jsonify({"message": "Equipe créée", "id": team_id}), 201


@admin_bp.route("/api/team-update-yk", methods=["POST"])
def team_update_yk():
    """Met à jour le nom et/ou le logo d'une équipe (clé YK, pas de JWT requis)."""
    data = request.get_json(silent=True) or {}
    if data.get("key") != "YK2026":
        return jsonify({"error": "Non autorisé"}), 403
    team_id = data.get("team_id")
    if not team_id:
        return jsonify({"error": "team_id requis"}), 400
    fields, values = [], []
    if "name" in data and data["name"]:
        fields.append("name = %s")
        values.append(data["name"].strip())
    if "logo_path" in data and data["logo_path"]:
        fields.append("logo_path = %s")
        values.append(data["logo_path"])
    if not fields:
        return jsonify({"error": "Aucune donnée"}), 400
    values.append(team_id)
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute(f"UPDATE teams SET {', '.join(fields)} WHERE id = %s", values)
        conn.commit()
        cur.close()
    cache_invalidate("teams_list")
    return jsonify({"message": "Équipe mise à jour"})


@admin_bp.route("/api/match-quick-update", methods=["POST"])
def match_quick_update():
    data = request.get_json(silent=True) or {}
    if data.get("key") != "YK2026":
        return jsonify({"error": "Non autorisé"}), 403
    match_id = data.get("match_id")
    if not match_id:
        return jsonify({"error": "match_id requis"}), 400
    fields, values = [], []
    for field in ["score1", "score2", "status"]:
        if field in data:
            fields.append(f"{field} = %s")
            values.append(data[field])
    if not fields:
        return jsonify({"error": "Aucune donnée"}), 400
    values.append(match_id)
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute(f"UPDATE matches SET {', '.join(fields)} WHERE id = %s", values)
        conn.commit()
        cur.close()
    cache_invalidate("matches_list")
    cache_invalidate("results")
    return jsonify({"message": "Match mis à jour"})


# ─── Joueurs ───────────────────────────────────────────────────────────────────

def _allowed_photo(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@admin_bp.route("/api/admin/teams/<int:team_id>/players", methods=["POST"])
@token_required
def admin_add_player(team_id):
    data = request.get_json() or {}
    player_name = (data.get("player_name") or "").strip()
    photo_path = data.get("photo_path") or None
    if not player_name:
        return jsonify({"error": "Nom requis"}), 400
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute(
            "INSERT INTO players (team_id, player_name, photo_path) VALUES (%s, %s, %s) RETURNING id",
            (team_id, player_name, photo_path),
        )
        player_id = cur.fetchone()["id"]
        conn.commit()
        cur.close()
    cache_invalidate("teams_list")
    return jsonify({"id": player_id, "player_name": player_name, "photo_path": photo_path}), 201


@admin_bp.route("/api/admin/players/<int:player_id>", methods=["PUT"])
@token_required
def admin_update_player(player_id):
    data = request.get_json() or {}
    player_name = (data.get("player_name") or "").strip()
    if not player_name:
        return jsonify({"error": "Nom requis"}), 400
    with db_conn() as conn:
        cur = get_cursor(conn)
        if "photo_path" in data:
            cur.execute(
                "UPDATE players SET player_name = %s, photo_path = %s WHERE id = %s",
                (player_name, data["photo_path"], player_id),
            )
        else:
            cur.execute("UPDATE players SET player_name = %s WHERE id = %s", (player_name, player_id))
        conn.commit()
        cur.close()
    cache_invalidate("teams_list")
    return jsonify({"message": "Joueur mis à jour"})


@admin_bp.route("/api/admin/players/<int:player_id>", methods=["DELETE"])
@token_required
def admin_delete_player(player_id):
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("DELETE FROM players WHERE id = %s", (player_id,))
        conn.commit()
        cur.close()
    cache_invalidate("teams_list")
    return jsonify({"message": "Joueur supprimé"})


@admin_bp.route("/api/admin/uploads/photo", methods=["POST"])
@token_required
def admin_upload_photo():
    try:
        if "photo" not in request.files:
            return jsonify({"error": "Aucun fichier"}), 400
        file = request.files["photo"]
        if not file.filename or not _allowed_photo(file.filename):
            return jsonify({"error": "Format non autorisé (jpg, png, webp)"}), 400
        from PIL import Image
        img = Image.open(file)
        img.thumbnail(MAX_PHOTO_SIZE)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=65)
        data_url = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")
        return jsonify({"photo_path": data_url}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Buteurs & Passeurs ────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/matches/<int:match_id>/goals", methods=["GET"])
@token_required
def get_match_goals(match_id):
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("SELECT * FROM goals WHERE match_id = %s ORDER BY minute NULLS LAST, id", (match_id,))
        goals = [dict(g) for g in cur.fetchall()]
        cur.close()
    return jsonify(goals)


@admin_bp.route("/api/admin/matches/<int:match_id>/goals", methods=["POST"])
@token_required
def add_goal(match_id):
    data = request.get_json() or {}
    player_name = (data.get("player_name") or "").strip()
    team_name   = (data.get("team_name") or "").strip()
    type_       = data.get("type", "goal")
    minute      = data.get("minute") or None
    if not player_name or not team_name:
        return jsonify({"error": "Nom du joueur et équipe requis"}), 400
    if type_ not in ("goal", "assist"):
        return jsonify({"error": "Type invalide (goal ou assist)"}), 400
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute(
            "INSERT INTO goals (match_id, player_name, team_name, type, minute) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (match_id, player_name, team_name, type_, minute),
        )
        goal_id = cur.fetchone()["id"]
        conn.commit()
        cur.close()
    cache_invalidate("top_scorers")
    return jsonify({"id": goal_id, "match_id": match_id, "player_name": player_name,
                    "team_name": team_name, "type": type_, "minute": minute}), 201


@admin_bp.route("/api/admin/goals/<int:goal_id>", methods=["PUT"])
@token_required
def update_goal(goal_id):
    data = request.get_json() or {}
    player_name = (data.get("player_name") or "").strip()
    team_name   = (data.get("team_name") or "").strip()
    type_       = data.get("type", "goal")
    minute      = data.get("minute") or None
    if not player_name or not team_name:
        return jsonify({"error": "Nom du joueur et équipe requis"}), 400
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute(
            "UPDATE goals SET player_name=%s, team_name=%s, type=%s, minute=%s WHERE id=%s",
            (player_name, team_name, type_, minute, goal_id),
        )
        conn.commit()
        cur.close()
    cache_invalidate("top_scorers")
    return jsonify({"message": "But/Passe mis à jour"})


@admin_bp.route("/api/admin/goals/<int:goal_id>", methods=["DELETE"])
@token_required
def delete_goal(goal_id):
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("DELETE FROM goals WHERE id=%s", (goal_id,))
        conn.commit()
        cur.close()
    cache_invalidate("top_scorers")
    return jsonify({"message": "Supprimé"})


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _get_username():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"]).get("sub", "admin")
    except Exception:
        return "admin"


def _log(action: str, details: str = ""):
    try:
        username = _get_username()
        with db_conn() as conn:
            cur = get_cursor(conn)
            cur.execute(
                "INSERT INTO admin_logs (username, action, details) VALUES (%s, %s, %s)",
                (username, action, (details or "")[:500])
            )
            conn.commit()
            cur.close()
    except Exception:
        pass


# ─── Galerie ───────────────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/gallery", methods=["GET"])
@token_required
def admin_get_gallery():
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("SELECT id, title, photo_path, created_at FROM gallery ORDER BY created_at DESC")
        rows = [{"id": r["id"], "title": r["title"], "photo_path": r["photo_path"],
                 "created_at": str(r["created_at"])} for r in cur.fetchall()]
        cur.close()
    return jsonify(rows)


@admin_bp.route("/api/admin/gallery", methods=["POST"])
@token_required
def admin_add_photo():
    data = request.get_json() or {}
    photo_path = (data.get("photo_path") or "").strip()
    title = (data.get("title") or "").strip()
    if not photo_path:
        return jsonify({"error": "Photo requise"}), 400
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute(
            "INSERT INTO gallery (title, photo_path) VALUES (%s, %s) RETURNING id, created_at",
            (title or None, photo_path)
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
    cache_invalidate("gallery")
    _log("gallery_add", title or "sans titre")
    return jsonify({"id": row["id"], "title": title, "photo_path": photo_path,
                    "created_at": str(row["created_at"])}), 201


@admin_bp.route("/api/admin/gallery/<int:photo_id>", methods=["PATCH"])
@token_required
def admin_update_photo(photo_id):
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip() or None
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("UPDATE gallery SET title=%s WHERE id=%s", (title, photo_id))
        conn.commit()
        cur.close()
    cache_invalidate("gallery")
    _log("gallery_update", f"photo #{photo_id}")
    return jsonify({"id": photo_id, "title": title})


@admin_bp.route("/api/admin/gallery/<int:photo_id>", methods=["DELETE"])
@token_required
def admin_delete_photo(photo_id):
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("DELETE FROM gallery WHERE id=%s", (photo_id,))
        conn.commit()
        cur.close()
    cache_invalidate("gallery")
    _log("gallery_delete", f"photo #{photo_id}")
    return jsonify({"message": "Photo supprimée"})


# ─── Annonces ──────────────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/announcements", methods=["GET"])
@token_required
def admin_get_announcements():
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("SELECT id, title, content, type, active, created_at FROM announcements ORDER BY created_at DESC")
        rows = [{"id": r["id"], "title": r["title"], "content": r["content"],
                 "type": r["type"], "active": r["active"], "created_at": str(r["created_at"])}
                for r in cur.fetchall()]
        cur.close()
    return jsonify(rows)


@admin_bp.route("/api/admin/announcements", methods=["POST"])
@token_required
def admin_add_announcement():
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    type_ = data.get("type", "info")
    if not title or not content:
        return jsonify({"error": "Titre et contenu requis"}), 400
    if type_ not in ("info", "warning", "urgent"):
        type_ = "info"
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute(
            "INSERT INTO announcements (title, content, type) VALUES (%s, %s, %s) RETURNING id, created_at",
            (title, content, type_)
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
    cache_invalidate("announcements")
    _log("announcement_add", title)
    return jsonify({"id": row["id"], "title": title, "content": content,
                    "type": type_, "active": True, "created_at": str(row["created_at"])}), 201


@admin_bp.route("/api/admin/announcements/<int:ann_id>", methods=["PUT"])
@token_required
def admin_update_announcement(ann_id):
    data = request.get_json() or {}
    fields, values = [], []
    for f in ["title", "content", "type"]:
        if f in data:
            fields.append(f"{f} = %s")
            values.append(data[f])
    if "active" in data:
        fields.append("active = %s")
        values.append(bool(data["active"]))
    if not fields:
        return jsonify({"error": "Rien à modifier"}), 400
    values.append(ann_id)
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute(f"UPDATE announcements SET {', '.join(fields)} WHERE id = %s", values)
        conn.commit()
        cur.close()
    cache_invalidate("announcements")
    _log("announcement_update", f"#{ann_id}")
    return jsonify({"message": "Annonce mise à jour"})


@admin_bp.route("/api/admin/announcements/<int:ann_id>", methods=["DELETE"])
@token_required
def admin_delete_announcement(ann_id):
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("DELETE FROM announcements WHERE id=%s", (ann_id,))
        conn.commit()
        cur.close()
    cache_invalidate("announcements")
    _log("announcement_delete", f"#{ann_id}")
    return jsonify({"message": "Annonce supprimée"})


# ─── Logs ──────────────────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/logs", methods=["GET"])
@token_required
def admin_get_logs():
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("SELECT id, username, action, details, created_at FROM admin_logs ORDER BY created_at DESC LIMIT 200")
        rows = [{"id": r["id"], "username": r["username"], "action": r["action"],
                 "details": r["details"], "created_at": str(r["created_at"])} for r in cur.fetchall()]
        cur.close()
    return jsonify(rows)
