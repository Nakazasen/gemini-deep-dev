"""Live documentation fetcher with multi-tier cache and bundled fallback."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html as html_lib
import os
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple
import urllib.parse

import httpx

from custom_harness.mcp_grounding.cache import DocCache
from custom_harness.mcp_grounding.hasher import compute_canonical_hash, normalize_canonical_text
from custom_harness.mcp_grounding.schemas import DocContentResult


# Canonical sitemap of official Antigravity documentation
SITEMAP_ROUTES: Dict[str, str] = {
    "index": "https://antigravity.google/docs",
    "skills": "https://antigravity.google/docs/skills",
    "rules-workflows": "https://antigravity.google/docs/rules-workflows",
    "rules": "https://antigravity.google/docs/rules-workflows",
    "hooks": "https://antigravity.google/docs/hooks",
    "plugins": "https://antigravity.google/docs/plugins",
    "sidecars": "https://antigravity.google/docs/sidecars",
    "mcp": "https://antigravity.google/docs/mcp",
    "mcp-servers": "https://antigravity.google/docs/mcp",
    "ide-browser": "https://antigravity.google/docs/ide/browser",
    "permissions": "https://antigravity.google/docs/permissions",
    "changelog": "https://antigravity.google/changelog",
    "support": "https://antigravity.google/support",
    "cli-features": "https://antigravity.google/docs/cli/features",
    "cli-best-practices": "https://antigravity.google/docs/cli/best-practices",
    "cli-reference": "https://antigravity.google/docs/cli/reference",
}

# Mapping topic keywords to local bundled fallback reference files
BUNDLED_TOPIC_MAP: Dict[str, List[str]] = {
    "mcp": ["mcp_servers.md", "sdk.md"],
    "mcp_servers": ["mcp_servers.md"],
    "hooks": ["hooks.md"],
    "plugins": ["plugins.md"],
    "rules": ["rules.md"],
    "rules-workflows": ["rules.md"],
    "skills": ["skills.md"],
    "json_configs": ["json_configs.md"],
    "cli": ["cli.md"],
    "ide": ["ide.md"],
    "sdk": ["sdk.md"],
    "app": ["app.md"],
}

# Default embedded fallback documentation if offline and local files missing
EMBEDDED_DOCS: Dict[str, str] = {
    "mcp": """# Antigravity Model Context Protocol (MCP) Configuration

The Model Context Protocol (MCP) connects Antigravity IDE and sub-agents to external tools and data sources.

## Configuration Schema
MCP servers are defined in `~/.gemini/antigravity/mcp_config.json` under `mcpServers`.

