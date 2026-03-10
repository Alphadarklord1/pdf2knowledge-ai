from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
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
    ocr_used: bool = False
    ocr_available: bool = False
    extraction_quality: str = "native"
    ocr_warning: str = ""


@dataclass
class TableCandidate:
    page_number: int
    heading: str
    rows: list[list[str]] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)


@dataclass
class VisualCandidate:
    page_number: int
    label: str
    caption: str


@dataclass
class DecomposedSection:
    heading: str
    body: str
    page_numbers: list[int] = field(default_factory=list)
    table_like_lines: list[str] = field(default_factory=list)
    visual_references: list[str] = field(default_factory=list)
    structured_tables: list[TableCandidate] = field(default_factory=list)
    visual_candidates: list[VisualCandidate] = field(default_factory=list)


@dataclass
class ParseResult:
    pages: list[PdfPage]
    sections: list[DecomposedSection]
    warnings: list[str]
    total_tables: int
    total_visual_references: int
    total_images: int = 0
    ocr_pages: int = 0
    ocr_available: bool = False
    table_candidates: list[TableCandidate] = field(default_factory=list)
    visual_candidates: list[VisualCandidate] = field(default_factory=list)


def get_ocr_tool_status() -> dict[str, str | bool]:
    tesseract_path = shutil.which("tesseract")
    pdftoppm_path = shutil.which("pdftoppm")
    available = bool(tesseract_path and pdftoppm_path)
    return {
        "available": available,
        "tesseract_path": tesseract_path or "",
        "pdftoppm_path": pdftoppm_path or "",
        "message": "OCR ready" if available else "OCR tools missing (requires tesseract + pdftoppm).",
    }


