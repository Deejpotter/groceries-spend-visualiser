import multiprocessing
import os

# Bind to all interfaces on the configured port
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# Worker configuration
workers = int(os.getenv("GUNICORN_WORKERS", str(multiprocessing.cpu_count() * 2 + 1)))
worker_class = "sync"
worker_connections = 1000

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
