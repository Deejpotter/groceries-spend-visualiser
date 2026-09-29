import os

# Bind to all interfaces on the configured port
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# Worker configuration
# SQLite allows a single writer, so keep the worker count small; threads cover concurrent reads.
workers = int(os.getenv("GUNICORN_WORKERS", "2"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))

# Timeouts
timeout = 30
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")

# Process naming
proc_name = "grocery-visualiser"

# Graceful shutdown
graceful_timeout = 30
preload_app = True
