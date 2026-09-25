import os
import re
import time
from datetime import UTC, datetime

from flask import Blueprint, current_app, render_template, request, url_for

from . import cache, nfr

bp = Blueprint("main", __name__)

OUTCOME_FILTERS = {
    "novel": "Novel food",
    "not-novel": "Not novel food",
    "additive": "Food additive",
}


@bp.app_template_filter("datetime")
def format_datetime(timestamp):
    moment = datetime.fromtimestamp(timestamp, UTC)
    return f"{moment.day} {moment:%b %Y, %H:%M} UTC"


@bp.app_template_global()
def asset_url(filename):
    """Static URL with a version so browsers can cache assets for a long time."""
    path = os.path.join(current_app.static_folder, filename)
    return url_for("static", filename=filename, v=int(os.path.getmtime(path)))


@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/healthz")
def healthz():
    return {"status": "ok"}


@bp.get("/nfr")
def nfr_view():
    config = current_app.config
    data = cache.get(nfr.DATA_KEY)
    state = nfr.get_status(cache)["state"]

    if data and time.time() - data["checked_at"] < config["NFR_CHECK_INTERVAL"]:
        return _render_nfr(data)
    if state == "processing":
        return _render_nfr(data, notice=True) if data else _processing()
    if state == "error":
        # Back off until the error status expires rather than re-running a
        # multi-minute parse of a PDF that just failed.
        if data:
            return _render_nfr(data, warning="The newest PDF could not be processed.")
        return _error("The latest PDF could not be processed. Please try again in a few minutes.")

    try:
        result, update = nfr.check_for_update(data, config)
    except nfr.UpstreamError as exc:
        current_app.logger.warning("NFR update check failed: %s", exc)
        if data:
            return _render_nfr(data, warning=f"Couldn't check FSANZ for updates ({exc}).")
        return _error(f"{exc}. Please try again later.", 502)

    if result == "unchanged" and data:
        data.update(checked_at=time.time(), page_validators=update["page_validators"])
        cache.set(nfr.DATA_KEY, data, timeout=config["NFR_DATA_TTL"])
        return _render_nfr(data)

    nfr.start_processing(current_app._get_current_object(), cache, update)
    return _render_nfr(data, notice=True) if data else _processing()


@bp.get("/nfr/status")
def nfr_status():
    state = nfr.get_status(cache)["state"]
    if state != "processing" and cache.has(nfr.DATA_KEY):
        state = "complete"
    return {"state": state}, 200, {"Cache-Control": "no-store"}


def _processing():
    return render_template("processing.html"), 202


def _error(message, status=503):
    return render_template("error.html", message=message), status


def _search_text(row):
    parts = [nfr.plain_text(row["food"]), nfr.plain_text(row["justification"])]
    parts += [o["text"] for o in row["outcome"]]
    parts += [n["text"] or "" for n in row["notes"]]
    return " ".join(parts).lower()


def _matches(row, filters):
    """Server-side twin of the filtering in nfr.js, for browsers without JS."""
    if filters["outcome"] and filters["outcome"] not in {o["tone"] for o in row["outcome"]}:
        return False
    fields = {
        "q": _search_text(row),
        "food": nfr.plain_text(row["food"]).lower(),
        "justification": nfr.plain_text(row["justification"]).lower(),
    }
    return all(
        _word_start(term).search(text) for name, text in fields.items() for term in filters[name].split()
    )


def _word_start(term):
    """Match ``term`` at the start of a word, so "lion" finds "Lion's mane" but not "million"."""
    return re.compile(r"(?<![^\W_])" + re.escape(term))


def _render_nfr(data, notice=False, warning=None):
    filters = {
        name: request.args.get(name, "").strip().lower()[:200]
        for name in ("q", "food", "justification", "outcome")
    }
    if filters["outcome"] not in OUTCOME_FILTERS:
        filters["outcome"] = ""
    rows = [
        dict(row, hidden=not _matches(row, filters), tones=" ".join(o["tone"] for o in row["outcome"]))
        for row in data["rows"]
    ]
    return render_template(
        "nfr.html",
        data=data,
        rows=rows,
        visible=sum(not row["hidden"] for row in rows),
        filters=filters,
        outcome_filters=OUTCOME_FILTERS,
        notice=notice,
        warning=warning,
    )
