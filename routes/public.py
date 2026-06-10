import base64
import threading
from flask import Blueprint, jsonify, request, Response
from database import db_conn, get_cursor
import cache as _cache

public_bp = Blueprint("public", __name__)

# Cache mémoire pour les photos joueurs — même pattern que les logos d'équipe.
# Évite 13 requêtes DB × N utilisateurs pour chaque page de détail.
_player_cache: dict = {}   # {player_id: {"bytes": bytes, "mime": str} | None}
_player_lock = threading.Lock()


def _prewarm_player_photos():
    """Décode et met en cache toutes les photos joueurs au démarrage du worker."""
    try:
        with db_conn() as conn:
            cur = get_cursor(conn)
            cur.execute(
                "SELECT id, photo_path FROM players "
                "WHERE photo_path IS NOT NULL AND photo_path != ''"
            )
            rows = cur.fetchall()
            cur.close()
        for row in rows:
            pid = row["id"]
            photo = row["photo_path"]
            if photo and photo.startswith("data:"):
                try:
                    header, b64data = photo.split(",", 1)
                    mime = header.split(":")[1].split(";")[0]
                    img_bytes = base64.b64decode(b64data)
                    with _player_lock:
                        _player_cache[pid] = {"bytes": img_bytes, "mime": mime}
                except Exception:
                    with _player_lock:
                        _player_cache[pid] = None
            else:
                # Chemin fichier legacy : absent sur Render éphémère
                with _player_lock:
                    _player_cache[pid] = None
    except Exception:
        pass


def _strip_photos(data: dict) -> dict:
    return {
        **data,
        "logo_path": None,
        "players": [{**p, "photo_path": None} for p in data.get("players", [])],
    }


# ─── Fonctions de fetch (module-level, réutilisables pour le pré-chauffe) ─────

def _fetch_gallery():
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("SELECT id, title, photo_path, created_at FROM gallery ORDER BY created_at DESC")
        result = [{"id": r["id"], "title": r["title"], "photo_path": r["photo_path"],
                   "created_at": str(r["created_at"])} for r in cur.fetchall()]
        cur.close()
    return result


def _fetch_announcements():
    with db_conn() as conn:
        cur = get_cursor(conn)
        cur.execute("""
            SELECT id, title, content, type, created_at
            FROM announcements
            WHERE active = TRUE
            ORDER BY created_at DESC
        """)
        result = [{"id": r["id"], "title": r["title"], "content": r["content"],
                   "type": r["type"], "created_at": str(r["created_at"])} for r in cur.fetchall()]
        cur.close()
    return result


def _fetch_team_detail(team_id: int) -> dict:
    with db_conn() as conn:
        cur = get_cursor(conn)

        cur.execute("""
            SELECT t.id, t.name, t.captain_name, t.phone,
                   t.logo_path IS NOT NULL AND t.logo_path != '' AS has_logo,
                   t.created_at,
                   p.id AS player_id, p.player_name,
                   p.photo_path IS NOT NULL AND p.photo_path != '' AS has_photo
            FROM teams t
            LEFT JOIN players p ON p.team_id = t.id
            WHERE t.id = %s AND t.validated = 1
            ORDER BY p.id
        """, (team_id,))
        rows = cur.fetchall()
        if not rows:
            cur.close()
            raise LookupError("Équipe non trouvée")

        first = rows[0]
        tid = first["id"]
        team = {
            "id": tid,
            "name": first["name"],
            "captain_name": first["captain_name"],
            # URL légère au lieu du base64 — le navigateur charge à la demande
            "logo_path": f"/api/teams/{tid}/logo" if first["has_logo"] else None,
            "created_at": str(first["created_at"]) if first["created_at"] else None,
            "players": [
                {
                    "id": r["player_id"],
                    "player_name": r["player_name"],
                    # URL au lieu du base64 — photo_path garde le même nom pour la compat frontend
                    "photo_path": f"/api/players/{r['player_id']}/photo" if r["has_photo"] else None,
                }
                for r in rows if r["player_id"]
            ],
        }

        cur.execute("""
            SELECT id, team1_name, team2_name, match_date, match_time, phase,
                   score1, score2, status
            FROM matches
            WHERE LOWER(team1_name) = LOWER(%s) OR LOWER(team2_name) = LOWER(%s)
            ORDER BY match_date, match_time
        """, (team["name"], team["name"]))
        matches = [dict(m) for m in cur.fetchall()]

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
            if gf > ga:   won += 1
            elif gf == ga: drawn += 1
            else:          lost += 1

        cur.execute("""
            SELECT player_name, type, COUNT(*) AS total
            FROM goals
            WHERE LOWER(team_name) = LOWER(%s)
            GROUP BY player_name, type
            ORDER BY total DESC
        """, (team["name"],))
        scorers = [dict(r) for r in cur.fetchall()]
        cur.close()

    return {
        **team,
        "matches": matches,
        "stats": {
            "played": played, "won": won, "drawn": drawn, "lost": lost,
            "goals_for": goals_for, "goals_against": goals_against,
            "points": won * 3 + drawn,
        },
        "scorers": scorers,
    }


