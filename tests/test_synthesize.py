from browser_service import synthesize
from browser_service.models import ExtractedPage, Source


async def test_synthesize_numbers_sources_and_truncates_per_source_budget(monkeypatch):
    captured = {}

    async def fake_run_raw_claude(prompt, model, timeout_s=None):
        captured["prompt"] = prompt
        captured["model"] = model
        return "The sky is blue [1] and grass is green [2]."

    monkeypatch.setattr(synthesize, "run_raw_claude", fake_run_raw_claude)

    pages = [
        ExtractedPage(url="https://a.example", title="A", text="x" * 5000),
        ExtractedPage(url="https://b.example", title="B", text="grass is green"),
    ]
    answer, sources = await synthesize.synthesize("what color is the sky?", pages)

    assert answer == "The sky is blue [1] and grass is green [2]."
    assert sources == [
        Source(n=1, url="https://a.example", title="A"),
        Source(n=2, url="https://b.example", title="B"),
    ]
    assert "[1] A (https://a.example)" in captured["prompt"]
    assert "[2] B (https://b.example)" in captured["prompt"]
    assert captured["model"] == "sonnet"
    # per-source truncation keeps a single huge page from blowing out the prompt
    source_1_block = captured["prompt"].split("[1] A (https://a.example)\n", 1)[1].split("\n\n[2]")[0]
    assert len(source_1_block) == synthesize._PER_SOURCE_CHAR_BUDGET


async def test_synthesize_short_circuits_when_no_pages(monkeypatch):
    called = False

    async def fake_run_raw_claude(*args, **kwargs):
        nonlocal called
        called = True
        return "should not be reached"

    monkeypatch.setattr(synthesize, "run_raw_claude", fake_run_raw_claude)

    answer, sources = await synthesize.synthesize("anything", [])

    assert not called
    assert sources == []
    assert "No usable sources" in answer
