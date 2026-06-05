from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def extract_hwpx_pages(file_path: str, chars_per_page: int = 2000) -> List[Dict[str, object]]:
    paragraphs: List[str] = []
    table_cells: List[str] = []
    with zipfile.ZipFile(file_path, "r") as zf:
        for member in sorted(zf.namelist()):
            if not member.lower().endswith(".xml"):
                continue
            if "section" not in member.lower() and "content" not in member.lower():
                continue

            try:
                raw = zf.read(member)
                root = ET.fromstring(raw)
            except Exception:
                continue

            for node in root.iter():
                tag = _strip_ns(node.tag).lower()
                if tag in {"p", "paragraph"}:
                    text = "".join(node.itertext())
                    text = re.sub(r"\s+", " ", text).strip()
                    if text:
                        paragraphs.append(text)
                elif tag in {"tc", "cell", "td"}:
                    text = "".join(node.itertext())
                    text = re.sub(r"\s+", " ", text).strip()
                    if text:
                        table_cells.append(text)

    merged_texts: List[str] = []
    seen = set()
    for item in paragraphs + table_cells:
        normalized = re.sub(r"\s+", " ", item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged_texts.append(normalized)

    pages: List[Dict[str, object]] = []
    buffer: List[str] = []
    current_len = 0
    page_no = 1

    for text in merged_texts:
        if current_len + len(text) > chars_per_page and buffer:
            pages.append({"page": page_no, "text": "\n".join(buffer).strip()})
            page_no += 1
            buffer = []
            current_len = 0

        buffer.append(text)
        current_len += len(text)

    if buffer:
        pages.append({"page": page_no, "text": "\n".join(buffer).strip()})

    return pages
