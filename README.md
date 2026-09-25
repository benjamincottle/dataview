# Dataview

Searchable web views of public datasets that are only published as PDFs.

Currently one dataset: FSANZ's [record of views on novel foods](https://www.foodstandards.gov.au/business/novel/novelrecs)
(Standard 1.5.1). Served at `/nfr`.

## How it works

1. A request to `/nfr` serves cached data if FSANZ was checked in the last 15 minutes.
2. Otherwise it makes conditional requests (`ETag` / `Last-Modified`) for the FSANZ page and the PDF it links to.
3. If the PDF changed, a background thread extracts the tables with [camelot](https://camelot-py.readthedocs.io/)
   (seconds on x86, a few minutes on small ARM boards). Visitors see the previous data meanwhile, or a
   progress page on first run.
4. If FSANZ is unreachable, the last good data is still served, with a warning.

PDF text is never treated as HTML: cells are parsed into text, superscript and footnote segments and
rendered through Jinja's autoescaping. Footnote text is pulled from the bottom of the relevant PDF page.

## Running

```sh
docker build -t dataview .
docker run --rm -p 8000:8000 dataview
```

The image runs gunicorn on `$PORT` (default 8000) as a non-root user, with a `/healthz` health check.
It sets its own security headers, including a strict Content-Security-Policy (no inline script or
style), so a reverse proxy does not need to add a CSP; if one does, it must not be looser than the app's.

### Configuration

Any Flask setting can be set with a `DATAVIEW_` prefixed environment variable.

| Variable | Default | |
| --- | --- | --- |
| `PORT` | `8000` | Port gunicorn listens on |
| `WEB_CONCURRENCY` | `1` | gunicorn workers. With more than one, use a shared cache (below) |
| `GUNICORN_THREADS` | `4` | Threads per worker |
| `DATAVIEW_CACHE_TYPE` | `SimpleCache` | `FileSystemCache` keeps data across restarts and workers |
| `DATAVIEW_CACHE_DIR` | `/home/app/cache` in the image | Used by `FileSystemCache`; mount a volume here to keep data across restarts |
| `DATAVIEW_NFR_CHECK_INTERVAL` | `900` | Seconds between checks for a new PDF |
| `DATAVIEW_NFR_RETRY_AFTER` | `300` | Seconds to wait after a failed parse before retrying |

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.txt
pip install -r requirements-dev.txt
flask --app dataview run --debug
pytest
ruff check . && ruff format --check .
```

Runtime dependencies are listed in `requirements.in`. `requirements.txt` is a hashed lock generated from
it; regenerate after editing (or to pick up updates) with:

```sh
uv pip compile requirements.in --universal --python-version 3.14 --generate-hashes -o requirements.txt
```

## CI

`.github/workflows/build.yml` lints and tests every push and pull request, then builds the arm64 image.
Pushes to `main` publish it to `ghcr.io/<owner>/dataview`. A daily run rebuilds when the
`python:3.14-slim` base image changes, to pick up OS security fixes.
