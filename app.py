import os
import sys
import time
import logging
import traceback

print("=" * 60)
print("STARTUP: app.py chargement commence")
print(f"STARTUP: Python {sys.version}")
print(f"STARTUP: CWD = {os.getcwd()}")
print("=" * 60)

# ── Variables d'environnement (présence sans valeur) ──────────────────────────
print("ENV CHECK:")
print(f"  DATABASE_URL    = {bool(os.getenv('DATABASE_URL'))}")
print(f"  JWT_SECRET_KEY  = {bool(os.getenv('JWT_SECRET_KEY'))}")
print(f"  ADMIN_USERNAME  = {bool(os.getenv('ADMIN_USERNAME'))}")
print(f"  ADMIN_PASSWORD  = {bool(os.getenv('ADMIN_PASSWORD'))}")
print(f"  FRONTEND_URL    = {bool(os.getenv('FRONTEND_URL'))}")
print(f"  PORT            = {os.getenv('PORT', '(non défini)')}")
print(f"  FLASK_ENV       = {os.getenv('FLASK_ENV', '(non défini)')}")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_perf_log = logging.getLogger("perf")

# ── Imports avec logs ─────────────────────────────────────────────────────────
print("IMPORT: flask...")
from flask import Flask, request, send_from_directory
from flask_cors import CORS
from flask_compress import Compress
print("IMPORT: flask OK")

print("IMPORT: config...")
try:
    from config import UPLOAD_FOLDER, FRONTEND_URL
    print(f"IMPORT: config OK — UPLOAD_FOLDER={UPLOAD_FOLDER}")
except Exception as e:
    print(f"IMPORT: config ERREUR — {repr(e)}")
    traceback.print_exc()
    raise

print("IMPORT: database...")
try:
    from database import init_db
    print("IMPORT: database OK")
except Exception as e:
    print(f"IMPORT: database ERREUR — {repr(e)}")
    traceback.print_exc()
    raise

print("IMPORT: routes.teams...")
try:
    from routes.teams import teams_bp
    print("IMPORT: routes.teams OK")
except Exception as e:
    print(f"IMPORT: routes.teams ERREUR — {repr(e)}")
    traceback.print_exc()
    raise

print("IMPORT: routes.matches...")
try:
    from routes.matches import matches_bp
    print("IMPORT: routes.matches OK")
except Exception as e:
    print(f"IMPORT: routes.matches ERREUR — {repr(e)}")
    traceback.print_exc()
    raise

print("IMPORT: routes.admin...")
try:
    from routes.admin import admin_bp
    print("IMPORT: routes.admin OK")
except Exception as e:
    print(f"IMPORT: routes.admin ERREUR — {repr(e)}")
    traceback.print_exc()
    raise

print("IMPORT: routes.public...")
try:
    from routes.public import public_bp
    print("IMPORT: routes.public OK")
except Exception as e:
    print(f"IMPORT: routes.public ERREUR — {repr(e)}")
    traceback.print_exc()
    raise

# ── Création Flask ─────────────────────────────────────────────────────────────
print("FLASK: création de l'app...")
app = Flask(__name__)
print("FLASK: app créée OK")

app.config["COMPRESS_MIMETYPES"] = ["application/json", "text/html", "text/plain"]
app.config["COMPRESS_LEVEL"] = 6
app.config["COMPRESS_MIN_SIZE"] = 500
Compress(app)
print("FLASK: Compress OK")

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
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
print(f"FLASK: CORS OK — origines autorisées: {_allowed_origins}")

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

app.register_blueprint(teams_bp)
app.register_blueprint(matches_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(public_bp)
print("FLASK: blueprints enregistrés OK")

# ── Init dossier uploads ───────────────────────────────────────────────────────
print(f"UPLOADS: makedirs {UPLOAD_FOLDER}...")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
print("UPLOADS: OK")

# ── Init base de données ───────────────────────────────────────────────────────
print("DATABASE: init_db() début...")
try:
    init_db()
    print("DATABASE: init_db() OK")
except Exception as e:
    print(f"DATABASE: init_db() ERREUR — {repr(e)}")
    traceback.print_exc()
    raise

print("=" * 60)
print("STARTUP: app.py chargement TERMINÉ — serveur prêt")
print("=" * 60)


@app.before_request
def _start_timer():
    request._t0 = time.monotonic()


@app.after_request
def _log_perf(response):
    t0 = getattr(request, "_t0", None)
    if t0 and not request.path.startswith("/uploads"):
        ms = (time.monotonic() - t0) * 1000
        if ms >= 1000:
            _perf_log.warning("CRITIQUE  %s %s → %d  [%.0f ms]", request.method, request.path, response.status_code, ms)
        elif ms >= 300:
            _perf_log.warning("LENT      %s %s → %d  [%.0f ms]", request.method, request.path, response.status_code, ms)
        elif ms >= 100:
            _perf_log.info("MOYEN     %s %s → %d  [%.0f ms]", request.method, request.path, response.status_code, ms)
    return response


@app.after_request
def add_cache_headers(response):
    if request.method == "GET" and response.status_code == 200:
        if not request.path.startswith("/api/admin"):
            response.headers["Cache-Control"] = "public, max-age=60"
        else:
            response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/uploads/<string:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/api/health")
def health():
    return {"status": "ok", "message": "API Tournoi opérationnelle"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(debug=debug, host="0.0.0.0", port=port)
