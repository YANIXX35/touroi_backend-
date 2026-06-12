import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1
worker_class = "gthread"
threads = 6        # réduit de 16 → 6 pour économiser ~40MB de stack
timeout = 60
keepalive = 5
