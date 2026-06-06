import os
import bcrypt
import psycopg2
import psycopg2.extras
import psycopg2.pool
from config import ADMIN_USERNAME, ADMIN_PASSWORD

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# ─── Connection pool (réutilise les connexions au lieu d'en ouvrir une par requête) ──
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            dsn=DATABASE_URL,
        )
    return _pool


def get_db():
    """
    Retourne une connexion tirée du pool.
    conn.close() la remet dans le pool au lieu de la fermer vraiment.
    """
    pool = _get_pool()
    conn = pool.getconn()

    def _return_to_pool():
        try:
            if not conn.closed:
                conn.rollback()
        except Exception:
            pass
        pool.putconn(conn)

    conn.close = _return_to_pool
    return conn


def get_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            captain_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            logo_path TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            validated INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id SERIAL PRIMARY KEY,
            team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            player_name TEXT NOT NULL,
            photo_path TEXT
        )
    """)
    cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS photo_path TEXT")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id SERIAL PRIMARY KEY,
            team1_id INTEGER REFERENCES teams(id),
            team2_id INTEGER REFERENCES teams(id),
            team1_name TEXT,
            team2_name TEXT,
            match_date TEXT,
            match_time TEXT,
            phase TEXT DEFAULT 'Poule',
            score1 INTEGER DEFAULT NULL,
            score2 INTEGER DEFAULT NULL,
            status TEXT DEFAULT 'upcoming'
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        )
    """)

    cur.execute("SELECT id FROM admins WHERE username = %s", (ADMIN_USERNAME,))
    existing = cur.fetchone()

    if not existing:
        password_hash = bcrypt.hashpw(
            ADMIN_PASSWORD.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        cur.execute(
            "INSERT INTO admins (username, password_hash) VALUES (%s, %s)",
            (ADMIN_USERNAME, password_hash),
        )

    conn.commit()
    cur.close()
    conn.close()
    print("Base de donnees PostgreSQL initialisee.")
