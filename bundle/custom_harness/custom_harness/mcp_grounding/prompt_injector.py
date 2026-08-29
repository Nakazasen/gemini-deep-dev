"""Prompt grounding context injection and citation verification engine."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Dict, List, Optional, Set, Tuple

from custom_harness.mcp_grounding.schemas import (
    CitationItem,
    DocContentResult,
    GroundingMetadata,
)


DELIMITER_START = "--- [GROUNDED DOCUMENTATION CONTEXT START] ---"
DELIMITER_END = "--- [GROUNDED DOCUMENTATION CONTEXT END] ---"

DOC_HASH_REGEX = re.compile(r"\[Doc-Hash:\s*([a-fA-F0-9]{6,64})\]", re.IGNORECASE)
DOC_HASH_ALT_REGEX = re.compile(r"Doc-Hash:\s*([a-fA-F0-9]{6,64})", re.IGNORECASE)
SOURCE_TAG_REGEX = re.compile(r"\[Source:\s*([^\]]+)\]", re.IGNORECASE)


def format_grounded_prompt(
    user_prompt: str,
    docs: List[DocContentResult],
    max_context_chars: Optional[int] = None,
) -> Tuple[str, GroundingMetadata]:
    """Wrap retrieved authoritative documentation chunks into a standardized grounding block.

    Args:
        user_prompt: User instruction or prompt string.
        docs: List of DocContentResult instances.
        max_context_chars: Optional maximum total characters of grounded content.

    Returns:
        (grounded_full_prompt, grounding_metadata)
    """
    if not docs:
        meta = GroundingMetadata(
            injected_docs=[],
            doc_hashes={},
            delimiter_block_length=0,
            total_grounded_chars=0,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        return user_prompt, meta

    doc_hashes: Dict[str, str] = {}
    source_blocks: List[str] = []
    total_grounded_chars = 0

    # Handle max_context_chars truncation across docs
    remaining_budget = max_context_chars if max_context_chars is not None else float("inf")

    injected_doc_list: List[DocContentResult] = []

    for i, doc in enumerate(docs, start=1):
        doc_hashes[doc.doc_id] = doc.sha256
        short_hash = doc.sha256[:8]

        content_to_inject = doc.content.strip()
        if max_context_chars is not None:
            if remaining_budget <= 0:
                break
            if len(content_to_inject) > remaining_budget:
                content_to_inject = content_to_inject[:int(remaining_budget)]
            remaining_budget -= len(content_to_inject)

        total_grounded_chars += len(content_to_inject)
        injected_doc_list.append(doc)

        block = (
            f"### Source {i}: {doc.title}\n"
            f"- Doc-ID: {doc.doc_id}\n"
            f"- URI: {doc.uri}\n"
            f"- SHA-256: {doc.sha256}\n"
            f"- SHA-256 Prefix: {short_hash}\n\n"
            f"```markdown\n"
            f"{content_to_inject}\n"
            f"```"
        )
        source_blocks.append(block)

    grounding_header = (
        f"{DELIMITER_START}\n"
        f"The following authoritative documentation chunks have been verified with cryptographic SHA-256 hashes.\n"
        f"All code, signatures, and configuration you generate MUST conform strictly to these references.\n"
        f"You MUST include a citation tag `[Doc-Hash: <sha256_short>]` in your docstrings or implementation comments.\n\n"
        + "\n\n".join(source_blocks)
        + f"\n{DELIMITER_END}\n\n"
    )

    full_prompt = f"{grounding_header}User Request:\n{user_prompt}"

    meta = GroundingMetadata(
        injected_docs=injected_doc_list,
        doc_hashes=doc_hashes,
        delimiter_block_length=len(grounding_header),
        total_grounded_chars=total_grounded_chars,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    return full_prompt, meta


def validate_citations(
    response_text: str,
    injected_docs: List[DocContentResult],
    enforce_citations: bool = True,
) -> Tuple[bool, List[CitationItem], List[str]]:
    """Validate that the model response contains genuine citations matching injected document hashes.

    Returns:
        (passed: bool, citations: List[CitationItem], errors: List[str])
    """
    if not injected_docs:
        # No grounding was injected, citation check automatically passes
        return True, [], []

    # Map of short hash (8 chars) -> doc, and full hash -> doc
    hash_to_doc: Dict[str, DocContentResult] = {}
    title_to_doc: Dict[str, DocContentResult] = {}

    for doc in injected_docs:
        full_hash = doc.sha256.lower()
        short_hash = full_hash[:8]
        hash_to_doc[full_hash] = doc
        hash_to_doc[short_hash] = doc
        title_to_doc[doc.title.lower().strip()] = doc

    matched_citations: List[CitationItem] = []
    found_hashes: Set[str] = set()
    errors: List[str] = []

    # 1. Search for [Doc-Hash: <hash>]
    for match in DOC_HASH_REGEX.finditer(response_text):
        raw_hash = match.group(1).lower().strip()
        matched_doc: Optional[DocContentResult] = None

        # Check exact prefix or full match
        for k, doc in hash_to_doc.items():
            if raw_hash.startswith(k) or k.startswith(raw_hash):
                matched_doc = doc
                break

        if matched_doc:
            found_hashes.add(matched_doc.sha256[:8])
            # Extract surrounding sentence excerpt
            start_pos = max(0, match.start() - 60)
            end_pos = min(len(response_text), match.end() + 60)
            excerpt = response_text[start_pos:end_pos].replace("\n", " ").strip()

            matched_citations.append(
                CitationItem(
                    source_title=matched_doc.title,
                    uri=matched_doc.uri,
                    sha256_short=matched_doc.sha256[:8],
                    sha256_full=matched_doc.sha256,
                    excerpt=excerpt,
                    confidence=1.0,
                )
            )
        else:
            errors.append(f"Response cited unknown doc hash: '{raw_hash}'")

    # 2. Search for [Source: <title>] if no doc hashes found
    if not matched_citations:
        for match in SOURCE_TAG_REGEX.finditer(response_text):
            source_query = match.group(1).lower().strip()
            for title_key, doc in title_to_doc.items():
                if source_query in title_key or title_key in source_query:
                    found_hashes.add(doc.sha256[:8])
                    matched_citations.append(
                        CitationItem(
                            source_title=doc.title,
                            uri=doc.uri,
                            sha256_short=doc.sha256[:8],
                            sha256_full=doc.sha256,
                            excerpt=match.group(0),
                            confidence=0.85,
                        )
                    )

    # 3. Check citation requirement
    passed = True
    if enforce_citations and not matched_citations:
        passed = False
        errors.append(
            "Citation verification failed: Model response did not contain any valid [Doc-Hash: <sha256_prefix>] citation tags."
        )

    return passed, matched_citations, errors


def extract_citations(
    response_text: str,
    injected_docs: Optional[List[DocContentResult]] = None,
) -> List[CitationItem]:
    """Extract citation items matching prompt tags from response text."""
    if injected_docs is not None:
        _, citations, _ = validate_citations(response_text, injected_docs, enforce_citations=False)
        return citations

    citations: List[CitationItem] = []
    for match in DOC_HASH_REGEX.finditer(response_text):
        raw_hash = match.group(1).lower().strip()
        start_pos = max(0, match.start() - 60)
        end_pos = min(len(response_text), match.end() + 60)
        excerpt = response_text[start_pos:end_pos].replace("\n", " ").strip()
        citations.append(
            CitationItem(
                source_title=f"Doc {raw_hash[:8]}",
                uri="",
                sha256_short=raw_hash[:8],
                sha256_full=raw_hash if len(raw_hash) == 64 else ("0" * (64 - len(raw_hash)) + raw_hash),
                excerpt=excerpt,
                confidence=1.0,
            )
        )
    return citations


__all__ = [
    "DELIMITER_START",
    "DELIMITER_END",
    "format_grounded_prompt",
    "validate_citations",
    "extract_citations",
]
