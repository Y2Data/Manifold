import pytest

pytest.importorskip("trafilatura")

from browser_service import fetch  # noqa: E402

_SUBSTANTIAL_HTML = "<html><body><article>" + ("Real page content, sentence by sentence. " * 40) + "</article></body></html>"
_THIN_HTML = "<html><body><div id='root'></div></body></html>"
_RENDERED_HTML = "<html><body><article>" + ("Rendered by a real browser. " * 40) + "</article></body></html>"


async def test_fetch_page_stays_on_http_when_content_is_substantial(monkeypatch):
    async def fake_http(url):
        return _SUBSTANTIAL_HTML

    async def fake_browser(url):
        raise AssertionError("should not escalate when the http response already has real content")

    monkeypatch.setattr(fetch, "_fetch_via_http", fake_http)
    monkeypatch.setattr(fetch, "_fetch_via_browser", fake_browser)

    result = await fetch.fetch_page("https://example.com/article")
    assert result.method == "http"
    assert result.html == _SUBSTANTIAL_HTML


async def test_fetch_page_escalates_to_browser_on_thin_content(monkeypatch):
    async def fake_http(url):
        return _THIN_HTML

    async def fake_browser(url):
        return _RENDERED_HTML

    monkeypatch.setattr(fetch, "_fetch_via_http", fake_http)
    monkeypatch.setattr(fetch, "_fetch_via_browser", fake_browser)

    result = await fetch.fetch_page("https://example.com/spa")
    assert result.method == "browser"
    assert result.html == _RENDERED_HTML


async def test_fetch_page_escalates_to_browser_on_non_html_response(monkeypatch):
    async def fake_http(url):
        return None  # e.g. non-2xx or non-HTML content-type

    async def fake_browser(url):
        return _RENDERED_HTML

    monkeypatch.setattr(fetch, "_fetch_via_http", fake_http)
    monkeypatch.setattr(fetch, "_fetch_via_browser", fake_browser)

    result = await fetch.fetch_page("https://example.com/blocked")
    assert result.method == "browser"
    assert result.html == _RENDERED_HTML
