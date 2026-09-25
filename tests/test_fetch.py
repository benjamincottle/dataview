import pytest

from dataview import nfr

CONFIG = {"NFR_HTTP_TIMEOUT": 5, "NFR_MAX_PDF_BYTES": 1024}
PAGE = b'<a class="file-download-pdf" href="/files/views.pdf">PDF</a>'
PDF_URL = "https://www.foodstandards.gov.au/files/views.pdf"


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i : i + size]


@pytest.fixture
def upstream(monkeypatch):
    """Map URL -> FakeResponse and record the headers each request sent."""
    responses, sent = {}, {}

    def get(url, headers, timeout, stream):
        sent[url] = headers
        return responses[url]

    monkeypatch.setattr(nfr._session, "get", get)
    return responses, sent


def test_first_fetch_downloads_without_validators(upstream):
    responses, sent = upstream
    responses[nfr.PAGE_URL] = FakeResponse(body=PAGE, headers={"ETag": '"p1"'})
    responses[PDF_URL] = FakeResponse(body=b"%PDF-1.7 ...", headers={"Last-Modified": "yesterday"})

    result, update = nfr.check_for_update(None, CONFIG)

    assert result == "changed"
    assert sent == {nfr.PAGE_URL: {}, PDF_URL: {}}
    assert update["pdf"] == b"%PDF-1.7 ..."
    assert update["pdf_url"] == PDF_URL
    assert update["page_validators"] == {"If-None-Match": '"p1"'}
    assert update["pdf_validators"] == {"If-Modified-Since": "yesterday"}


def test_unchanged_page_short_circuits(upstream):
    responses, sent = upstream
    responses[nfr.PAGE_URL] = FakeResponse(status=304)
    data = {"page_validators": {"If-None-Match": '"p1"'}}

    assert nfr.check_for_update(data, CONFIG) == ("unchanged", {"page_validators": data["page_validators"]})
    assert sent == {nfr.PAGE_URL: {"If-None-Match": '"p1"'}}


def test_changed_page_with_same_pdf_uses_pdf_validators(upstream):
    responses, sent = upstream
    responses[nfr.PAGE_URL] = FakeResponse(body=PAGE, headers={"ETag": '"p2"'})
    responses[PDF_URL] = FakeResponse(status=304)
    data = {
        "page_validators": {"If-None-Match": '"p1"'},
        "pdf_url": PDF_URL,
        "pdf_validators": {"If-None-Match": '"f1"'},
    }

    result, update = nfr.check_for_update(data, CONFIG)

    assert result == "unchanged"
    assert update == {"page_validators": {"If-None-Match": '"p2"'}}
    assert sent[PDF_URL] == {"If-None-Match": '"f1"'}


@pytest.mark.parametrize(
    "pdf_response",
    [
        FakeResponse(status=404),
        FakeResponse(body=b"<html>not a pdf</html>"),
        FakeResponse(body=b"%PDF-" + b"x" * 2048),
    ],
    ids=["http-error", "not-a-pdf", "too-large"],
)
def test_bad_pdf_downloads_raise(upstream, pdf_response):
    responses, _ = upstream
    responses[nfr.PAGE_URL] = FakeResponse(body=PAGE)
    responses[PDF_URL] = pdf_response
    with pytest.raises(nfr.UpstreamError):
        nfr.check_for_update(None, CONFIG)


def test_network_errors_become_upstream_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise nfr.requests.ConnectionError("down")

    monkeypatch.setattr(nfr._session, "get", boom)
    with pytest.raises(nfr.UpstreamError, match="Could not reach"):
        nfr.check_for_update(None, CONFIG)
