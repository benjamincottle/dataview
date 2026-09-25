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
    PORT=8000

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /home/app --create-home app

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
