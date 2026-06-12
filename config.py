import os
import logging
from datetime import datetime

# --- Clé secrète JWT (lire depuis variable d'environnement en prod) ---
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "tournoi-eglise-secret-key-2024-secure!")
if not os.environ.get("JWT_SECRET_KEY"):
    logging.warning("⚠️  JWT_SECRET_KEY non définie — clé par défaut utilisée (INSÉCURISÉ en prod)")
JWT_EXPIRATION_HOURS = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))

# --- Gmail SMTP ---
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "votre.email@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "xxxx xxxx xxxx xxxx")

# --- Emails des responsables (séparés par une virgule en var d'env) ---
_responsables_raw = os.environ.get(
    "RESPONSABLES_EMAILS",
    "responsable1@gmail.com,responsable2@gmail.com"
)
RESPONSABLES_EMAILS = [e.strip() for e in _responsables_raw.split(",") if e.strip()]

# --- Infos du tournoi ---
TOURNOI_NOM = os.environ.get("TOURNOI_NOM", "Tournoi de Football de l'Étoile Universelle de Grand Bassam 2026")
TOURNOI_DATE_DEBUT = "2026-06-13"
TOURNOI_LIEU = "Grand Bassam"

# --- Dossier uploads ---
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_PHOTO_SIZE = (200, 200)

# --- Admin par défaut ---
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
if not os.environ.get("ADMIN_PASSWORD"):
    logging.warning("⚠️  ADMIN_PASSWORD non définie — mot de passe 'admin123' utilisé (INSÉCURISÉ en prod)")

# --- Frontend URL (pour CORS en prod) ---
FRONTEND_URL = os.environ.get("FRONTEND_URL", "")

# --- Clôture des inscriptions ---
# Les inscriptions ferment le 11 juin 2026 à 22h00 (heure Abidjan = UTC+0).
# Après cette date, POST /api/register retourne 403.
REGISTRATION_DEADLINE = datetime(2026, 6, 13, 22, 0, 0)
