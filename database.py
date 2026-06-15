import os
import bcrypt
import psycopg2
import psycopg2.extras
import psycopg2.pool
from contextlib import contextmanager
from config import ADMIN_USERNAME, ADMIN_PASSWORD

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# 1 worker × 16 threads : maxconn=5 connexions PG — compatible Aiven free.
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=DATABASE_URL,
        )
    return _pool


class _PooledConn:
    """
    Wrapper autour d'une connexion psycopg2 tiree du pool.
    .close() remet la connexion dans le pool au lieu de la fermer vraiment.
    """
    __slots__ = ("_conn", "_pool")

    def __init__(self, conn, pool):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_pool", pool)

    def close(self):
        conn = object.__getattribute__(self, "_conn")
        pool = object.__getattribute__(self, "_pool")
        try:
            if not conn.closed:
                conn.rollback()
        except Exception:
            pass
        pool.putconn(conn)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_conn"), name, value)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def get_db():
    """Retourne une connexion tiree du pool. conn.close() la remet dans le pool."""
    pool = _get_pool()
    return _PooledConn(pool.getconn(), pool)


@contextmanager
def db_conn():
    """Context manager — garantit le retour au pool meme en cas d'exception."""
    conn = get_db()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    with db_conn() as conn:
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
        cur.execute("ALTER TABLE matches ADD COLUMN IF NOT EXISTS terrain TEXT")
        cur.execute("ALTER TABLE matches ADD COLUMN IF NOT EXISTS match_number INTEGER")
        # Migrations pour colonnes ajoutées après la création initiale des tables
        cur.execute("ALTER TABLE announcements ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
        cur.execute("ALTER TABLE announcements ADD COLUMN IF NOT EXISTS type TEXT DEFAULT 'info'")

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
            CREATE TABLE IF NOT EXISTS goals (
                id SERIAL PRIMARY KEY,
                match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                player_name TEXT NOT NULL,
                team_name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'goal',
                minute INTEGER,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_teams_validated ON teams(validated)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_players_team_id ON players(team_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_teams_phone ON teams(phone)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_teams_name ON teams(name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_phase ON matches(phase)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_goals_match_id ON goals(match_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_goals_type ON goals(type)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS gallery (
                id SERIAL PRIMARY KEY,
                title TEXT,
                photo_path TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'photo',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("ALTER TABLE gallery ADD COLUMN IF NOT EXISTS media_type TEXT NOT NULL DEFAULT 'photo'")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'info',
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                ip TEXT PRIMARY KEY,
                attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TIMESTAMPTZ,
                last_attempt TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_gallery_created ON gallery(created_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_announcements_active ON announcements(active)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_admin_logs_created ON admin_logs(created_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_team1_lower ON matches(LOWER(team1_name))")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_team2_lower ON matches(LOWER(team2_name))")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_goals_team_lower ON goals(LOWER(team_name))")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS mvp_votes (
                id SERIAL PRIMARY KEY,
                player_name TEXT NOT NULL,
                team_name TEXT NOT NULL,
                voter_ip TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(voter_ip)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mvp_votes_ip ON mvp_votes(voter_ip)")

        cur.execute("SELECT id FROM admins WHERE username = %s", (ADMIN_USERNAME,))
        if not cur.fetchone():
            password_hash = bcrypt.hashpw(
                ADMIN_PASSWORD.encode("utf-8"), bcrypt.gensalt()
            ).decode("utf-8")
            cur.execute(
                "INSERT INTO admins (username, password_hash) VALUES (%s, %s)",
                (ADMIN_USERNAME, password_hash),
            )

        conn.commit()
        cur.close()
        print("Base de donnees PostgreSQL initialisee.")
