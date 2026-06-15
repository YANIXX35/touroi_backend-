from flask import Blueprint, request, jsonify
from database import db_conn, get_cursor

mvp_bp = Blueprint("mvp", __name__)


@mvp_bp.route("/api/mvp", methods=["GET"])
def get_mvp():
    voter_ip = request.remote_addr or "unknown"

    with db_conn() as conn:
        cur = get_cursor(conn)

        # Candidats = tous les joueurs ayant marqué un but, avec leur nb de votes
        cur.execute("""
            SELECT
                g.player_name,
                g.team_name,
                COUNT(g.id)              AS goals,
                COALESCE(v.vote_count, 0) AS votes
            FROM goals g
            LEFT JOIN (
                SELECT LOWER(player_name) AS pn, LOWER(team_name) AS tn, COUNT(*) AS vote_count
                FROM mvp_votes
                GROUP BY LOWER(player_name), LOWER(team_name)
            ) v ON LOWER(g.player_name) = v.pn AND LOWER(g.team_name) = v.tn
            WHERE g.type = 'goal'
            GROUP BY g.player_name, g.team_name, v.vote_count
            ORDER BY votes DESC, goals DESC
        """)
        candidates = [dict(r) for r in cur.fetchall()]

        # L'IP a-t-elle déjà voté ?
        cur.execute(
            "SELECT player_name, team_name FROM mvp_votes WHERE voter_ip = %s",
            (voter_ip,),
        )
        voted_row = cur.fetchone()
        cur.close()

    total_votes = sum(c["votes"] for c in candidates)
    for c in candidates:
        c["percentage"] = round((c["votes"] / total_votes * 100) if total_votes else 0, 1)
        c["goals"] = int(c["goals"])
        c["votes"] = int(c["votes"])

    return jsonify({
        "candidates":  candidates,
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
        return jsonify({"error": "Joueur invalide"}), 400

    with db_conn() as conn:
        cur = get_cursor(conn)

        # Déjà voté ?
        cur.execute("SELECT id FROM mvp_votes WHERE voter_ip = %s", (voter_ip,))
        if cur.fetchone():
            cur.close()
            return jsonify({"error": "Vous avez déjà voté !"}), 409

        # Le joueur existe bien dans les buts ?
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM goals
               WHERE LOWER(player_name) = LOWER(%s)
                 AND LOWER(team_name)   = LOWER(%s)
                 AND type = 'goal'""",
            (player_name, team_name),
        )
        row = cur.fetchone()
        if not row or int(row["cnt"]) == 0:
            cur.close()
            return jsonify({"error": "Joueur introuvable parmi les buteurs"}), 404

        cur.execute(
            "INSERT INTO mvp_votes (player_name, team_name, voter_ip) VALUES (%s, %s, %s)",
            (player_name, team_name, voter_ip),
        )
        conn.commit()
        cur.close()

    return jsonify({"success": True, "message": "Vote enregistré ! Merci !"})
