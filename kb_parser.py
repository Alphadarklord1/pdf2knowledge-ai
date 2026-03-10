from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable

from pypdf import PdfReader

HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+.+")
VISUAL_RE = re.compile(r"\b(figure|fig\.|chart|graph|diagram|image|table)\b", re.IGNORECASE)
TABLE_SPLIT_RE = re.compile(r"\s{2,}|\t+")


@dataclass
class PdfPage:
    page_number: int
    text: str
    image_count: int = 0
    text_char_count: int = 0
    likely_scanned: bool = False
    recommended_mode: str = "original"


@dataclass
class DecomposedSection:
    heading: str
    body: str
    page_numbers: list[int] = field(default_factory=list)
    table_like_lines: list[str] = field(default_factory=list)
    visual_references: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    pages: list[PdfPage]
    sections: list[DecomposedSection]
    warnings: list[str]
    total_tables: int
    total_visual_references: int


def extract_pdf_pages(pdf_path: Path) -> list[PdfPage]:
    reader = PdfReader(str(pdf_path))
    pages: list[PdfPage] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        image_count = 0
        try:
            image_count = len(page.images)  # type: ignore[attr-defined]
        except Exception:
            image_count = 0
        clean_text = text.strip()
        text_char_count = len(clean_text)
        likely_scanned = text_char_count < 80 and image_count > 0
        recommended_mode = "ocr" if likely_scanned else ("grayscale" if text_char_count < 180 else "original")
        pages.append(
            PdfPage(
                page_number=index,
                text=clean_text,
                image_count=image_count,
                text_char_count=text_char_count,
                likely_scanned=likely_scanned,
                recommended_mode=recommended_mode,
            )
        )
    return pages


def _normalize_lines(text: str) -> list[str]:
    lines = []
    for raw in (text or "").splitlines():
        line = " ".join(raw.split())
        if line:
            lines.append(line)
    return lines


def _is_heading(line: str) -> bool:
    if not line or len(line) > 90:
        return False
    if HEADING_RE.match(line):
        return True
    if line.endswith(":") and len(line.split()) <= 12:
        return True
    if line.isupper() and len(line.split()) <= 12:
        return True
    words = line.split()
    if 1 < len(words) <= 10 and all(word[:1].isupper() or word.isupper() for word in words if word.isalpha()):
        return True
    return False


def _table_like_lines(lines: Iterable[str]) -> list[str]:
    results: list[str] = []
    for line in lines:
        if len(TABLE_SPLIT_RE.split(line)) >= 3 or line.lower().startswith("table "):
            results.append(line)
    return results


def _visual_refs(lines: Iterable[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        if VISUAL_RE.search(line):
            found.append(line)
    return found


def decompose_pages(pages: list[PdfPage]) -> ParseResult:
    warnings: list[str] = []
    sections: list[DecomposedSection] = []
    current: DecomposedSection | None = None
    total_tables = 0
    total_visuals = 0
    total_text_chars = sum(len(page.text) for page in pages)

    if not pages:
        warnings.append("No pages were extracted from the PDF.")
    if pages and total_text_chars < 200:
        warnings.append("Very little text was extracted. The file may be scanned or image-only and may need OCR.")
    scanned_pages = [page.page_number for page in pages if page.likely_scanned]
    if scanned_pages:
        warnings.append(f"Pages {', '.join(map(str, scanned_pages[:8]))} look scan-heavy and may need OCR or higher contrast.")

    for page in pages:
        lines = _normalize_lines(page.text)
        if not lines:
            warnings.append(f"Page {page.page_number} has no extractable text.")
            continue
        page_tables = _table_like_lines(lines)
        page_visuals = _visual_refs(lines)
        total_tables += len(page_tables)
        total_visuals += len(page_visuals)

        for line in lines:
            if _is_heading(line):
                if current and current.body.strip():
                    sections.append(current)
                current = DecomposedSection(heading=line.rstrip(":"), body="", page_numbers=[page.page_number])
                continue
            if current is None:
                current = DecomposedSection(heading=f"Page {page.page_number} Overview", body="", page_numbers=[page.page_number])
            if page.page_number not in current.page_numbers:
                current.page_numbers.append(page.page_number)
            current.body = f"{current.body}\n{line}".strip()
            if line in page_tables and line not in current.table_like_lines:
                current.table_like_lines.append(line)
            if line in page_visuals and line not in current.visual_references:
                current.visual_references.append(line)

    if current and current.body.strip():
        sections.append(current)

    if not sections and pages:
        warnings.append("No sections were detected. Fallback page-based grouping was used.")

    return ParseResult(
        pages=pages,
        sections=sections,
        warnings=warnings,
        total_tables=total_tables,
        total_visual_references=total_visuals,
    )


def parse_pdf(pdf_path: Path) -> ParseResult:
    return decompose_pages(extract_pdf_pages(pdf_path))
