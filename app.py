import os
import time
import logging
import threading
from flask import Flask, request, send_from_directory
from flask_cors import CORS
from flask_compress import Compress

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_perf_log = logging.getLogger("perf")

from database import init_db
from routes.teams import teams_bp
from routes.matches import matches_bp
from routes.admin import admin_bp
from routes.public import public_bp
from config import UPLOAD_FOLDER, FRONTEND_URL

app = Flask(__name__)

app.config["COMPRESS_MIMETYPES"] = ["application/json", "text/html", "text/plain"]
app.config["COMPRESS_LEVEL"] = 6
app.config["COMPRESS_MIN_SIZE"] = 500
Compress(app)

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

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

app.register_blueprint(teams_bp)
app.register_blueprint(matches_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(public_bp)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
try:
    init_db()
except Exception as _e:
    logging.error("DB init echouee au demarrage (sera reessayee a la premiere requete): %s", _e)


def _prewarm_cache():
    """Pré-chauffe les clés de cache critiques au démarrage de chaque worker.
    Évite le cache stampede : le premier vrai utilisateur ne tape plus jamais
    dans une base froide avec 50 concurrents derrière lui."""
    time.sleep(2)   # Laisser gunicorn finir l'initialisation du worker
    try:
        import cache as _cache
        from routes.teams import _fetch_teams_list
        from routes.matches import _fetch_matches, _fetch_results, _fetch_top_scorers
        from routes.public import _fetch_gallery, _fetch_announcements

        # Utilise set() directement pour ne pas interférer avec le mécanisme
        # inflight des routes : le pre-warmer peuple le cache sans bloquer
        # les requêtes utilisateurs qui arriveraient pendant le chargement.
        _cache.set("teams_list",   _fetch_teams_list(),    ttl_seconds=300)
        _cache.set("matches_list", _fetch_matches(),       ttl_seconds=120)
        _cache.set("results",      _fetch_results(),       ttl_seconds=120)
        _cache.set("top_scorers",  _fetch_top_scorers(),   ttl_seconds=120)
        _cache.set("announcements", _fetch_announcements(), ttl_seconds=120)
        _cache.set("gallery",      _fetch_gallery(),       ttl_seconds=300)
        logging.info("Cache pré-chargé avec succès (worker prêt)")
    except Exception as _e:
        logging.warning("Pré-chauffe cache échouée (non bloquant): %s", _e)


threading.Thread(target=_prewarm_cache, daemon=True).start()


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
    return {"status": "ok", "message": "API Tournoi operationnelle"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(debug=debug, host="0.0.0.0", port=port)
