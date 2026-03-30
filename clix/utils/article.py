"""Convert between Twitter Article Draft.js content and Markdown."""

from __future__ import annotations

import random
import re
import string
from typing import Any

_IMAGE_URL_PATTERN = re.compile(
    r"https?://[^\s]+\.(?:jpg|jpeg|png|gif|webp)"
    r"|https?://pbs\.twimg\.com/[^\s]+"
)


def _normalize_entity_map(entity_map: dict | list) -> dict[str, dict]:
    """Normalize entityMap from list or dict format to a uniform dict."""
    if isinstance(entity_map, list):
        return {str(item["key"]): item["value"] for item in entity_map if "key" in item}
    return {str(k): v for k, v in entity_map.items()}


def _find_image_url(data: dict[str, Any]) -> str:
    """Recursively search for an image URL in entity data."""
    for key in ("original_img_url", "mediaUrlHttps", "url", "src"):
        val = data.get(key)
        if isinstance(val, str) and _IMAGE_URL_PATTERN.search(val):
            return val
    for val in data.values():
        if isinstance(val, dict):
            found = _find_image_url(val)
            if found:
                return found
    return ""


def _find_caption(data: dict[str, Any]) -> str:
    """Extract alt text or caption from entity data."""
    for key in ("caption", "alt", "altText", "title"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _render_atomic_block(
    block: dict[str, Any],
    entity_map: dict[str, dict],
    media_url_map: dict[str, str],
) -> str:
    """Render an atomic block as markdown (image or embedded markdown)."""
    for entity_range in block.get("entityRanges", []):
        entity_key = str(entity_range.get("key", ""))
        entity = entity_map.get(entity_key, {})
        entity_type = entity.get("type", "")
        entity_data = entity.get("data", {})

        if entity_type == "MARKDOWN":
            return entity_data.get("markdown", entity_data.get("text", ""))

        if entity_type in ("IMAGE", "PHOTO", "MEDIA"):
            url = _find_image_url(entity_data)
            if not url:
                # Try media ID lookup — check mediaItems array (MEDIA type)
                media_id = entity_data.get("mediaId", entity_data.get("media_id", ""))
                if not media_id:
                    items = entity_data.get("mediaItems", [])
                    if items:
                        media_id = items[0].get("mediaId", items[0].get("media_id", ""))
                url = media_url_map.get(str(media_id), "")
            if url:
                caption = _find_caption(entity_data)
                return f"![{caption}]({url})"
    return ""


def _build_media_url_map(article_data: dict[str, Any]) -> dict[str, str]:
    """Build a media_id → URL lookup from article media entities."""
    result = article_data.get("result", article_data)
    url_map: dict[str, str] = {}

    # From cover_media
    cover_media = result.get("cover_media", {})
    cover_info = cover_media.get("media_info", {})
    cover_id = cover_media.get("media_id", cover_info.get("media_id", ""))
    cover_url = cover_info.get("original_img_url", "")
    if cover_id and cover_url:
        url_map[str(cover_id)] = cover_url

    # From media_entities
    for entity in result.get("media_entities", []):
        mid = entity.get("media_id", "")
        info = entity.get("media_info", {})
        murl = (
            info.get("original_img_url", "")
            or entity.get("original_img_url", "")
            or entity.get("mediaUrlHttps", "")
        )
        if mid and murl:
            url_map[str(mid)] = murl

    return url_map


def article_to_markdown(article_data: dict[str, Any]) -> str:
    """Convert a Twitter Article's Draft.js content_state to Markdown.

    Handles block types: headers, blockquote, lists, code blocks, atomic
    (images/embedded markdown), and unstyled.
    """
    result = article_data.get("result", article_data)
    content_state = (
        result.get("content", {}).get("content_state", {})
        if "content" in result
        else result.get("content_state", {})
    )
    blocks = content_state.get("blocks", [])
    entity_map = _normalize_entity_map(content_state.get("entityMap", {}))
    media_url_map = _build_media_url_map(article_data)

    if not blocks:
        return ""

    lines: list[str] = []
    ordered_counter = 0

    for block in blocks:
        block_type = block.get("type", "unstyled")
        text = block.get("text", "")
        text = _apply_inline_styles(text, block.get("inlineStyleRanges", []))

        if block_type == "header-one":
            lines.append(f"# {text}")
            ordered_counter = 0
        elif block_type == "header-two":
            lines.append(f"## {text}")
            ordered_counter = 0
        elif block_type == "header-three":
            lines.append(f"### {text}")
            ordered_counter = 0
        elif block_type == "blockquote":
            lines.append(f"> {text}")
            ordered_counter = 0
        elif block_type == "unordered-list-item":
            lines.append(f"- {text}")
            ordered_counter = 0
        elif block_type == "ordered-list-item":
            ordered_counter += 1
            lines.append(f"{ordered_counter}. {text}")
        elif block_type == "code-block":
            lines.append(f"```\n{text}\n```")
            ordered_counter = 0
        elif block_type == "atomic":
            rendered = _render_atomic_block(block, entity_map, media_url_map)
            if rendered:
                lines.append(rendered)
            ordered_counter = 0
        else:
            # unstyled or unknown — plain paragraph
            lines.append(text)
            ordered_counter = 0

    return "\n\n".join(lines)


def _apply_inline_styles(text: str, style_ranges: list[dict[str, Any]]) -> str:
    """Apply bold/italic inline styles to text.

    Processes ranges from right to left to preserve offsets.
    """
    if not style_ranges or not text:
        return text

    # Sort by offset descending so insertions don't shift earlier offsets
    sorted_ranges = sorted(style_ranges, key=lambda r: r.get("offset", 0), reverse=True)

    for style_range in sorted_ranges:
        offset = style_range.get("offset", 0)
        length = style_range.get("length", 0)
        style = style_range.get("style", "")

        if offset + length > len(text):
            continue

        segment = text[offset : offset + length]
        if style == "BOLD":
            segment = f"**{segment}**"
        elif style == "ITALIC":
            segment = f"*{segment}*"
        elif style == "CODE":
            segment = f"`{segment}`"

        text = text[:offset] + segment + text[offset + length :]

    return text


def extract_article_metadata(article_data: dict[str, Any]) -> dict[str, Any]:
    """Extract title, author, and other metadata from article data.

    Returns a dict with title, cover_image_url, and lifecycle_state.
    """
    result = article_data.get("result", article_data)
    title = result.get("title", "")
    cover_image = result.get("cover_media", {}).get("media_info", {}).get("original_img_url", "")
    lifecycle_state = result.get("lifecycle_state", "")

    return {
        "title": title,
        "cover_image_url": cover_image,
        "lifecycle_state": lifecycle_state,
    }


# =============================================================================
# Markdown → Draft.js content_state (reverse of article_to_markdown)
# =============================================================================


def _generate_block_key() -> str:
    """Generate a random 5-char alphanumeric key like Draft.js."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=5))


class _InlineResult:
    """Result of extracting inline styles and link entities from markdown text."""

    def __init__(self) -> None:
        self.text: str = ""
        self.styles: list[dict[str, Any]] = []
        self.link_entities: list[tuple[int, int, str]] = []  # (offset, length, url)


def _extract_inline_formatting(text: str) -> _InlineResult:
    """Extract Markdown inline styles and links from text.

    Processes [text](url) links, **bold**, *italic*, and ~~strikethrough~~.
    Backtick code markers are stripped (X articles don't support Code inline style).
    Returns an _InlineResult with clean text, style ranges, and link entities.
    """
    result = _InlineResult()

    def _strip_markers(
        txt: str, pattern: re.Pattern[str], style: str
    ) -> tuple[str, list[dict[str, Any]]]:
        found: list[dict[str, Any]] = []
        result_parts: list[str] = []
        last_end = 0
        for match in pattern.finditer(txt):
            result_parts.append(txt[last_end : match.start()])
            content = match.group(1)
            offset = len("".join(result_parts))
            result_parts.append(content)
            last_end = match.end()
            found.append({"offset": offset, "length": len(content), "style": style})
        result_parts.append(txt[last_end:])
        return "".join(result_parts), found

    # Extract links [text](url) first
    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    links: list[tuple[int, int, str]] = []
    parts: list[str] = []
    last_end = 0
    for match in link_pattern.finditer(text):
        parts.append(text[last_end : match.start()])
        link_text = match.group(1)
        link_url = match.group(2)
        offset = len("".join(parts))
        parts.append(link_text)
        last_end = match.end()
        links.append((offset, len(link_text), link_url))
    parts.append(text[last_end:])
    text = "".join(parts)

    # Strip code backticks (X article API doesn't support Code inline style)
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Preserve inline LaTeX $...$ (don't strip as formatting markers)
    # LaTeX is rendered natively by X — pass through as-is

    # Bold (**...**) — before italic so ** is matched before *
    text, bold_styles = _strip_markers(text, re.compile(r"\*\*(.+?)\*\*"), "Bold")
    result.styles.extend(bold_styles)

    # Italic (*...*)
    text, italic_styles = _strip_markers(text, re.compile(r"\*(.+?)\*"), "Italic")
    result.styles.extend(italic_styles)

    # Strikethrough (~~...~~) — after bold/italic so offsets are correct
    text, strike_styles = _strip_markers(text, re.compile(r"~~(.+?)~~"), "Strikethrough")
    result.styles.extend(strike_styles)

    result.text = text
    result.link_entities = links
    return result


def _make_block(
    text: str,
    block_type: str,
    inline_styles: list[dict[str, Any]] | None = None,
    entity_ranges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a Draft.js block dict matching the X article API schema.

    X uses snake_case field names and requires data/entity_ranges/inline_style_ranges
    to always be present.
    """
    return {
        "data": {},
        "text": text,
        "key": _generate_block_key(),
        "type": block_type,
        "entity_ranges": entity_ranges or [],
        "inline_style_ranges": inline_styles or [],
    }


def markdown_to_content_state(markdown_text: str) -> tuple[dict[str, Any], str]:
    """Convert Markdown text to Draft.js content_state for X articles.

    Supported block types: header-one, header-two, blockquote,
    unordered-list-item, ordered-list-item, unstyled.
    Supported inline styles: Bold, Italic, Strikethrough.
    Code blocks use MARKDOWN entities, LaTeX uses LATEX entities (both atomic).
    Links are converted to LINK entities.

    Returns (content_state, title) where title is the first H1 heading found.
    """
    blocks: list[dict[str, Any]] = []
    entity_map: dict[str, dict[str, Any]] = {}
    entity_counter = 0
    title = ""

    def _add_entity(entity_type: str, mutability: str, data: dict[str, Any] | None = None) -> int:
        """Register an entity and return its key (int)."""
        nonlocal entity_counter
        entity_map[str(entity_counter)] = {
            "type": entity_type,
            "mutability": mutability,
            "data": data or {},
        }
        idx = entity_counter
        entity_counter += 1
        return idx

    def _add_code_block(lines: list[str], lang: str) -> None:
        """Add a code block as atomic MARKDOWN entity."""
        fence = f"```{lang}" if lang else "```"
        markdown = fence + "\n" + "\n".join(lines) + "\n```"
        ek = _add_entity("MARKDOWN", "Mutable", {"markdown": markdown})
        blocks.append(_make_block("", "unstyled"))
        blocks.append(
            _make_block(" ", "atomic", entity_ranges=[{"key": ek, "offset": 0, "length": 1}])
        )
        blocks.append(_make_block("", "unstyled"))

    def _add_latex_block(formula: str) -> None:
        """Add a LaTeX block as atomic LATEX entity."""
        ek = _add_entity("LATEX", "Immutable")
        blocks.append(_make_block("", "unstyled"))
        blocks.append(
            _make_block(
                formula,
                "atomic",
                entity_ranges=[{"key": ek, "offset": 0, "length": len(formula)}],
            )
        )
        blocks.append(_make_block("", "unstyled"))

    def _process_line(raw_text: str, block_type: str = "unstyled") -> None:
        """Parse inline formatting, links, and inline LaTeX from text."""
        nonlocal entity_counter

        # Single-line display LaTeX: $$formula$$
        display_match = re.match(r"^\$\$(.+)\$\$$", raw_text.strip())
        if display_match:
            _add_latex_block(display_match.group(1))
            return

        # Split line on inline LaTeX $...$, emitting text blocks and atomic LaTeX blocks
        latex_pattern = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")
        parts = latex_pattern.split(raw_text)
        # Even indices are text, odd indices are LaTeX formulas
        has_latex = len(parts) > 1
        for i, part in enumerate(parts):
            if i % 2 == 1:
                _add_latex_block(part)
            elif part.strip() or not has_latex:
                fmt = _extract_inline_formatting(part)
                ranges: list[dict[str, Any]] = []
                for offset, length, url in fmt.link_entities:
                    entity_map[str(entity_counter)] = {
                        "type": "LINK",
                        "mutability": "Mutable",
                        "data": {"url": url},
                    }
                    ranges.append({"key": entity_counter, "offset": offset, "length": length})
                    entity_counter += 1
                blocks.append(_make_block(fmt.text, block_type, fmt.styles, ranges))

    in_code_block = False
    code_lang = ""
    code_lines: list[str] = []
    for line in markdown_text.split("\n"):
        # Toggle code fences
        if line.strip().startswith("```"):
            if in_code_block:
                _add_code_block(code_lines, code_lang)
                code_lines = []
                code_lang = ""
            else:
                code_lang = line.strip()[3:].strip()
            in_code_block = not in_code_block
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        if not line.strip():
            continue

        trimmed = line.lstrip()

        # Determine block type and strip markdown prefix
        block_type = "unstyled"
        text = trimmed

        if trimmed.startswith("# ") and not trimmed.startswith("## "):
            block_type = "header-one"
            text = trimmed[2:]
            if not title:
                title = text.strip()
        elif trimmed.startswith("## ") and not trimmed.startswith("### "):
            block_type = "header-two"
            text = trimmed[3:]
        elif trimmed.startswith("### "):
            block_type = "header-two"
            text = trimmed[4:]
        elif trimmed.startswith("> "):
            block_type = "blockquote"
            text = trimmed[2:]
        elif re.match(r"^\d+\.\s", trimmed):
            block_type = "ordered-list-item"
            text = re.sub(r"^\d+\.\s+", "", trimmed)
        elif trimmed.startswith("- ") or trimmed.startswith("* "):
            block_type = "unordered-list-item"
            text = trimmed[2:]
        elif trimmed == "---" or trimmed == "***":
            blocks.append(_make_block("", "unstyled"))
            continue

        _process_line(text, block_type)

    # Handle unclosed code block
    if code_lines:
        _add_code_block(code_lines, code_lang)

    entity_map_list: list[dict[str, Any]] = [{"key": k, "value": v} for k, v in entity_map.items()]
    content_state: dict[str, Any] = {
        "blocks": blocks,
        "entity_map": entity_map_list,
    }
    return content_state, title
