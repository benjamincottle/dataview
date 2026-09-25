# Loaded automatically by gunicorn from the working directory. Command-line
# flags (e.g. a --bind in docker compose) still take precedence.
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# One worker by default: with the in-process SimpleCache, the parsed data and
# the background parse live in that process. To run more workers, share the
# cache between them with DATAVIEW_CACHE_TYPE=FileSystemCache.
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"
worker_tmp_dir = "/dev/shm"
timeout = 60
graceful_timeout = 20

accesslog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
