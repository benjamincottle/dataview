"""FSANZ "Record of views formed in response to inquiries" (novel foods).

The source is a PDF linked from an FSANZ web page. This module finds the PDF,
downloads it with conditional requests, and turns its tables into rows the
templates can render without ever trusting PDF text as HTML.

A cell is a list of segments:

* ``("text", str)`` plain text
* ``("sup", str)``  a superscript, e.g. the 9 in 10⁹
* ``("fn", str)``   a footnote reference, explained in the row's ``notes``
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

PAGE_URL = "https://www.foodstandards.gov.au/business/novel/novelrecs"
USER_AGENT = "dataview (+https://github.com/benjamincottle/dataview)"

DATA_KEY = "nfr:data"
STATUS_KEY = "nfr:status"

HEADER = ("food or food ingredient", "outcome view", "justification/comment")


class UpstreamError(Exception):
    """The FSANZ site could not be reached or returned something unexpected."""


class ParseError(Exception):
    """The PDF did not contain the tables we expect."""


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

# camelot's flag_size=True wraps text set in a smaller font in <s></s>.
_SUP_RE = re.compile(r"<s>(.*?)</s>", re.S)
# Word leaves a hidden "NF" anchor next to footnote N+1; it carries no meaning.
_WORD_ANCHOR_RE = re.compile(r"^\d+F$")
_SPACE_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:)])")
_FOOTNOTE_LINE_RE = re.compile(r"^\d+ ")
_SYMBOLS = {"TM": "™", "®": "®", "™": "™"}


def _tidy(text: str) -> str:
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", _SPACE_RE.sub(" ", text))


def _merge_text(segments: list) -> list:
    """Join adjacent text segments, normalise whitespace, trim the ends."""
    merged: list = []
    for kind, value in segments:
        if kind == "text" and merged and merged[-1][0] == "text":
            merged[-1] = ("text", merged[-1][1] + value)
        else:
            merged.append((kind, value))
    merged = [(k, _tidy(v) if k == "text" else v) for k, v in merged]
    if merged and merged[0][0] == "text":
        merged[0] = ("text", merged[0][1].lstrip())
    if merged and merged[-1][0] == "text":
        merged[-1] = ("text", merged[-1][1].rstrip())
    return [(k, v) for k, v in merged if v]


def parse_cell(raw: str) -> list:
    """Split one camelot cell into text / superscript / footnote segments.

    A superscript number is a footnote only when Word's matching "N-1F" anchor
    is in the same cell; otherwise it is an exponent (``1 x 10<s>9</s> CFU``).
    Long "superscripts" are body text camelot misjudged and are kept as text.
    """
    tokens = [m.strip() for m in _SUP_RE.findall(raw)]
    footnotes = {int(t[:-1]) + 1 for t in tokens if _WORD_ANCHOR_RE.match(t)}

    segments: list = []
    pos = 0
    after_mark = False

    def text(value: str) -> None:
        nonlocal after_mark
        if after_mark and (value[:1].isalnum() or value[:1] == "("):
            value = " " + value
        after_mark = False
        segments.append(("text", value))

    for match in _SUP_RE.finditer(raw):
        text(raw[pos : match.start()])
        pos = match.end()
        inner = _tidy(match.group(1)).strip()
        if not inner or _WORD_ANCHOR_RE.match(inner):
            continue
        if inner in _SYMBOLS:
            segments.append(("text", _SYMBOLS[inner]))
        elif inner.isdigit() and int(inner) in footnotes:
            if segments and segments[-1][0] == "text":  # attach marker to the word
                segments[-1] = ("text", segments[-1][1].rstrip())
            segments.append(("fn", inner))
        elif inner.isdigit() and len(inner) <= 2:
            segments.append(("sup", inner))
        else:
            before = "" if raw[: match.start()].endswith("-") else " "
            segments.append(("text", before + inner + " "))
            continue
        after_mark = True
    text(raw[pos:])
    return _merge_text(segments)


def plain_text(segments: list) -> str:
    return "".join(v for k, v in segments if k == "text")


def outcome_tone(item: str) -> str:
    """Classify one outcome bullet for styling and filtering."""
    lowered = item.lower()
    if lowered.startswith("not novel"):
        return "not-novel"
    if lowered.startswith("novel"):
        return "novel"
    if lowered.startswith("regulate") or "food additive" in lowered:
        return "additive"
    return "other"


def _is_header(cells: list[str]) -> bool:
    return tuple(_tidy(c).strip().lower() for c in cells) == HEADER


def parse_tables(tables: list[tuple[int, list[list[str]]]]) -> list[dict]:
    """Turn ``[(page, rows), ...]`` from camelot into display rows.

    Each PDF page is its own table, so an entry that runs over a page break
    shows up as a first row with an empty outcome; it is merged back into the
    row it continues.
    """
    rows: list[dict] = []
    for page, table in tables:
        first_data_row = True
        for cells in table:
            if len(cells) != 3:
                log.warning("Skipping row with %d cells on page %s", len(cells), page)
                continue
            if _is_header(cells) or not any(c.strip() for c in cells):
                continue
            food, outcome, justification = (parse_cell(c) for c in cells)
            if first_data_row and rows and not plain_text(outcome).strip():
                prev = rows[-1]
                prev["food"] = _merge_text(prev["food"] + [("text", " ")] + food)
                prev["justification"] = _merge_text(prev["justification"] + [("text", " ")] + justification)
                prev["refs"].extend((page, n) for k, n in food + justification if k == "fn")
                first_data_row = False
                continue
            first_data_row = False

            items = [i.strip(" .") for i in plain_text(outcome).split("•")]
            items = [i for i in items if i]
            rows.append(
                {
                    "food": food,
                    "outcome": [{"text": i, "tone": outcome_tone(i)} for i in items],
                    "justification": justification,
                    "page": page,
                    "refs": [(page, n) for k, n in food + outcome + justification if k == "fn"],
                    "notes": [],
                }
            )
    if not rows:
        raise ParseError("No rows found in the PDF; has its layout changed?")
    return rows


def attach_footnotes(rows: list[dict], page_texts: dict[int, str]) -> None:
    """Fill each row's ``notes`` from footnote lines at the bottom of its pages."""
    for row in rows:
        for page, number in row.pop("refs"):
            note = None
            lines = [line.strip() for line in page_texts.get(page, "").splitlines()]
            # Footnotes sit at the bottom of the page, so search upwards and
            # take the following lines too, until the next footnote begins.
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].startswith(number + " "):
                    parts = [lines[i][len(number) :]]
                    for line in lines[i + 1 :]:
                        if _FOOTNOTE_LINE_RE.match(line):
                            break
                        parts.append(line)
                    note = _tidy(" ".join(parts)).strip()
                    break
            row["notes"].append({"n": number, "page": page, "text": note})


