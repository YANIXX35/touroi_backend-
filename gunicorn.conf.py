import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 2
worker_class = "gthread"
threads = 8
timeout = 60
keepalive = 5
