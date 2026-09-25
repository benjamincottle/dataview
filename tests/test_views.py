import re
import time

import pytest
from conftest import make_data, make_row

from dataview import nfr

CHANGED = (
    "changed",
    {
        "pdf": b"%PDF-",
        "pdf_url": "https://www.foodstandards.gov.au/files/Record%20of%20Views.pdf",
        "page_validators": {},
        "pdf_validators": {},
    },
)


def fail_if_called(*args, **kwargs):
    raise AssertionError("should not contact FSANZ")


def test_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Novel foods" in response.data


def test_security_headers(client):
    response = client.get("/")
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_pages_have_no_inline_script_or_style(client, cache):
    cache.set(nfr.DATA_KEY, make_data(checked_at=time.time()))
    for path in ("/", "/nfr", "/missing"):
        html = client.get(path).get_data(as_text=True)
        assert "<script>" not in html
        assert "<style" not in html
        assert " style=" not in html
        assert " on" + "input=" not in html


def test_404_uses_error_page(client):
    response = client.get("/missing")
    assert response.status_code == 404
    assert "doesn&#39;t exist" in response.get_data(as_text=True)


def test_healthz(client):
    assert client.get("/healthz").json == {"status": "ok"}


def test_first_visit_processes_in_background(client, cache, monkeypatch, sync_threads):
    monkeypatch.setattr(nfr, "check_for_update", lambda data, config: CHANGED)
    monkeypatch.setattr(nfr, "extract_rows", lambda pdf: [make_row(food="Kombucha")])

    response = client.get("/nfr")
    assert response.status_code == 202
    assert b"Preparing the latest data" in response.data

    assert client.get("/nfr/status").json == {"state": "complete"}

    monkeypatch.setattr(nfr, "check_for_update", fail_if_called)
    page = client.get("/nfr")
    assert page.status_code == 200
    assert b"Kombucha" in page.data
    assert b"Record of Views" in page.data


def test_processing_failure_backs_off(client, cache, monkeypatch, sync_threads):
    monkeypatch.setattr(nfr, "check_for_update", lambda data, config: CHANGED)

    def broken(pdf):
        raise nfr.ParseError("layout changed")

    monkeypatch.setattr(nfr, "extract_rows", broken)
    client.get("/nfr")
    assert client.get("/nfr/status").json == {"state": "error"}

    monkeypatch.setattr(nfr, "check_for_update", fail_if_called)
    response = client.get("/nfr")
    assert response.status_code == 503
    assert b"could not be processed" in response.data


def test_request_during_processing_does_not_start_another(client, cache, monkeypatch):
    cache.set(nfr.STATUS_KEY, {"state": "processing"})
    monkeypatch.setattr(nfr, "check_for_update", fail_if_called)
    assert client.get("/nfr").status_code == 202
    assert client.get("/nfr/status").json == {"state": "processing"}


def test_fresh_data_is_served_without_contacting_fsanz(client, cache, monkeypatch):
    cache.set(nfr.DATA_KEY, make_data(checked_at=time.time()))
    monkeypatch.setattr(nfr, "check_for_update", fail_if_called)
    response = client.get("/nfr")
    assert response.status_code == 200
    assert b"Abalone" in response.data


def test_unchanged_upstream_refreshes_checked_at(client, cache, monkeypatch):
    cache.set(nfr.DATA_KEY, make_data(checked_at=0))
    monkeypatch.setattr(
        nfr, "check_for_update", lambda data, config: ("unchanged", {"page_validators": {"x": "y"}})
    )
    assert client.get("/nfr").status_code == 200
    data = cache.get(nfr.DATA_KEY)
    assert data["checked_at"] > 0
    assert data["page_validators"] == {"x": "y"}


def test_upstream_error_serves_stale_data(client, cache, monkeypatch):
    cache.set(nfr.DATA_KEY, make_data(checked_at=0))

    def down(data, config):
        raise nfr.UpstreamError("FSANZ returned HTTP 500")

    monkeypatch.setattr(nfr, "check_for_update", down)
    response = client.get("/nfr")
    assert response.status_code == 200
    assert b"Abalone" in response.data
    assert b"Couldn&#39;t check FSANZ for updates (FSANZ returned HTTP 500)" in response.data


def test_upstream_error_without_data_is_502(client, cache, monkeypatch):
    def down(data, config):
        raise nfr.UpstreamError("Could not reach www.foodstandards.gov.au")

    monkeypatch.setattr(nfr, "check_for_update", down)
    response = client.get("/nfr")
    assert response.status_code == 502
    assert b"Could not reach" in response.data


def test_new_version_keeps_serving_old_data(client, cache, monkeypatch):
    cache.set(nfr.DATA_KEY, make_data(checked_at=0))
    monkeypatch.setattr(nfr, "check_for_update", lambda data, config: CHANGED)
    monkeypatch.setattr(nfr, "start_processing", lambda app, cache, update: True)
    response = client.get("/nfr")
    assert response.status_code == 200
    assert b"Abalone" in response.data
    assert b"being processed" in response.data


def test_pdf_text_is_escaped(client, cache):
    row = make_row(food='<img src=x onerror="alert(1)">', justification="<script>alert(2)</script>")
    row["outcome"] = [{"text": "<b>Novel</b>", "tone": "novel"}]
    cache.set(nfr.DATA_KEY, make_data(rows=[row], checked_at=time.time()))
    html = client.get("/nfr").get_data(as_text=True)
    assert "<img src=x" not in html
    assert "<script>alert" not in html
    assert "<b>Novel" not in html
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in html


def test_footnotes_and_superscripts_render(client, cache):
    row = make_row()
    row["justification"] = [("text", "1 x 10"), ("sup", "9"), ("text", " CFU"), ("fn", "3")]
    row["notes"] = [{"n": "3", "page": 14, "text": "Updated in 2021."}]
    cache.set(nfr.DATA_KEY, make_data(rows=[row], checked_at=time.time()))
    html = client.get("/nfr").get_data(as_text=True)
    assert "1 x 10<sup>9</sup> CFU" in html
    assert 'href="#r1-note-3"' in html
    assert 'id="r1-note-3"' in html
    assert "Updated in 2021." in html
    assert "#page=14" in html


@pytest.mark.parametrize(
    ("query", "shown"),
    [
        ("", {"Abalone", "Kelp powder"}),
        ("?q=kelp", {"Kelp powder"}),
        ("?q=powder+kelp", {"Kelp powder"}),
        ("?food=abalone", {"Abalone"}),
        ("?outcome=novel", {"Kelp powder"}),
        ("?outcome=not-novel", {"Abalone"}),
        ("?outcome=bogus", {"Abalone", "Kelp powder"}),
        ("?justification=iodine", {"Kelp powder"}),
        ("?q=nothing-matches", set()),
        ("?q=odine", set()),
        ("?q=(iod", {"Kelp powder"}),
    ],
)
def test_server_side_filtering(client, cache, query, shown):
    rows = [
        make_row(food="Abalone", outcome=("Not novel food",), justification="Mollusc."),
        make_row(food="Kelp powder", outcome=("Novel food",), justification="High (iodine)."),
    ]
    cache.set(nfr.DATA_KEY, make_data(rows=rows, checked_at=time.time()))
    html = client.get("/nfr" + query).get_data(as_text=True)
    rendered = re.findall(r'<tr id="r\d+" data-tones="[^"]*" ?(hidden)?>\s*<th[^>]*>([^<]*)</th>', html)
    assert {name for hidden, name in rendered if not hidden} == shown
    assert len(rendered) == 2
    assert f"Showing {len(shown)} of 2 entries" in html
