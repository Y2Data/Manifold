import pytest

pytest.importorskip("trafilatura")

from fastapi.testclient import TestClient  # noqa: E402

from browser_service import main as browser_main  # noqa: E402
from browser_service.models import ExtractedPage, FetchedPage, SearchResult, Source  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    async def fake_search(question):
        return [SearchResult(url="https://a.example", title="A", snippet="snippet a")]

    async def fake_fetch_page(url):
        return FetchedPage(html="<html></html>", method="http")

    def fake_extract_text(html, url):
        return ExtractedPage(url=url, title="A", text="some real content " * 20)

    async def fake_synthesize(question, pages):
        return "answer text [1]", [Source(n=1, url=pages[0].url, title=pages[0].title)]

    monkeypatch.setattr(browser_main, "search", fake_search)
    monkeypatch.setattr(browser_main, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(browser_main, "extract_text", fake_extract_text)
    monkeypatch.setattr(browser_main, "synthesize", fake_synthesize)

    return TestClient(browser_main.app)


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "up"}


def test_research_endpoint_returns_answer_and_sources(client):
    resp = client.post("/research", json={"question": "what is a?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "answer text [1]"
    assert body["sources"] == [{"n": 1, "url": "https://a.example", "title": "A"}]
    assert isinstance(body["latency_ms"], int)


def test_research_endpoint_502s_when_search_finds_nothing(client, monkeypatch):
    async def empty_search(question):
        return []

    monkeypatch.setattr(browser_main, "search", empty_search)

    resp = client.post("/research", json={"question": "obscure query"})
    assert resp.status_code == 502
