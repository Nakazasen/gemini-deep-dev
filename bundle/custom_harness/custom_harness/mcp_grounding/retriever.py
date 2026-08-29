"""Local workspace scanner, search indexer, and multi-source document retriever."""

from __future__ import annotations

import math
import os
from pathlib import Path
import re
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

try:
    from rapidfuzz import fuzz
except ImportError:
    # Fallback simple fuzzy ratio if rapidfuzz is not installed
    class _FuzzFallback:
        @staticmethod
        def token_set_ratio(s1: str, s2: str) -> float:
            set1, set2 = set(s1.lower().split()), set(s2.lower().split())
            if not set1 or not set2:
                return 0.0
            intersection = set1.intersection(set2)
            return float(len(intersection)) / float(max(len(set1), len(set2))) * 100.0

        @staticmethod
        def partial_ratio(s1: str, s2: str) -> float:
            return 100.0 if s1.lower() in s2.lower() or s2.lower() in s1.lower() else 0.0

    fuzz = _FuzzFallback()  # type: ignore

from custom_harness.mcp_grounding.cache import DocCache
from custom_harness.mcp_grounding.hasher import compute_canonical_hash, normalize_canonical_text
from custom_harness.mcp_grounding.live_fetcher import (
    LiveDocFetcher,
    extract_markdown_sections,
    extract_markdown_title,
    extract_section_content,
    truncate_content,
    SITEMAP_ROUTES,
    EMBEDDED_DOCS,
)
from custom_harness.mcp_grounding.schemas import (
    DocContentResult,
    DocMatch,
    SearchDocsResult,
    SearchResultItem,
)


EXCLUDE_DIRS: Set[str] = {
    "node_modules",
    ".git",
    ".github",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".brain",
    "brain",
    ".system_generated",
    "crashes",
    "browser_recordings",
    "conversations",
    "global_workflows",
    "global_workflows_backup_timestamp_260122",
    "annotations",
    "html_artifacts",
    "code_tracker",
    "playground",
    "scratch",
    ".understand-anything",
    ".tmp_admin_debug",
    ".tmp_debug_fa",
    ".tmp_test_artifacts",
    ".tmp_uniform_e2e",
    "OUTPUT_FY2027",
    "reference_outputs",
    "RUN_HISTORY",
    ".pytest_cache",
    ".specify",
    "data",
    "raw",
    "processed",
    "outputs",
    "packaging",
    "reports",
    "installer",
    "assets",
}


def normalize_search_text(text: str) -> str:
    """Normalize text for search by lowercasing, stripping accents, and removing punctuation."""
    if not text:
        return ""
    # Unicode decomposition to strip accents
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Replace non-alphanumeric with spaces
    cleaned = re.sub(r"[^\w\s-]", " ", stripped.lower())
    return " ".join(cleaned.split())


def extract_search_snippet(content: str, query_terms: List[str], snippet_len: int = 220) -> str:
    """Find and format the most relevant snippet window containing query terms."""
    if not content:
        return ""

    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
    body_text = " ".join(lines)
    if not body_text:
        body_text = content[:snippet_len]

    body_norm = normalize_search_text(body_text)

    # Find earliest term position
    best_pos = -1
    for term in query_terms:
        pos = body_norm.find(term)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos

    if best_pos == -1:
        # Fall back to first characters
        snippet = body_text[:snippet_len].strip()
        return snippet + ("..." if len(body_text) > snippet_len else "")

    # Calculate window around best_pos
    half = snippet_len // 2
    start = max(0, best_pos - half)
    end = min(len(body_text), start + snippet_len)

    snippet = body_text[start:end].strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(body_text) else ""
    return f"{prefix}{snippet}{suffix}"


class IndexedDocument:
    """In-memory index entry for a local or cached document."""

    def __init__(
        self,
        doc_id: str,
        title: str,
        content: str,
        uri: str,
        source_type: str,
        sha256: str,
        sections: List[str],
        file_path: Optional[Path] = None,
        last_modified: Optional[str] = None,
    ):
        self.doc_id = doc_id
        self.title = title
        self.content = content
        self.uri = uri
        self.source_type = source_type
        self.sha256 = sha256
        self.sections = sections
        self.file_path = file_path
        self.last_modified = last_modified

        # Pre-computed normalized text for search
        self.norm_title = normalize_search_text(title)
        self.norm_sections = normalize_search_text(" ".join(sections))
        self.norm_content = normalize_search_text(content)
        self.tokens = set(self.norm_content.split()) | set(self.norm_title.split())


