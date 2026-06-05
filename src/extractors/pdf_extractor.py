from __future__ import annotations

import re
from typing import Dict, List

import fitz


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_by_dict(page: fitz.Page) -> List[str]:
    lines: List[str] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            span_texts = [str(span.get("text", "")) for span in line.get("spans", [])]
            joined = _normalize(" ".join(span_texts))
            if joined:
                lines.append(joined)
    return lines


def _extract_by_words(page: fitz.Page) -> List[str]:
    words = page.get_text("words")
    # words: (x0, y0, x1, y1, word, block_no, line_no, word_no)
    words = sorted(words, key=lambda w: (round(float(w[1]), 1), float(w[0])))
    line_map: Dict[tuple, List[str]] = {}
    for w in words:
        key = (int(w[5]), int(w[6]))
        line_map.setdefault(key, []).append(str(w[4]))
    return [_normalize(" ".join(parts)) for parts in line_map.values() if _normalize(" ".join(parts))]


def extract_pdf_pages(file_path: str) -> List[Dict[str, object]]:
    pages: List[Dict[str, object]] = []
    with fitz.open(file_path) as doc:
        for i, page in enumerate(doc, start=1):
            text_lines: List[str] = []
            seen = set()

            for line in _extract_by_dict(page):
                if line not in seen:
                    seen.add(line)
                    text_lines.append(line)

            for line in _extract_by_words(page):
                if line not in seen:
                    seen.add(line)
                    text_lines.append(line)

            # fallback: 기존 전체 문자열 추출
            if not text_lines:
                fallback = _normalize(page.get_text("text"))
                if fallback:
                    text_lines.append(fallback)

            text = "\n".join(text_lines).strip()
            pages.append({"page": i, "text": text})
    return pages