```json
{
  "mcpServers": {
    "deep_dev_harness": {
      "command": "python",
      "args": ["-m", "custom_harness.mcp_server"],
      "env": {
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

## Stdio Transport
Servers communicate via stdio newline-delimited JSON-RPC 2.0 frames. All logging MUST go to stderr.

## Core Tools
1. `fetch_doc`: Retrieve full documentation or specific markdown sections.
2. `search_docs`: Search across local workspace, cached web docs, and guides.
3. `execute_host_proposal`: Validate and apply a scope-ticketed host proposal through the canonical `deep_dev_harness` server.
3. `genai_query`: Grounded generation query with citation verification.
""",
    "rules": """# Antigravity Rules & Workflows

Rules define declarative guardrails and behavioral guidelines loaded into agent context.

## Core Principles
1. Truthfulness & Anti-Laziness: No facade or dummy implementations.
2. Fail-Closed: Out-of-bounds operations fail safely.
3. Provenance & Citations: Grounded responses must cite SHA-256 doc hashes.
""",
    "skills": """# Antigravity Skills

Skills extend agent capabilities with modular instructions, scripts, and examples.
Each skill contains a `SKILL.md` frontmatter definition and execution references.
""",
}


def html_to_markdown(html_text: str) -> str:
    """Convert HTML documentation into clean, structured Markdown."""
    if not ("<html" in html_text.lower() or "<!doctype html" in html_text.lower()):
        return html_text

    # Remove script, style, nav, footer elements
    clean = re.sub(r"<(script|style|nav|footer|svg)[\s\S]*?</\1>", "", html_text, flags=re.IGNORECASE)

    # Try to extract <main> or <article>
    main_match = re.search(r"<(main|article)[\s\S]*?</\1>", clean, flags=re.IGNORECASE)
    if main_match:
        clean = main_match.group(0)

    # Convert headings
    for level in range(6, 0, -1):
        clean = re.sub(
            rf"<h{level}[^>]*>([\s\S]*?)</h{level}>",
            lambda m: f"\n\n{'#' * level} {re.sub(r'<[^>]+>', '', m.group(1)).strip()}\n\n",
            clean,
            flags=re.IGNORECASE,
        )

    # Convert code blocks
    clean = re.sub(
        r"<pre[^>]*><code[^>]*>([\s\S]*?)</code></pre>",
        lambda m: f"\n```\n{html_lib.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()}\n```\n",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"<code[^>]*>([\s\S]*?)</code>",
        lambda m: f"`{html_lib.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()}`",
        clean,
        flags=re.IGNORECASE,
    )

    # Convert list items and paragraphs
    clean = re.sub(
        r"<li[^>]*>([\s\S]*?)</li>",
        lambda m: f"\n- {re.sub(r'<[^>]+>', '', m.group(1)).strip()}",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"<p[^>]*>([\s\S]*?)</p>",
        lambda m: f"\n\n{re.sub(r'<[^>]+>', '', m.group(1)).strip()}\n\n",
        clean,
        flags=re.IGNORECASE,
    )

    # Strip remaining HTML tags
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = html_lib.unescape(clean)

    # Clean redundant whitespace
    lines = [line.strip() for line in clean.splitlines()]
    clean_lines: List[str] = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                clean_lines.append("")
                prev_blank = True
        else:
            clean_lines.append(line)
            prev_blank = False

    return "\n".join(clean_lines).strip()


def extract_markdown_title(content: str, default: str = "Antigravity Documentation") -> str:
    """Extract document title from YAML frontmatter or first # Heading."""
    if not content:
        return default

    # Frontmatter check
    if content.startswith("---"):
        fm_match = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            title_match = re.search(r"^title:\s*[\"']?(.*?)[\"']?\s*$", fm_text, re.MULTILINE | re.IGNORECASE)
            if title_match:
                return title_match.group(1).strip()

    # First heading check
    heading_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if heading_match:
        return heading_match.group(1).strip()

    return default


def extract_markdown_sections(content: str) -> List[str]:
    """List all markdown heading titles in the document."""
    headings: List[str] = []
    for line in content.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if match:
            headings.append(match.group(2).strip())
    return headings


def extract_section_content(content: str, section_query: str) -> Tuple[str, bool]:
    """Extract sub-section content under a matching heading title or anchor.

    Returns:
        (extracted_content, section_found_boolean)
    """
    clean_query = section_query.strip().lstrip("#").lower().replace("-", " ")
    lines = content.splitlines()

    start_idx = -1
    target_level = 0

    for i, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if match:
            level = len(match.group(1))
            heading_title = match.group(2).strip().lower().replace("-", " ")
            if clean_query in heading_title or heading_title in clean_query:
                start_idx = i
                target_level = level
                break

    if start_idx == -1:
        return content, False

    # Collect lines until next heading of equal or higher level (fewer or equal #)
    section_lines: List[str] = [lines[start_idx]]
    for i in range(start_idx + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+(.+)$", lines[i].strip())
        if match:
            level = len(match.group(1))
            if level <= target_level:
                break
        section_lines.append(lines[i])

    return "\n".join(section_lines), True


def truncate_content(text: str, max_length: int) -> Tuple[str, bool, int]:
    """Truncate content to max_length characters if exceeded."""
    char_count = len(text)
    if char_count <= max_length:
        return text, False, char_count

    truncated_text = text[:max_length] + "\n\n... [TRUNCATED DUE TO MAX_LENGTH LIMIT] ..."
    return truncated_text, True, len(truncated_text)


class LiveDocFetcher:
    """Async & sync fetcher for live Antigravity docs with 3-tier fallback."""

    def __init__(
        self,
        cache: Optional[DocCache] = None,
        timeout: float = 8.0,
        builtin_dirs: Optional[List[Path]] = None,
    ):
        self.cache = cache or DocCache()
        self.timeout = timeout

        # Search paths for bundled builtin docs
        user_home = Path.home()
        self.builtin_dirs = builtin_dirs or [
            user_home / ".gemini" / "antigravity" / "builtin" / "skills" / "antigravity_guide" / "references",
            user_home / ".gemini" / "antigravity" / "builtin" / "skills" / "agy-customizations" / "docs",
            user_home / ".gemini" / "antigravity" / "builtin" / "skills",
        ]

    def _resolve_url(self, target: str) -> str:
        """Resolve shorthand doc_id or URL path to fully qualified HTTP URL."""
        target_clean = target.strip()

        if target_clean.startswith("doc:builtin:") or target_clean.startswith("doc:fallback:"):
            slug = target_clean.split(":")[-1]
            return f"embedded://builtin/docs/{slug}.md"

        # Handle doc_id prefixes like 'doc:live:mcp' or 'doc:live:skills'
        if target_clean.startswith("doc:live:"):
            slug = target_clean[len("doc:live:") :].strip().lower()
            if slug in SITEMAP_ROUTES:
                return SITEMAP_ROUTES[slug]
            return f"https://antigravity.google/docs/{slug}"

        if target_clean.startswith("http://") or target_clean.startswith("https://"):
            return target_clean

        # Check slug in sitemap
        slug = target_clean.lower().strip("/")
        if slug in SITEMAP_ROUTES:
            return SITEMAP_ROUTES[slug]

        if slug.startswith("docs/"):
            return f"https://antigravity.google/{slug}"

        return f"https://antigravity.google/docs/{slug}"

    def _get_bundled_fallback(self, target: str) -> Optional[Tuple[str, str, str]]:
        """Look up bundled reference markdown files or embedded fallback text.

        Returns (title, content, uri_or_path) or None.
        """
        target_clean = target.strip().replace("doc:builtin:", "").replace("doc:fallback:", "")
        target_lower = target_clean.lower()
        topic_slug = ""
        for key in SITEMAP_ROUTES:
            if key in target_lower:
                topic_slug = key
                break
        if not topic_slug:
            for key in BUNDLED_TOPIC_MAP:
                if key in target_lower:
                    topic_slug = key
                    break

        # Search builtin directories on disk
        if topic_slug and topic_slug in BUNDLED_TOPIC_MAP:
            candidate_filenames = BUNDLED_TOPIC_MAP[topic_slug]
            for b_dir in self.builtin_dirs:
                if not b_dir.is_dir():
                    continue
                for fname in candidate_filenames:
                    file_path = b_dir / fname
                    if file_path.is_file():
                        try:
                            content = file_path.read_text(encoding="utf-8")
                            title = extract_markdown_title(content, default=f"Antigravity {topic_slug.title()} Guide")
                            return title, content, str(file_path)
                        except Exception:
                            continue

        # General file search in builtin_dirs
        for b_dir in self.builtin_dirs:
            if not b_dir.is_dir():
                continue
            for file_path in b_dir.glob("*.md"):
                if file_path.stem.lower() in target_lower or target_lower in file_path.stem.lower():
                    try:
                        content = file_path.read_text(encoding="utf-8")
                        title = extract_markdown_title(content, default=file_path.stem)
                        return title, content, str(file_path)
                    except Exception:
                        continue

        # Check embedded fallback dictionary
        for key, text in EMBEDDED_DOCS.items():
            if key in target_lower or topic_slug == key:
                title = extract_markdown_title(text, default=f"Antigravity {key.title()} Reference")
                return title, text, f"embedded://builtin/docs/{key}.md"

        return None

    async def fetch_doc(
        self,
        target: str,
        section: Optional[str] = None,
        max_length: int = 50000,
        refresh_cache: bool = False,
    ) -> DocContentResult:
        """Fetch documentation with multi-tier caching, section parsing, and fallback."""
        target_clean = target.strip()

        # Direct handling of builtin / fallback targets
        if target_clean.startswith("doc:builtin:") or target_clean.startswith("doc:fallback:"):
            fallback = self._get_bundled_fallback(target_clean)
            if fallback:
                title, content_raw, uri_path = fallback
                sections = extract_markdown_sections(content_raw)
                content_out = content_raw
                section_found = None
                if section:
                    content_out, section_found = extract_section_content(content_out, section)
                content_out, is_trunc, char_len = truncate_content(content_out, max_length)
                final_sha256 = compute_canonical_hash(content_out)

                slug_name = target_clean.split(":")[-1]
                return DocContentResult(
                    doc_id=f"doc:fallback:{slug_name}",
                    title=title,
                    content=content_out,
                    sha256=final_sha256,
                    source_type="bundle_fallback",
                    uri=uri_path,
                    available_sections=sections,
                    last_modified=datetime.now(timezone.utc).isoformat(),
                    char_count=char_len,
                    truncated=is_trunc,
                    section_found=section_found,
                )

        resolved_url = self._resolve_url(target)
        cache_key = f"live:{resolved_url}"

        # Tier 1 & 2: Check cache if not forcing refresh
        if not refresh_cache:
            cached_result = self.cache.get(cache_key)
            if cached_result:
                content = cached_result.content
                section_found = None
                if section:
                    content, section_found = extract_section_content(content, section)

                content, is_trunc, char_len = truncate_content(content, max_length)
                sha256 = compute_canonical_hash(content)

                return DocContentResult(
                    doc_id=cached_result.doc_id,
                    title=cached_result.title,
                    content=content,
                    sha256=sha256,
                    source_type="live_cached",
                    uri=resolved_url,
                    available_sections=cached_result.available_sections,
                    last_modified=cached_result.last_modified,
                    char_count=char_len,
                    truncated=is_trunc,
                    section_found=section_found,
                )

        # Attempt network fetch with conditional headers & retries
        headers = {"User-Agent": "Antigravity-MCP-Grounding/1.0 (Python/HTTPX)"}
        meta = self.cache.get_meta(cache_key)
        if meta and not refresh_cache:
            if meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]

        fetched_content: Optional[str] = None
        fetched_etag: Optional[str] = None
        fetched_last_modified: Optional[str] = None
        source_type = "live_fresh"

        # HTTP fetch attempt with exponential backoff
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                    resp = await client.get(resolved_url, headers=headers)

                    if resp.status_code == 304 and meta:
                        # 304 Not Modified: use disk cache
                        cached_result = self.cache.disk.get(cache_key, allow_stale=True)
                        if cached_result:
                            fetched_content = cached_result.content
                            source_type = "live_cached"
                            break

                    if resp.status_code == 200:
                        raw_text = resp.text
                        fetched_content = html_to_markdown(raw_text)
                        fetched_etag = resp.headers.get("ETag")
                        fetched_last_modified = resp.headers.get("Last-Modified")
                        source_type = "live_fresh"
                        break

            except (httpx.RequestError, httpx.TimeoutException, OSError):
                if attempt < 2:
                    await asyncio.sleep(0.3 * (2**attempt))
                continue

        # Network succeeded
        if fetched_content:
            raw_title = extract_markdown_title(fetched_content)
            sections = extract_markdown_sections(fetched_content)
            full_sha256 = compute_canonical_hash(fetched_content)

            full_result = DocContentResult(
                doc_id=f"doc:live:{resolved_url.split('/')[-1] or 'index'}",
                title=raw_title,
                content=fetched_content,
                sha256=full_sha256,
                source_type="live_fresh",
                uri=resolved_url,
                available_sections=sections,
                last_modified=fetched_last_modified,
                char_count=len(fetched_content),
                truncated=False,
            )

            # Store in multi-tier cache
            self.cache.set(
                cache_key,
                full_result,
                etag=fetched_etag,
                last_modified=fetched_last_modified,
            )

            # Apply section extraction & truncation
            content_out = fetched_content
            section_found = None
            if section:
                content_out, section_found = extract_section_content(content_out, section)

            content_out, is_trunc, char_len = truncate_content(content_out, max_length)
            final_sha256 = compute_canonical_hash(content_out)

            return DocContentResult(
                doc_id=full_result.doc_id,
                title=raw_title,
                content=content_out,
                sha256=final_sha256,
                source_type=source_type,
                uri=resolved_url,
                available_sections=sections,
                last_modified=fetched_last_modified,
                char_count=char_len,
                truncated=is_trunc,
                section_found=section_found,
            )

        # Network failed: Check stale disk cache
        stale_disk = self.cache.disk.get(cache_key, allow_stale=True)
        if stale_disk:
            content_out = stale_disk.content
            section_found = None
            if section:
                content_out, section_found = extract_section_content(content_out, section)
            content_out, is_trunc, char_len = truncate_content(content_out, max_length)
            final_sha256 = compute_canonical_hash(content_out)

            return DocContentResult(
                doc_id=stale_disk.doc_id,
                title=stale_disk.title,
                content=content_out,
                sha256=final_sha256,
                source_type="live_cached",
                uri=resolved_url,
                available_sections=stale_disk.available_sections,
                last_modified=stale_disk.last_modified,
                char_count=char_len,
                truncated=is_trunc,
                section_found=section_found,
            )

        # Check Tier 3: Bundled fallback
        fallback = self._get_bundled_fallback(target)
        if fallback:
            title, content_raw, uri_path = fallback
            sections = extract_markdown_sections(content_raw)
            content_out = content_raw
            section_found = None
            if section:
                content_out, section_found = extract_section_content(content_out, section)
            content_out, is_trunc, char_len = truncate_content(content_out, max_length)
            final_sha256 = compute_canonical_hash(content_out)

            slug_name = target.split("/")[-1].replace("doc:live:", "")
            return DocContentResult(
                doc_id=f"doc:fallback:{slug_name}",
                title=title,
                content=content_out,
                sha256=final_sha256,
                source_type="bundle_fallback",
                uri=uri_path,
                available_sections=sections,
                last_modified=datetime.now(timezone.utc).isoformat(),
                char_count=char_len,
                truncated=is_trunc,
                section_found=section_found,
            )

        # Nothing available
        raise FileNotFoundError(
            f"Documentation target '{target}' could not be fetched online and no local cached or bundled fallback exists."
        )

    def fetch_doc_sync(
        self,
        target: str,
        section: Optional[str] = None,
        max_length: int = 50000,
        refresh_cache: bool = False,
    ) -> DocContentResult:
        """Synchronous wrapper for fetch_doc."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # In active loop, run in executor
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(
                        asyncio.run,
                        self.fetch_doc(
                            target,
                            section=section,
                            max_length=max_length,
                            refresh_cache=refresh_cache,
                        ),
                    ).result()
            else:
                return loop.run_until_complete(
                    self.fetch_doc(
                        target,
                        section=section,
                        max_length=max_length,
                        refresh_cache=refresh_cache,
                    )
                )
        except RuntimeError:
            return asyncio.run(
                self.fetch_doc(
                    target,
                    section=section,
                    max_length=max_length,
                    refresh_cache=refresh_cache,
                )
            )
