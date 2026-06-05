import sqlite3
import os
import bcrypt
from config import ADMIN_USERNAME, ADMIN_PASSWORD

DB_PATH = os.path.join(os.path.dirname(__file__), "tournoi.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            captain_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            logo_path TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            validated INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team1_id INTEGER,
            team2_id INTEGER,
            team1_name TEXT,
            team2_name TEXT,
            match_date TEXT,
            match_time TEXT,
            phase TEXT DEFAULT 'Poule',
            score1 INTEGER DEFAULT NULL,
            score2 INTEGER DEFAULT NULL,
            status TEXT DEFAULT 'upcoming',
            FOREIGN KEY (team1_id) REFERENCES teams(id),
            FOREIGN KEY (team2_id) REFERENCES teams(id)
        );

        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        );
    """)

    # Créer l'admin par défaut s'il n'existe pas
    existing = cursor.execute(
        "SELECT id FROM admins WHERE username = ?", (ADMIN_USERNAME,)
    ).fetchone()

    if not existing:
        password_hash = bcrypt.hashpw(
            ADMIN_PASSWORD.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        cursor.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
            (ADMIN_USERNAME, password_hash),
        )

    conn.commit()
    conn.close()
    print("Base de données initialisée.")
