import os
from flask import Flask, send_from_directory
from flask_cors import CORS
from database import init_db
from routes.teams import teams_bp
from routes.matches import matches_bp
from routes.admin import admin_bp
from config import UPLOAD_FOLDER, FRONTEND_URL

app = Flask(__name__)

# Origines autorisées — Vercel hardcodé pour garantir le CORS même si FRONTEND_URL n'est pas défini
_allowed_origins = [
    "http://localhost:4200",
    "https://tournoi-front.vercel.app",
]
if FRONTEND_URL and FRONTEND_URL.strip() not in _allowed_origins:
    _allowed_origins.append(FRONTEND_URL.strip())

CORS(
    app,
    resources={r"/api/*": {"origins": _allowed_origins}},
    supports_credentials=False,
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 Mo max

app.register_blueprint(teams_bp)
app.register_blueprint(matches_bp)
app.register_blueprint(admin_bp)

# Créer le dossier uploads et initialiser la BDD au démarrage (gunicorn inclus)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
init_db()


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/api/health")
def health():
    return {"status": "ok", "message": "API Tournoi opérationnelle"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(debug=debug, host="0.0.0.0", port=port)
