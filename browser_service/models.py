from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str


@dataclass
class FetchedPage:
    html: str
    method: str  # "http" | "browser" — which path actually produced this HTML


@dataclass
class ExtractedPage:
    url: str
    title: str
    text: str


class ResearchRequest(BaseModel):
    question: str


class Source(BaseModel):
    n: int
    url: str
    title: str


class ResearchResponse(BaseModel):
    answer: str
    sources: list[Source]
    latency_ms: int
