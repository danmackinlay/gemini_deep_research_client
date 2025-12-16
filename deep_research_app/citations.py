"""Citation parsing, normalization, and URL resolution."""

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class SourceInfo:
    """Information about a single source."""

    title: str
    url: str
    final_url: Optional[str] = None  # Resolved from redirect


@dataclass
class ProcessedReport:
    """Result of processing a report's citations."""

    text: str
    sources: dict[str, SourceInfo]
    errors: list[str] = field(default_factory=list)


def parse_sources(report_text: str) -> dict[str, SourceInfo]:
    """
    Extract sources section into {number: SourceInfo} map.

    Handles formats like:
    - **Sources:**
    - ## Sources
    - Sources:

    Each entry like:
    1. [title](url)
    """
    sources: dict[str, SourceInfo] = {}

    # Find sources section - look for various headers
    sources_pattern = r"(?:^|\n)(?:\*\*Sources:\*\*|## Sources|Sources:)\s*\n([\s\S]*?)(?:\n##|\n\*\*[A-Z]|\Z)"
    match = re.search(sources_pattern, report_text, re.IGNORECASE)

    if not match:
        return sources

    sources_text = match.group(1)

    # Parse each numbered entry: N. [title](url)
    entry_pattern = r"(\d+)\.\s*\[([^\]]+)\]\(([^)]+)\)"
    for m in re.finditer(entry_pattern, sources_text):
        num, title, url = m.groups()
        sources[num] = SourceInfo(title=title, url=url)

    return sources


def normalize_inline_citations(text: str, sources: dict[str, SourceInfo]) -> str:
    """
    Convert [cite: N], [cite: N, M] to [N](url), [M](url).

    Also handles:
    - [N] (bare number, not already a link)
    """

    def replace_cite(match: re.Match) -> str:
        """Replace [cite: N, M, ...] with [N](url), [M](url), ..."""
        nums_str = match.group(1)
        nums = [n.strip() for n in nums_str.split(",")]
        parts = []
        for n in nums:
            if n in sources:
                parts.append(f"[{n}]({sources[n].final_url or sources[n].url})")
            else:
                parts.append(f"[{n}]")  # Keep as-is if source not found
        return ", ".join(parts)

    # Replace [cite: N] and [cite: N, M, ...]
    text = re.sub(r"\[cite:\s*([\d,\s]+)\]", replace_cite, text)

    # Replace bare [N] that aren't already links (not followed by `(`)
    def replace_bare(match: re.Match) -> str:
        n = match.group(1)
        if n in sources:
            return f"[{n}]({sources[n].final_url or sources[n].url})"
        return match.group(0)

    text = re.sub(r"\[(\d+)\](?!\()", replace_bare, text)

    # Remove duplicate inline URLs that follow [N](url) - model sometimes adds (url) after
    # Pattern: [N](url)(url) -> [N](url)
    text = re.sub(r"(\[\d+\]\([^)]+\))\(https?://[^)]+\)", r"\1", text)

    return text


def rebuild_sources_section(sources: dict[str, SourceInfo]) -> str:
    """Generate clean ## Sources markdown from source map."""
    if not sources:
        return ""

    lines = ["", "## Sources", ""]
    for num in sorted(sources.keys(), key=int):
        src = sources[num]
        url = src.final_url or src.url
        lines.append(f"{num}. [{src.title}]({url})")

    return "\n".join(lines)


def remove_sources_section(text: str) -> str:
    """Remove the existing sources section from text."""
    # Match various source section headers and everything after
    patterns = [
        r"\n\*\*Sources:\*\*\s*\n[\s\S]*$",
        r"\n## Sources\s*\n[\s\S]*$",
        r"\nSources:\s*\n[\s\S]*$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text.rstrip()


def validate_citations(text: str, sources: dict[str, SourceInfo]) -> list[str]:
    """Return list of validation errors."""
    errors = []

    # Find all citation references in text
    cite_refs = set()
    for m in re.finditer(r"\[(\d+)\]", text):
        cite_refs.add(m.group(1))

    # Check all referenced citations have sources
    for ref in cite_refs:
        if ref not in sources:
            errors.append(f"Citation [{ref}] references missing source")

    # Check all sources have URLs
    for num, src in sources.items():
        if not src.url:
            errors.append(f"Source {num} missing URL")

    # Warn about unused sources (not an error)
    source_nums = set(sources.keys())
    unused = source_nums - cite_refs
    if unused:
        errors.append(f"Unused sources: {sorted(unused, key=int)}")

    return errors


def resolve_redirect(url: str, timeout: float = 10.0) -> Optional[str]:
    """
    Follow grounding-api-redirect URLs to get final URL.

    Returns None on failure, original URL if not a redirect.
    """
    if "grounding-api-redirect" not in url:
        return url

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.head(url)
            return str(resp.url)
    except Exception:
        return None


def resolve_all_redirects(
    sources: dict[str, SourceInfo], timeout: float = 10.0
) -> None:
    """Resolve redirect URLs for all sources in-place."""
    for src in sources.values():
        if "grounding-api-redirect" in src.url:
            final = resolve_redirect(src.url, timeout)
            if final and final != src.url:
                src.final_url = final


def process_report(report_text: str, resolve_redirects: bool = True) -> ProcessedReport:
    """
    Full citation processing pipeline.

    1. Parse sources section
    2. Optionally resolve redirect URLs
    3. Normalize inline citations to [N](url) format
    4. Remove old sources section and append clean one
    5. Validate
    """
    # Parse sources
    sources = parse_sources(report_text)

    if not sources:
        return ProcessedReport(
            text=report_text,
            sources={},
            errors=["No sources section found"],
        )

    # Resolve redirects
    if resolve_redirects:
        resolve_all_redirects(sources)

    # Normalize inline citations
    text = normalize_inline_citations(report_text, sources)

    # Remove old sources section and add clean one
    text = remove_sources_section(text)
    text += rebuild_sources_section(sources)

    # Validate
    errors = validate_citations(text, sources)

    return ProcessedReport(text=text, sources=sources, errors=errors)