def extract_rows(pdf: bytes) -> list[dict]:
    """Run camelot over the PDF and return display rows. Slow (minutes on ARM)."""
    import camelot  # heavy import, only needed in the worker thread
    import pypdfium2

    tables = camelot.read_pdf(io.BytesIO(pdf), pages="1-end", flag_size=True)
    rows = parse_tables([(t.page, t.df.values.tolist()) for t in tables])

    pages = {page for row in rows for page, _ in row["refs"]}
    page_texts = {}
    if pages:
        doc = pypdfium2.PdfDocument(pdf)
        try:
            for page in pages:
                page_texts[page] = doc[page - 1].get_textpage().get_text_range()
        finally:
            doc.close()
    attach_footnotes(rows, page_texts)
    return rows


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


def _validators(response: requests.Response) -> dict:
    headers = {}
    if etag := response.headers.get("ETag"):
        headers["If-None-Match"] = etag
    if modified := response.headers.get("Last-Modified"):
        headers["If-Modified-Since"] = modified
    return headers


def _get(url: str, headers: dict, timeout: float, max_bytes: int | None = None):
    try:
        response = _session.get(url, headers=headers, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        raise UpstreamError(f"Could not reach {urlsplit(url).netloc}") from exc
    with response:
        if response.status_code == 304:
            return response, None
        if response.status_code != 200:
            raise UpstreamError(f"{urlsplit(url).netloc} returned HTTP {response.status_code}")
        body = bytearray()
        for chunk in response.iter_content(64 * 1024):
            body += chunk
            if max_bytes and len(body) > max_bytes:
                raise UpstreamError("The PDF is larger than expected")
        return response, bytes(body)


def find_pdf_url(html: bytes, page_url: str = PAGE_URL) -> str:
    """Return the absolute URL of the PDF linked from the FSANZ page."""
    link = BeautifulSoup(html, "html.parser").find("a", class_="file-download-pdf")
    if not link or not link.get("href"):
        raise UpstreamError("Could not find the PDF link on the FSANZ page")
    url = urljoin(page_url, link["href"])
    parts, expected = urlsplit(url), urlsplit(page_url)
    if parts.scheme != "https" or parts.netloc != expected.netloc:
        raise UpstreamError("The PDF link points somewhere unexpected")
    return url


def title_from_url(url: str) -> str:
    return PurePosixPath(unquote(urlsplit(url).path)).stem


def check_for_update(data: dict | None, config) -> tuple[str, dict]:
    """Ask FSANZ whether anything changed since ``data`` was built.

    Returns ``("unchanged", {"page_validators": ...})`` or
    ``("changed", {"pdf": bytes, "pdf_url": ..., "page_validators": ...,
    "pdf_validators": ...})``. Validators are only sent when there is cached
    data to fall back on, so a 304 always has something to show.
    """
    timeout = config["NFR_HTTP_TIMEOUT"]
    page_headers = data["page_validators"] if data else {}
    page, html = _get(PAGE_URL, page_headers, timeout)
    if html is None:
        return "unchanged", {"page_validators": page_headers}

    page_validators = _validators(page)
    pdf_url = find_pdf_url(html)
    pdf_headers = data["pdf_validators"] if data and data["pdf_url"] == pdf_url else {}
    pdf, body = _get(pdf_url, pdf_headers, timeout, config["NFR_MAX_PDF_BYTES"])
    if body is None:
        return "unchanged", {"page_validators": page_validators}
    if not body.startswith(b"%PDF-"):
        raise UpstreamError("The download is not a PDF")
    return "changed", {
        "pdf": body,
        "pdf_url": pdf_url,
        "page_validators": page_validators,
        "pdf_validators": _validators(pdf),
    }


# --------------------------------------------------------------------------
# Background processing
# --------------------------------------------------------------------------

_start_lock = threading.Lock()


def get_status(cache) -> dict:
    return cache.get(STATUS_KEY) or {"state": "idle"}


def start_processing(app, cache, update: dict) -> bool:
    """Parse the new PDF in a background thread. False if one is already running."""
    with _start_lock:
        if get_status(cache)["state"] == "processing":
            return False
        cache.set(
            STATUS_KEY,
            {"state": "processing", "since": time.time()},
            timeout=app.config["NFR_PROCESSING_TIMEOUT"],
        )
    threading.Thread(target=_process, args=(app, cache, update), name="nfr-process", daemon=True).start()
    return True


def _process(app, cache, update: dict) -> None:
    with app.app_context():
        started = time.monotonic()
        try:
            rows = extract_rows(update["pdf"])
        except Exception:
            app.logger.exception("Processing %s failed", update["pdf_url"])
            cache.set(STATUS_KEY, {"state": "error"}, timeout=app.config["NFR_RETRY_AFTER"])
            return
        now = time.time()
        cache.set(
            DATA_KEY,
            {
                "title": title_from_url(update["pdf_url"]),
                "pdf_url": update["pdf_url"],
                "rows": rows,
                "page_validators": update["page_validators"],
                "pdf_validators": update["pdf_validators"],
                "updated_at": now,
                "checked_at": now,
            },
            timeout=app.config["NFR_DATA_TTL"],
        )
        cache.set(STATUS_KEY, {"state": "complete"}, timeout=600)
        app.logger.info(
            "Parsed %d rows from %s in %.0fs",
            len(rows),
            update["pdf_url"],
            time.monotonic() - started,
        )
