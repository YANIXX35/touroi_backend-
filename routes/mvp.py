from flask import Blueprint, request, jsonify
from database import db_conn, get_cursor
from config import JWT_SECRET_KEY
import jwt as pyjwt
import os

mvp_bp = Blueprint("mvp", __name__)

# ─── Candidats fixes : les 4 capitaines des demi-finalistes ──────────────────
API_BASE = os.environ.get("API_BASE_URL", "https://touroi-backend.onrender.com")

CANDIDATES = [
    {
        "player_id":   412,
        "player_name": "Mohamed",
        "team_name":   "Futur Pro",
        "role":        "Capitaine",
        "photo_url":   f"{API_BASE}/api/players/412/photo",
    },
    {
        "player_id":   703,
        "player_name": "Gnombre Élise",
        "team_name":   "Universel Foot",
        "role":        "Capitaine",
        "photo_url":   f"{API_BASE}/api/players/703/photo",
    },
    {
        "player_id":   678,
        "player_name": "JEREMIE",
        "team_name":   "Les Enfants s'Amusent",
        "role":        "Capitaine",
        "photo_url":   f"{API_BASE}/api/players/678/photo",
        "is_mvp_winner": True,
    },
    {
        "player_id":   563,
        "player_name": "OUDRAOGO Ousmane",
        "team_name":   "FC Colombie",
        "role":        "Capitaine",
        "photo_url":   f"{API_BASE}/api/players/563/photo",
    },
]

_VALID_KEYS = {
    (c["player_name"].lower(), c["team_name"].lower()) for c in CANDIDATES
}


@mvp_bp.route("/api/mvp", methods=["GET"])
def get_mvp():
    voter_ip = request.remote_addr or "unknown"

    with db_conn() as conn:
        cur = get_cursor(conn)

        # Nombre de votes par candidat
        cur.execute("""
            SELECT LOWER(player_name) AS pn, LOWER(team_name) AS tn, COUNT(*) AS cnt
            FROM mvp_votes
            GROUP BY LOWER(player_name), LOWER(team_name)
        """)
        vote_map = {(r["pn"], r["tn"]): int(r["cnt"]) for r in cur.fetchall()}

        # Vote de cet IP
        cur.execute(
            "SELECT player_name, team_name FROM mvp_votes WHERE voter_ip = %s",
            (voter_ip,),
        )
        voted_row = cur.fetchone()
        cur.close()

    total_votes = sum(vote_map.values())

    result = []
    for c in CANDIDATES:
        key    = (c["player_name"].lower(), c["team_name"].lower())
        votes  = vote_map.get(key, 0)
        pct    = round((votes / total_votes * 100) if total_votes else 0, 1)
        result.append({**c, "votes": votes, "percentage": pct})

    result.sort(key=lambda x: x["votes"], reverse=True)

    return jsonify({
        "candidates":  result,
        "total_votes": total_votes,
        "user_voted":  voted_row is not None,
        "user_vote":   dict(voted_row) if voted_row else None,
    })


@mvp_bp.route("/api/mvp/vote", methods=["POST"])
def cast_vote():
    voter_ip    = request.remote_addr or "unknown"
    data        = request.get_json(silent=True) or {}
    player_name = (data.get("player_name") or "").strip()
    team_name   = (data.get("team_name")   or "").strip()

    if not player_name or not team_name:
        return jsonify({"error": "Candidat invalide"}), 400

    if (player_name.lower(), team_name.lower()) not in _VALID_KEYS:
        return jsonify({"error": "Ce joueur ne fait pas partie des candidats MVP"}), 400

    with db_conn() as conn:
        cur = get_cursor(conn)

        cur.execute("SELECT id FROM mvp_votes WHERE voter_ip = %s", (voter_ip,))
        if cur.fetchone():
            cur.close()
            return jsonify({"error": "Vous avez déjà voté !"}), 409

        cur.execute(
            "INSERT INTO mvp_votes (player_name, team_name, voter_ip) VALUES (%s, %s, %s)",
            (player_name, team_name, voter_ip),
        )
        conn.commit()
        cur.close()

    return jsonify({"success": True, "message": "Vote enregistré ! Merci !"})


@mvp_bp.route("/api/mvp/reset", methods=["DELETE"])
def reset_votes():
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    try:
        pyjwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
    except Exception:
        return jsonify({"error": "Non autorisé"}), 401

    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("DELETE FROM mvp_votes")
        deleted = cur.rowcount
        conn.commit()
        cur.close()

    return jsonify({"success": True, "deleted": deleted})
