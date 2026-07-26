import pytest

pytest.importorskip("lxml")

from browser_service.search import _parse_results  # noqa: E402

_DDG_HTML = """
<div class="results">
  <div class="result results_links results_links_deep web-result">
    <div class="result__body">
      <h2 class="result__title">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&amp;rut=abc">
          Example Domain
        </a>
      </h2>
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage">
        This domain is for use in illustrative examples.
      </a>
    </div>
  </div>
</div>
"""


def test_parse_results_extracts_title_snippet_and_unwraps_redirect_link():
    results = _parse_results(_DDG_HTML)
    assert len(results) == 1
    result = results[0]
    assert result.url == "https://example.com/page"
    assert result.title == "Example Domain"
    assert "illustrative examples" in result.snippet


def test_parse_results_returns_empty_list_for_no_results():
    assert _parse_results("<div class='results'>no results found.</div>") == []
