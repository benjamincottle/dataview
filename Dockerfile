# syntax=docker/dockerfile:1

# Kept in step with UPSTREAM_IMAGE in .github/workflows/build.yml, which passes
# it in so the daily digest check and the build use the same base.
ARG PYTHON_IMAGE=python:3.14-slim

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
# Hashes pin every dependency; binary wheels only, so nothing compiles here.
RUN /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r /tmp/requirements.txt


FROM ${PYTHON_IMAGE}

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATAVIEW_CACHE_DIR=/home/app/cache

# Fixed IDs (the ones earlier images got by chance) so volume and tmpfs
# ownership in compose files stays stable across rebuilds. /home/app is
# opened up from useradd's 0700 so the image also runs as any other uid
# (e.g. `user: "1000:1000"` to own a bind-mounted cache).
RUN groupadd --system --gid 101 app \
    && useradd --system --uid 100 --gid app --home-dir /home/app --create-home app \
    && install -d -o app -g app /home/app/cache \
    && chmod 0755 /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web
# Code stays root-owned, so the app user can't modify it.
COPY gunicorn.conf.py healthcheck.py manage.py ./
COPY dataview ./dataview

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "healthcheck.py"]

CMD ["gunicorn", "dataview:app"]
