from __future__ import annotations

import re
from typing import Dict, List


def extract_txt_pages(file_path: str, chars_per_page: int = 2000) -> List[Dict[str, object]]:
    # UTF-8 시도 후 실패 시 CP949로 fallback
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="cp949", errors="ignore") as f:
            content = f.read()

    # 단락 분리 및 정제
    paragraphs = [p.strip() for p in content.splitlines() if p.strip()]

    pages: List[Dict[str, object]] = []
    buffer: List[str] = []
    current_len = 0
    page_no = 1

    for text in paragraphs:
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
