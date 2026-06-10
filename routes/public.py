from flask import Blueprint, jsonify, request
from database import get_db, get_cursor
from cache import get as cache_get, set as cache_set


def _strip_photos(data: dict) -> dict:
    """Retire les photos base64 pour le mode lite (connexion lente)."""
    return {
        **data,
        "logo_path": None,
        "players": [{**p, "photo_path": None} for p in data.get("players", [])],
    }

public_bp = Blueprint("public", __name__)


# ─── Galerie ───────────────────────────────────────────────────────────────

@public_bp.route("/api/gallery", methods=["GET"])
def get_gallery():
    cached = cache_get("gallery")
    if cached is not None:
        return jsonify(cached)
    try:
        conn = get_db()
        cur = get_cursor(conn)
        cur.execute("SELECT id, title, photo_path, created_at FROM gallery ORDER BY created_at DESC")
        rows = [{"id": r["id"], "title": r["title"], "photo_path": r["photo_path"],
                 "created_at": str(r["created_at"])} for r in cur.fetchall()]
        cur.close(); conn.close()
        cache_set("gallery", rows, ttl_seconds=300)
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Annonces ──────────────────────────────────────────────────────────────

@public_bp.route("/api/announcements", methods=["GET"])
def get_announcements():
    cached = cache_get("announcements")
    if cached is not None:
        return jsonify(cached)
    try:
        conn = get_db()
        cur = get_cursor(conn)
        cur.execute("""
            SELECT id, title, content, type, created_at
            FROM announcements
            WHERE active = TRUE
            ORDER BY created_at DESC
        """)
        rows = [{"id": r["id"], "title": r["title"], "content": r["content"],
                 "type": r["type"], "created_at": str(r["created_at"])} for r in cur.fetchall()]
        cur.close(); conn.close()
        cache_set("announcements", rows, ttl_seconds=120)
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Détail équipe enrichi ─────────────────────────────────────────────────

@public_bp.route("/api/teams/<int:team_id>/detail", methods=["GET"])
def get_team_detail(team_id):
    lite = request.args.get("lite", "0") == "1"
    cached = cache_get(f"team_detail_{team_id}")
    if cached is not None:
        return jsonify(_strip_photos(cached) if lite else cached)
    try:
        conn = get_db()
        cur = get_cursor(conn)

        # Infos équipe + joueurs avec photos
        cur.execute("""
            SELECT t.id, t.name, t.captain_name, t.phone, t.logo_path, t.created_at,
                   p.id AS player_id, p.player_name, p.photo_path
            FROM teams t
            LEFT JOIN players p ON p.team_id = t.id
            WHERE t.id = %s AND t.validated = 1
            ORDER BY p.id
        """, (team_id,))
        rows = cur.fetchall()
        if not rows:
            cur.close(); conn.close()
            return jsonify({"error": "Équipe non trouvée"}), 404

        first = rows[0]
        team = {
            "id": first["id"],
            "name": first["name"],
            "captain_name": first["captain_name"],
            "logo_path": first["logo_path"],
            "created_at": str(first["created_at"]) if first["created_at"] else None,
            "players": [
                {"id": r["player_id"], "player_name": r["player_name"], "photo_path": r["photo_path"]}
                for r in rows if r["player_id"]
            ],
        }

        # Matchs de l'équipe
        cur.execute("""
            SELECT id, team1_name, team2_name, match_date, match_time, phase,
                   score1, score2, status
            FROM matches
            WHERE LOWER(team1_name) = LOWER(%s) OR LOWER(team2_name) = LOWER(%s)
            ORDER BY match_date, match_time
        """, (team["name"], team["name"]))
        matches = [dict(m) for m in cur.fetchall()]

        # Stats calculées
        played = won = drawn = lost = goals_for = goals_against = 0
        for m in matches:
            if m["status"] != "finished" or m["score1"] is None:
                continue
            is_team1 = m["team1_name"].lower() == team["name"].lower()
            gf = m["score1"] if is_team1 else m["score2"]
            ga = m["score2"] if is_team1 else m["score1"]
            played += 1
            goals_for += gf
            goals_against += ga
            if gf > ga: won += 1
            elif gf == ga: drawn += 1
            else: lost += 1

        # Buteurs & passeurs de l'équipe
        cur.execute("""
            SELECT player_name, type, COUNT(*) AS total
            FROM goals
            WHERE LOWER(team_name) = LOWER(%s)
            GROUP BY player_name, type
            ORDER BY total DESC
        """, (team["name"],))
        scorers = [dict(r) for r in cur.fetchall()]

        cur.close(); conn.close()

        result = {
            **team,
            "matches": matches,
            "stats": {
                "played": played, "won": won, "drawn": drawn, "lost": lost,
                "goals_for": goals_for, "goals_against": goals_against,
                "points": won * 3 + drawn,
            },
            "scorers": scorers,
        }
        cache_set(f"team_detail_{team_id}", result, ttl_seconds=120)
        return jsonify(_strip_photos(result) if lite else result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
