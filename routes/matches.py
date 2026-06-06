from flask import Blueprint, jsonify
from database import get_db, get_cursor
from cache import get as cache_get, set as cache_set

matches_bp = Blueprint("matches", __name__)


@matches_bp.route("/api/matches", methods=["GET"])
def get_matches():
    cached = cache_get("matches_list")
    if cached is not None:
        return jsonify(cached)
    try:
        conn = get_db()
        cur = get_cursor(conn)
        cur.execute("SELECT * FROM matches ORDER BY match_date, match_time")
        matches = cur.fetchall()
        cur.close()
        conn.close()
        result = [dict(m) for m in matches]
        cache_set("matches_list", result, ttl_seconds=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@matches_bp.route("/api/results", methods=["GET"])
def get_results():
    cached = cache_get("results")
    if cached is not None:
        return jsonify(cached)
    try:
        conn = get_db()
        cur = get_cursor(conn)

        cur.execute("SELECT * FROM matches WHERE status = 'finished' ORDER BY match_date, match_time")
        finished = cur.fetchall()

        cur.execute("SELECT id, name FROM teams WHERE validated = 1")
        teams_raw = cur.fetchall()

        standings_by_id   = {t["id"]:   {"id": t["id"], "name": t["name"], "played": 0, "won": 0, "drawn": 0, "lost": 0, "goals_for": 0, "goals_against": 0, "points": 0} for t in teams_raw}
        standings_by_name = {t["name"]: standings_by_id[t["id"]] for t in teams_raw}

        cur.execute("SELECT * FROM matches WHERE phase = 'Poule' AND status = 'finished'")
        poule_matches = cur.fetchall()

        def get_entry(team_id, team_name):
            if team_id and team_id in standings_by_id:
                return standings_by_id[team_id]
            if team_name and team_name in standings_by_name:
                return standings_by_name[team_name]
            return None

        for m in poule_matches:
            s1, s2 = m["score1"] or 0, m["score2"] or 0
            e1 = get_entry(m["team1_id"], m["team1_name"])
            e2 = get_entry(m["team2_id"], m["team2_name"])

            for entry, gf, ga in [(e1, s1, s2), (e2, s2, s1)]:
                if entry is None:
                    continue
                entry["played"] += 1
                entry["goals_for"] += gf
                entry["goals_against"] += ga
                if gf > ga:
                    entry["won"] += 1
                    entry["points"] += 3
                elif gf == ga:
                    entry["drawn"] += 1
                    entry["points"] += 1
                else:
                    entry["lost"] += 1

        ranking = sorted(
            standings_by_id.values(),
            key=lambda x: (-x["points"], -(x["goals_for"] - x["goals_against"]), -x["goals_for"])
        )

        cur.close()
        conn.close()
        result = {
            "finished_matches": [dict(m) for m in finished],
            "standings": ranking,
        }
        cache_set("results", result, ttl_seconds=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
