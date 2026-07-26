import pytest

pytest.importorskip("trafilatura")

from browser_service.extract import extract_text  # noqa: E402

_ARTICLE_HTML = """
<html>
<head><title>Test Article Title</title></head>
<body>
<article>
<h1>Test Article Title</h1>
<p>""" + ("This is a real sentence with actual article content. " * 40) + """</p>
</article>
</body>
</html>
"""

_SPA_SHELL_HTML = """
<html>
<head><title>App</title></head>
<body>
<div id="root"></div>
<script src="/app.js"></script>
</body>
</html>
"""


def test_extract_text_returns_content_for_article_html():
    page = extract_text(_ARTICLE_HTML, "https://example.com/article")
    assert page is not None
    assert page.url == "https://example.com/article"
    assert len(page.text) > 200
    assert "real sentence" in page.text


def test_extract_text_returns_none_for_empty_js_shell():
    page = extract_text(_SPA_SHELL_HTML, "https://example.com/app")
    assert page is None
