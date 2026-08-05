"""Hierarchy-aware document chunking for retrieval.

The database retrieves child chunks, while ``parent_path`` retains the source
section hierarchy for evidence display and neighbouring-chunk expansion.
"""

from __future__ import annotations

import re

from langchain_text_splitters import RecursiveCharacterTextSplitter


CHILD_CHUNK_SIZE = 400
CHILD_CHUNK_OVERLAP = 60

_HEADING_PATTERNS = (
    re.compile(r"^(#{1,6})\s+(.+?)\s*$"),
    re.compile(r"^(第[一二三四五六七八九十百零〇0-9]+[章节篇部分])\s*(.*)$"),
    re.compile(r"^([一二三四五六七八九十]+)[、.．]\s*(.+)$"),
    re.compile(r"^(\d+(?:\.\d+){0,4})[、.．]?\s+(.+)$"),
)


def build_hierarchical_chunks(pages: list[tuple[int, str]]) -> list[dict]:
    """Convert page text into retrievable child chunks with a heading path."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHILD_CHUNK_SIZE,
        chunk_overlap=CHILD_CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "，", " "],
    )
    heading_stack: list[str] = []
    chunks: list[dict] = []

    for page, page_text in pages:
        section_lines: list[str] = []
        section_path = " > ".join(heading_stack) or "正文"

        def flush_section() -> None:
            body = "\n".join(section_lines).strip()
            if not body:
                return
            for child_text in splitter.split_text(body):
                chunks.append(
                    {
                        "text": f"【章节】{section_path}\n{child_text}",
                        "page": page,
                        "parent_path": section_path,
                    }
                )

        for raw_line in page_text.splitlines():
            line = raw_line.strip()
            if not line:
                section_lines.append(raw_line)
                continue
            heading = parse_heading(line)
            if heading:
                flush_section()
                section_lines = []
                level, title = heading
                if level <= len(heading_stack):
                    heading_stack = heading_stack[: level - 1]
                while len(heading_stack) < level - 1:
                    heading_stack.append("未命名层级")
                heading_stack.append(title)
                section_path = " > ".join(heading_stack)
            else:
                section_lines.append(raw_line)
        flush_section()

    return chunks


def parse_heading(line: str) -> tuple[int, str] | None:
    """Extract a ``(level, title)`` tuple from common report heading styles."""
    markdown = _HEADING_PATTERNS[0].match(line)
    if markdown:
        return len(markdown.group(1)), markdown.group(2).strip()

    chapter = _HEADING_PATTERNS[1].match(line)
    if chapter:
        return 1, " ".join(part for part in chapter.groups() if part).strip()

    chinese = _HEADING_PATTERNS[2].match(line)
    if chinese:
        return 2, " ".join(chinese.groups()).strip()

    numeric = _HEADING_PATTERNS[3].match(line)
    if numeric:
        number, title = numeric.groups()
        return number.count(".") + 1, f"{number} {title}".strip()
    return None