def _ocr_pdf_page(pdf_path: Path, page_number: int) -> tuple[str, str]:
    status = get_ocr_tool_status()
    if not bool(status["available"]):
        return "", str(status["message"])
    with tempfile.TemporaryDirectory(prefix="kb-ocr-") as temp_dir:
        prefix = Path(temp_dir) / f"page-{page_number}"
        render = subprocess.run(
            [
                str(status["pdftoppm_path"]),
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-png",
                str(pdf_path),
                str(prefix),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if render.returncode != 0:
            message = render.stderr.strip() or "Failed to rasterize PDF page for OCR."
            return "", message
        image_path = prefix.with_name(f"{prefix.name}-1.png")
        if not image_path.exists():
            return "", "OCR rasterized page image was not created."
        ocr = subprocess.run(
            [str(status["tesseract_path"]), str(image_path), "stdout", "--psm", "6"],
            capture_output=True,
            text=True,
            check=False,
        )
        if ocr.returncode != 0:
            message = ocr.stderr.strip() or "Tesseract OCR failed."
            return "", message
        return ocr.stdout.strip(), ""


def extract_pdf_pages(pdf_path: Path) -> list[PdfPage]:
    reader = PdfReader(str(pdf_path))
    pages: list[PdfPage] = []
    ocr_status = get_ocr_tool_status()
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        image_count = 0
        try:
            image_count = len(page.images)  # type: ignore[attr-defined]
        except Exception:
            image_count = 0
        native_text = text.strip()
        clean_text = native_text
        ocr_used = False
        ocr_warning = ""
        text_char_count = len(clean_text)
        likely_scanned = text_char_count < 80 and image_count > 0
        if text_char_count < 80 and bool(ocr_status["available"]):
            ocr_text, ocr_error = _ocr_pdf_page(pdf_path, index)
            if len(ocr_text.strip()) > max(text_char_count + 20, 120):
                clean_text = ocr_text.strip()
                ocr_used = True
                text_char_count = len(clean_text)
            elif ocr_error:
                ocr_warning = ocr_error
        elif text_char_count < 80 and not bool(ocr_status["available"]):
            ocr_warning = str(ocr_status["message"])
        extraction_quality = "strong" if text_char_count >= 500 else "usable" if text_char_count >= 180 else "weak"
        if ocr_used:
            extraction_quality = "ocr"
        recommended_mode = "original" if ocr_used or text_char_count >= 180 else ("ocr" if likely_scanned else "grayscale")
        pages.append(
            PdfPage(
                page_number=index,
                text=clean_text,
                image_count=image_count,
                text_char_count=text_char_count,
                likely_scanned=likely_scanned,
                recommended_mode=recommended_mode,
                ocr_used=ocr_used,
                ocr_available=bool(ocr_status["available"]),
                extraction_quality=extraction_quality,
                ocr_warning=ocr_warning,
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


def _structured_table_candidates(page_number: int, lines: list[str]) -> list[TableCandidate]:
    raw_lines = _table_like_lines(lines)
    if not raw_lines:
        return []
    rows = []
    for line in raw_lines:
        cells = [cell.strip(" |") for cell in TABLE_SPLIT_RE.split(line) if cell.strip(" |")]
        if len(cells) >= 3:
            rows.append(cells)
    if not rows:
        return []
    return [TableCandidate(page_number=page_number, heading=f"Page {page_number} Table Extract", rows=rows, raw_lines=raw_lines)]


def _visual_refs(lines: Iterable[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        if VISUAL_RE.search(line):
            found.append(line)
    return found


def _visual_candidates(page_number: int, lines: list[str]) -> list[VisualCandidate]:
    return [VisualCandidate(page_number=page_number, label=f"Figure-{page_number}-{idx}", caption=line) for idx, line in enumerate(_visual_refs(lines), start=1)]


def decompose_pages(pages: list[PdfPage]) -> ParseResult:
    warnings: list[str] = []
    sections: list[DecomposedSection] = []
    current: DecomposedSection | None = None
    total_tables = 0
    total_visuals = 0
    total_images = sum(page.image_count for page in pages)
    total_text_chars = sum(len(page.text) for page in pages)
    ocr_pages = sum(1 for page in pages if page.ocr_used)
    ocr_status = get_ocr_tool_status()
    table_candidates: list[TableCandidate] = []
    visual_candidates: list[VisualCandidate] = []

    if not pages:
        warnings.append("No pages were extracted from the PDF.")
    if pages and total_text_chars < 200:
        warnings.append("Very little text was extracted. The file may be scanned or image-only and may need OCR.")
    scanned_pages = [page.page_number for page in pages if page.likely_scanned]
    if scanned_pages:
        warnings.append(f"Pages {', '.join(map(str, scanned_pages[:8]))} look scan-heavy and may need OCR or higher contrast.")
    if scanned_pages and not bool(ocr_status["available"]):
        warnings.append("OCR tools are not installed locally. Install tesseract and pdftoppm for scanned PDF support.")

    for page in pages:
        lines = _normalize_lines(page.text)
        if not lines:
            warnings.append(f"Page {page.page_number} has no extractable text.")
            continue
        page_tables = _table_like_lines(lines)
        page_visuals = _visual_refs(lines)
        page_structured_tables = _structured_table_candidates(page.page_number, lines)
        page_visual_candidates = _visual_candidates(page.page_number, lines)
        total_tables += len(page_tables)
        total_visuals += len(page_visuals)
        table_candidates.extend(page_structured_tables)
        visual_candidates.extend(page_visual_candidates)

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
        if current is not None:
            for table in page_structured_tables:
                if table not in current.structured_tables:
                    current.structured_tables.append(table)
            for visual in page_visual_candidates:
                if visual not in current.visual_candidates:
                    current.visual_candidates.append(visual)

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
        total_images=total_images,
        ocr_pages=ocr_pages,
        ocr_available=bool(ocr_status["available"]),
        table_candidates=table_candidates,
        visual_candidates=visual_candidates,
    )


def parse_pdf(pdf_path: Path) -> ParseResult:
    return decompose_pages(extract_pdf_pages(pdf_path))
