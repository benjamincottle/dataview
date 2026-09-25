import pytest

from dataview import create_app, nfr


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "CACHE_TYPE": "SimpleCache"})
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def cache(app):
    from dataview import cache

    with app.app_context():
        cache.clear()
        yield cache


@pytest.fixture
def sync_threads(monkeypatch):
    """Run background processing inline so tests are deterministic."""

    class InlineThread:
        def __init__(self, target, args=(), **kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(nfr.threading, "Thread", InlineThread)


def make_row(food="Abalone", outcome=("Not novel food",), justification="Fine."):
    return {
        "food": [("text", food)],
        "outcome": [{"text": o, "tone": nfr.outcome_tone(o)} for o in outcome],
        "justification": [("text", justification)],
        "page": 1,
        "notes": [],
    }


def make_data(rows=None, checked_at=0.0, **extra):
    data = {
        "title": "Record of Views",
        "pdf_url": "https://www.foodstandards.gov.au/files/Record%20of%20Views.pdf",
        "rows": rows or [make_row()],
        "page_validators": {"If-None-Match": '"page"'},
        "pdf_validators": {"If-None-Match": '"pdf"'},
        "updated_at": checked_at,
        "checked_at": checked_at,
    }
    data.update(extra)
    return data
