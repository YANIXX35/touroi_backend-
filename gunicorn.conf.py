import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1
worker_class = "gthread"
threads = 10       # Standard plan : 2GB RAM + 1 CPU — peut gérer plus de concurrence
timeout = 60
keepalive = 5
