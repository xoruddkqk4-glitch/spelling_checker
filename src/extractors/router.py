from __future__ import annotations

import os
from typing import Dict, List

from .docx_extractor import extract_docx_pages
from .hwpx_extractor import extract_hwpx_pages
from .pdf_extractor import extract_pdf_pages
from .txt_extractor import extract_txt_pages


def extract_by_extension(file_path: str, original_name: str | None = None) -> List[Dict[str, object]]:
    base_name = original_name if original_name else file_path
    ext = os.path.splitext(base_name)[1].lower()

    if ext == ".hwpx":
        return extract_hwpx_pages(file_path)
    if ext == ".pdf":
        return extract_pdf_pages(file_path)
    if ext == ".docx":
        return extract_docx_pages(file_path)
    if ext == ".txt":
        return extract_txt_pages(file_path)

    raise ValueError(f"Unsupported extension: {ext}")