# ─── Routes ───────────────────────────────────────────────────────────────────

@public_bp.route("/api/gallery", methods=["GET"])
def get_gallery():
    try:
        result = _cache.get_or_compute("gallery", _fetch_gallery, ttl_seconds=300)
        if result is None:
            return jsonify({"error": "Service temporairement indisponible"}), 503
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@public_bp.route("/api/announcements", methods=["GET"])
def get_announcements():
    try:
        result = _cache.get_or_compute("announcements", _fetch_announcements, ttl_seconds=120)
        if result is None:
            return jsonify({"error": "Service temporairement indisponible"}), 503
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@public_bp.route("/api/teams/<int:team_id>/detail", methods=["GET"])
def get_team_detail(team_id):
    lite = request.args.get("lite", "0") == "1"
    try:
        result = _cache.get_or_compute(
            f"team_detail_{team_id}",
            lambda: _fetch_team_detail(team_id),
            ttl_seconds=120,
        )
        if result is None:
            return jsonify({"error": "Service temporairement indisponible"}), 503
        return jsonify(_strip_photos(result) if lite else result)
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@public_bp.route("/api/players/<int:player_id>/photo", methods=["GET"])
def get_player_photo(player_id):
    """Sert la photo d'un joueur depuis le cache mémoire (0 DB après warmup)."""
    # Chemin rapide : cache mémoire
    with _player_lock:
        if player_id in _player_cache:
            entry = _player_cache[player_id]
            if entry is None:
                return "", 404
            resp = Response(entry["bytes"], mimetype=entry["mime"])
            resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
            return resp

    # Premier accès : charger depuis la DB
    try:
        with db_conn() as conn:
            cur = get_cursor(conn)
            cur.execute("SELECT photo_path FROM players WHERE id = %s", (player_id,))
            row = cur.fetchone()
            cur.close()
    except Exception:
        return "", 503

    if not row or not row["photo_path"]:
        with _player_lock:
            _player_cache[player_id] = None
        return "", 404

    photo = row["photo_path"]
    if not photo.startswith("data:"):
        # Legacy fichier : absent sur Render éphémère
        with _player_lock:
            _player_cache[player_id] = None
        return "", 404

    try:
        header, b64data = photo.split(",", 1)
        mime = header.split(":")[1].split(";")[0]
        img_bytes = base64.b64decode(b64data)
        entry = {"bytes": img_bytes, "mime": mime}
        with _player_lock:
            _player_cache[player_id] = entry
        resp = Response(img_bytes, mimetype=mime)
        resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return resp
    except Exception:
        with _player_lock:
            _player_cache[player_id] = None
        return "", 500
