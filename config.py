import os

# --- Clé secrète JWT (lire depuis variable d'environnement en prod) ---
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "tournoi-eglise-secret-key-2024-secure!")
JWT_EXPIRATION_HOURS = int(os.environ.get("JWT_EXPIRATION_HOURS", "8"))

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
TOURNOI_NOM = os.environ.get("TOURNOI_NOM", "Tournoi FJU Côte d'Ivoire 2026")
TOURNOI_DATE_DEBUT = "2026-06-13"
TOURNOI_LIEU = "Terrain de l'Église"

# --- Dossier uploads ---
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_PHOTO_SIZE = (400, 400)

# --- Admin par défaut ---
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# --- Frontend URL (pour CORS en prod) ---
FRONTEND_URL = os.environ.get("FRONTEND_URL", "")
