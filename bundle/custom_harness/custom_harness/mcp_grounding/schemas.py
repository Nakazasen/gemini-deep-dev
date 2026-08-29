"""Pydantic V2 schemas used by the canonical Deep Dev MCP server."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class FetchDocInput(BaseModel):
    """Input payload for the `fetch_doc` MCP tool."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target: str = Field(
        ...,
        min_length=1,
        description="Target document URI, URL (e.g. 'https://antigravity.google/docs/mcp'), relative workspace path ('docs/rules.md'), or doc_id ('doc:live:mcp').",
    )
    section: Optional[str] = Field(
        default=None,
        description="Optional heading name or anchor (e.g., 'Configuration Schema' or '#stdio-transport') to retrieve only that section.",
    )
    max_length: Optional[int] = Field(
        default=50000,
        gt=0,
        description="Maximum character length of content to return before truncation.",
    )
    refresh_cache: bool = Field(
        default=False,
        description="If True, bypasses local disk cache and forces a fresh network fetch for live URLs.",
    )


class DocContentResult(BaseModel):
    """Structured response for retrieved documentation content."""

    model_config = ConfigDict(extra="ignore")

    doc_id: str = Field(
        ...,
        description="Canonical document ID, e.g. 'doc:local:docs/rules.md' or 'doc:live:mcp'",
    )
    title: str = Field(
        ...,
        description="Extracted title from markdown frontmatter or top # heading",
    )
    content: str = Field(
        ...,
        description="Markdown body content or extracted section content",
    )
    sha256: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="64-character lowercase hexadecimal SHA-256 hash of canonical content",
    )
    source_type: Literal["local", "live_fresh", "live_cached", "bundle_fallback"] = Field(
        ...,
        description="Provenance source category",
    )
    uri: str = Field(
        ...,
        description="Resolved local file path or public HTTP URL",
    )
    available_sections: List[str] = Field(
        default_factory=list,
        description="List of markdown heading titles present in document",
    )
    last_modified: Optional[str] = Field(
        default=None,
        description="ISO 8601 timestamp of file modification or HTTP Last-Modified",
    )
    char_count: int = Field(
        ...,
        description="Length of returned content in characters",
    )
    truncated: bool = Field(
        default=False,
        description="True if content was truncated by max_length",
    )
    section_found: Optional[bool] = Field(
        default=None,
        description="True if requested section heading was located in the document",
    )

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if len(v_clean) != 64 or not all(c in "0123456789abcdef" for c in v_clean):
            raise ValueError(f"Invalid SHA-256 hex string: {v}")
        return v_clean


class SearchDocsInput(BaseModel):
    """Input payload for the `search_docs` MCP tool."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(
        ...,
        min_length=1,
        description="Search terms or natural language query (e.g., 'mcp config stdio transport', 'pydantic structured outputs')",
    )
    sources: List[Literal["local", "live", "builtin"]] = Field(
        default=["local", "live", "builtin"],
        description="Source categories to search within",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of search results to return",
    )
    min_score: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Minimum relevance score threshold (0.0 to 1.0)",
    )
    include_snippets: bool = Field(
        default=True,
        description="Whether to generate and include contextual excerpt snippets",
    )


class SearchResultItem(BaseModel):
    """Individual document search match item."""

    model_config = ConfigDict(extra="ignore")

    doc_id: str = Field(..., description="Unique document identifier")
    title: str = Field(..., description="Document title")
    uri: str = Field(..., description="File path or URL")
    source_type: str = Field(..., description="Source origin: 'local', 'live', or 'builtin'")
    score: float = Field(..., ge=0.0, le=1.0, description="Relevance score from 0.0 to 1.0")
    sha256: str = Field(..., description="SHA-256 checksum of document content")
    matched_section: Optional[str] = Field(default=None, description="Heading title of the best matching section")
    snippet: str = Field(default="", description="Contextual excerpt showing matching terms")

    @property
    def doc_hash(self) -> str:
        return self.sha256

    @property
    def source_path(self) -> str:
        return self.uri


class DocMatch(BaseModel):
    """Rich document match details for retrieval rankings."""

    model_config = ConfigDict(extra="ignore")

    doc_id: str
    title: str
    uri: str
    source_type: str
    score: float
    sha256: str
    matched_section: Optional[str] = None
    snippet: str = ""
    highlight_terms: List[str] = Field(default_factory=list)


class SearchDocsResult(BaseModel):
    """Structured response for search queries across documents."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(..., description="Original search query")
    total_matches: int = Field(..., description="Number of results matching query and threshold")
    results: List[SearchResultItem] = Field(default_factory=list, description="Ranked list of search results")


class CitationItem(BaseModel):
    """Structured citation referencing an authoritative source document."""

    model_config = ConfigDict(extra="ignore")

    source_title: str = Field(..., description="Title of the cited source document")
    uri: str = Field(..., description="File path or URL of source")
    sha256_short: str = Field(..., min_length=6, max_length=16, description="Short hex prefix of SHA-256 hash")
    sha256_full: str = Field(..., min_length=64, max_length=64, description="Complete 64-character SHA-256 hash")
    excerpt: str = Field(default="", description="Relevant text snippet cited")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Citation confidence score")


class TokenUsage(BaseModel):
    """Token consumption metrics."""

    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class GroundingMetadata(BaseModel):
    """Metadata tracking grounding context injected into prompts."""

    model_config = ConfigDict(extra="ignore")

    injected_docs: List[DocContentResult] = Field(default_factory=list)
    doc_hashes: Dict[str, str] = Field(default_factory=dict)
    delimiter_block_length: int = 0
    total_grounded_chars: int = 0
    timestamp: Optional[str] = None


class GenAIQueryInput(BaseModel):
    """Input payload for the `genai_query` MCP tool."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt: str = Field(
        ...,
        min_length=1,
        description="Natural language instruction or code generation request",
    )
    doc_ids_or_queries: Optional[List[str]] = Field(
        default=None,
        description="Explicit list of doc_ids or search queries to retrieve and ground context from",
    )
    model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model identifier (e.g., 'gemini-2.5-flash', 'gemini-1.5-flash')",
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Generation temperature; default 0.0 for deterministic output",
    )
    enforce_citations: bool = Field(
        default=True,
        description="If True, verifies that the model response contains valid citations matching injected doc hashes",
    )


class GenAIQueryResult(BaseModel):
    """Structured response from grounded generation queries."""

    model_config = ConfigDict(extra="ignore")

    response: str = Field(..., description="Generated text, code, or answer")
    grounded: bool = Field(..., description="Whether grounding documentation was injected")
    citations: List[CitationItem] = Field(default_factory=list, description="List of verified citations")
    doc_hashes: Dict[str, str] = Field(default_factory=dict, description="Map of doc_id -> sha256 hash")
    model_used: str = Field(..., description="Name of the model executed")
    token_usage: Optional[TokenUsage] = Field(default=None, description="Token consumption metrics")
    citation_check_passed: bool = Field(default=True, description="Whether citation verification passed")
    citation_errors: Optional[List[str]] = Field(default=None, description="List of citation validation error messages if any")