class DocRetriever:
    """Multi-source document retriever and search engine."""

    def __init__(
        self,
        workspace_roots: Optional[List[Union[str, Path]]] = None,
        cache: Optional[DocCache] = None,
        live_fetcher: Optional[LiveDocFetcher] = None,
        workspace_root: Optional[Union[str, Path]] = None,
        index_on_init: bool = True,
    ):
        self.cache = cache or DocCache()
        self.live_fetcher = live_fetcher or LiveDocFetcher(cache=self.cache)

        # Permitted workspace roots for local scanning and path validation
        configured_roots = [
            Path(value).expanduser().resolve()
            for value in os.environ.get("DEEP_DEV_DOC_ROOTS", "").split(os.pathsep)
            if value.strip()
        ]
        default_roots = configured_roots or [Path.cwd().resolve()]
        if workspace_root is not None and workspace_roots is None:
            workspace_roots = [Path(workspace_root).resolve()]
        elif workspace_roots is not None:
            workspace_roots = [Path(p).resolve() for p in workspace_roots]

        self.workspace_roots = (
            [p for p in default_roots if p.is_dir()]
            if workspace_roots is None
            else [p for p in workspace_roots if p.is_dir()]
        )
        self._local_index: Dict[str, IndexedDocument] = {}
        self._indexed = False
        if index_on_init:
            self.reindex_local_docs()

    def _ensure_index(self) -> None:
        if not self._indexed:
            self.reindex_local_docs()

    def is_path_safe(self, path: Path) -> bool:
        """Verify that path does not escape allowed workspace roots (Fail-Closed)."""
        resolved = path.resolve()
        for root in self.workspace_roots:
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False

    def reindex_local_docs(self) -> int:
        """Scan and index all permitted markdown documents in workspace roots."""
        self._local_index.clear()
        indexed_count = 0

        for root in self.workspace_roots:
            if not root.is_dir():
                continue

            for dirpath, dirnames, filenames in os.walk(root):
                # Prune excluded directories
                dirnames[:] = [
                    d for d in dirnames
                    if d not in EXCLUDE_DIRS
                    and d.lower() not in EXCLUDE_DIRS
                    and (not d.startswith(".") or d in {".agent", ".agents", ".gemini"})
                ]

                for fname in filenames:
                    if not fname.endswith(".md") and fname not in {"AGENTS.md", "GEMINI.md", ".antigravityrules"}:
                        continue

                    full_path = Path(dirpath) / fname
                    if not self.is_path_safe(full_path):
                        continue

                    try:
                        content = full_path.read_text(encoding="utf-8")
                    except Exception:
                        try:
                            content = full_path.read_text(encoding="latin-1", errors="replace")
                        except Exception:
                            continue

                    title = extract_markdown_title(content, default=fname)
                    sections = extract_markdown_sections(content)
                    sha256 = compute_canonical_hash(content)

                    # Create relative doc_id
                    try:
                        rel_path = full_path.relative_to(root).as_posix()
                        doc_id = f"doc:local:{rel_path}"
                    except ValueError:
                        doc_id = f"doc:local:{fname}"

                    try:
                        mtime = full_path.stat().st_mtime
                        import datetime
                        last_mod = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).isoformat()
                    except Exception:
                        last_mod = None

                    idx_doc = IndexedDocument(
                        doc_id=doc_id,
                        title=title,
                        content=content,
                        uri=str(full_path),
                        source_type="local",
                        sha256=sha256,
                        sections=sections,
                        file_path=full_path,
                        last_modified=last_mod,
                    )
                    self._local_index[doc_id] = idx_doc
                    indexed_count += 1

        # Also index embedded builtin docs
        for key, text in EMBEDDED_DOCS.items():
            title = extract_markdown_title(text, default=f"Antigravity {key.title()} Guide")
            sections = extract_markdown_sections(text)
            sha256 = compute_canonical_hash(text)
            doc_id = f"doc:builtin:{key}"
            self._local_index[doc_id] = IndexedDocument(
                doc_id=doc_id,
                title=title,
                content=text,
                uri=f"embedded://builtin/docs/{key}.md",
                source_type="builtin",
                sha256=sha256,
                sections=sections,
            )

        self._indexed = True
        return indexed_count

    def fetch_local_doc(
        self,
        target: str,
        section: Optional[str] = None,
        max_length: int = 50000,
    ) -> DocContentResult:
        """Retrieve local workspace document with path traversal protection."""
        self._ensure_index()
        target_clean = target.strip()
        if target_clean.startswith("doc:local:"):
            target_clean = target_clean[len("doc:local:") :]
        elif target_clean.startswith("doc:builtin:"):
            target_clean = target_clean[len("doc:builtin:") :]

        # 1. Exact match first
        target_exact = target.strip()
        matched_doc = self._local_index.get(target_exact)
        if not matched_doc:
            matched_doc = self._local_index.get(f"doc:builtin:{target_clean}")
        if not matched_doc:
            matched_doc = self._local_index.get(f"doc:local:{target_clean}")

        # 2. Substring fallback if not exact match
        if not matched_doc:
            for doc_id, doc in self._local_index.items():
                if doc.uri == target or target_clean in doc_id:
                    matched_doc = doc
                    break

        if matched_doc:
            doc = matched_doc
            content_out = doc.content
            section_found = None
            if section:
                content_out, section_found = extract_section_content(content_out, section)
            content_out, is_trunc, char_len = truncate_content(content_out, max_length)
            sha256 = compute_canonical_hash(content_out)

            stype = (
                "bundle_fallback"
                if (doc.source_type == "builtin" or doc.doc_id.startswith("doc:builtin:"))
                else ("bundle_fallback" if doc.source_type == "bundle_fallback" else "local")
            )
            return DocContentResult(
                doc_id=doc.doc_id,
                title=doc.title,
                content=content_out,
                sha256=sha256,
                source_type=stype,
                uri=doc.uri,
                available_sections=doc.sections,
                last_modified=doc.last_modified,
                char_count=char_len,
                truncated=is_trunc,
                section_found=section_found,
            )

        # Resolve local path
        target_path = Path(target_clean)
        resolved_path: Optional[Path] = None

        if target_path.is_absolute():
            if self.is_path_safe(target_path) and target_path.is_file():
                resolved_path = target_path
            elif not self.is_path_safe(target_path):
                raise PermissionError(
                    f"Access Denied: Path '{target_path}' is outside permitted workspace roots (Fail-Closed)."
                )
        else:
            # Try against each workspace root
            for root in self.workspace_roots:
                candidate = (root / target_clean).resolve()
                if self.is_path_safe(candidate) and candidate.is_file():
                    resolved_path = candidate
                    break

        if not resolved_path or not resolved_path.is_file():
            raise FileNotFoundError(f"Local document not found: {target}")

        try:
            content = resolved_path.read_text(encoding="utf-8")
        except Exception:
            content = resolved_path.read_text(encoding="latin-1", errors="replace")

        title = extract_markdown_title(content, default=resolved_path.name)
        sections = extract_markdown_sections(content)

        content_out = content
        section_found = None
        if section:
            content_out, section_found = extract_section_content(content_out, section)

        content_out, is_trunc, char_len = truncate_content(content_out, max_length)
        sha256 = compute_canonical_hash(content_out)

        import datetime

        try:
            mtime = resolved_path.stat().st_mtime
            last_mod = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).isoformat()
        except Exception:
            last_mod = None

        return DocContentResult(
            doc_id=f"doc:local:{resolved_path.name}",
            title=title,
            content=content_out,
            sha256=sha256,
            source_type="local",
            uri=str(resolved_path),
            available_sections=sections,
            last_modified=last_mod,
            char_count=char_len,
            truncated=is_trunc,
            section_found=section_found,
        )

    def fetch_doc(
        self,
        target: str,
        section: Optional[str] = None,
        max_length: int = 50000,
        refresh_cache: bool = False,
    ) -> DocContentResult:
        """Unified fetch_doc routing between live/cached and local workspace docs."""
        target_clean = target.strip()

        # Check if target is a remote URL or live doc_id
        if (
            target_clean.startswith("http://")
            or target_clean.startswith("https://")
            or target_clean.startswith("doc:live:")
            or target_clean in SITEMAP_ROUTES
            or (target_clean.startswith("docs/") and not (Path("d:/Sandbox/MP2027") / target_clean).exists())
        ):
            return self.live_fetcher.fetch_doc_sync(
                target=target_clean,
                section=section,
                max_length=max_length,
                refresh_cache=refresh_cache,
            )

        # Otherwise fetch from local workspace
        return self.fetch_local_doc(
            target=target_clean,
            section=section,
            max_length=max_length,
        )

    def search_docs(
        self,
        query: str,
        sources: Optional[List[str]] = None,
        limit: int = 5,
        min_score: float = 0.20,
        include_snippets: bool = True,
    ) -> SearchDocsResult:
        """Search indexed documents using keyword matching, BM25 TF-IDF, and RapidFuzz."""
        self._ensure_index()
        query_clean = query.strip()
        if not query_clean:
            return SearchDocsResult(query=query, total_matches=0, results=[])

        target_sources = set(sources or ["local", "live", "builtin"])
        norm_query = normalize_search_text(query_clean)
        query_terms = [t for t in norm_query.split() if len(t) > 1]
        if not query_terms:
            query_terms = [norm_query]

        scored_matches: List[Tuple[float, IndexedDocument, Optional[str], str]] = []

        for doc in self._local_index.values():
            if doc.source_type not in target_sources and "all" not in target_sources:
                continue

            score = 0.0

            # 1. Exact phrase in title (Huge boost)
            if norm_query in doc.norm_title:
                score += 0.50
            else:
                title_ratio = fuzz.token_set_ratio(norm_query, doc.norm_title) / 100.0
                score += title_ratio * 0.35

            # 2. Section heading matches
            matched_section: Optional[str] = None
            best_sec_score = 0.0
            for sec in doc.sections:
                norm_sec = normalize_search_text(sec)
                sec_ratio = fuzz.token_set_ratio(norm_query, norm_sec) / 100.0
                if sec_ratio > best_sec_score:
                    best_sec_score = sec_ratio
                    matched_section = sec

            score += best_sec_score * 0.25

            # 3. Term overlap in content (BM25 term saturation style)
            term_matches = 0
            for term in query_terms:
                if term in doc.norm_content:
                    term_matches += 1
                elif fuzz.partial_ratio(term, doc.norm_content) > 85:
                    term_matches += 0.5

            if query_terms:
                overlap_ratio = term_matches / len(query_terms)
                score += overlap_ratio * 0.40

            # Normalize final score between 0.0 and 1.0
            final_score = min(1.0, max(0.0, round(score, 4)))

            if final_score >= min_score:
                snippet = ""
                if include_snippets:
                    snippet = extract_search_snippet(doc.content, query_terms)
                scored_matches.append((final_score, doc, matched_section, snippet))

        # Sort descending by score
        scored_matches.sort(key=lambda x: x[0], reverse=True)
        top_matches = scored_matches[:limit]

        results = [
            SearchResultItem(
                doc_id=doc.doc_id,
                title=doc.title,
                uri=doc.uri,
                source_type=doc.source_type,
                score=score,
                sha256=doc.sha256,
                matched_section=sec,
                snippet=snip,
            )
            for score, doc, sec, snip in top_matches
        ]

        return SearchDocsResult(
            query=query,
            total_matches=len(results),
            results=results,
        )

    def search(
        self,
        query: str,
        sources: Optional[List[str]] = None,
        max_results: int = 5,
        min_score: float = 0.20,
        include_snippets: bool = True,
    ) -> List[SearchResultItem]:
        """Convenience search alias returning list of SearchResultItem."""
        res = self.search_docs(
            query=query,
            sources=sources,
            limit=max_results,
            min_score=min_score,
            include_snippets=include_snippets,
        )
        return res.results


# Export alias
WorkspaceRetriever = DocRetriever

__all__ = [
    "DocRetriever",
    "WorkspaceRetriever",
    "IndexedDocument",
    "normalize_search_text",
    "extract_search_snippet",
]
