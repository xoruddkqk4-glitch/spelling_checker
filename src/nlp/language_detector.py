from __future__ import annotations

import re


KOREAN_RE = re.compile(r"[가-힣]")
ENGLISH_RE = re.compile(r"[A-Za-z]")


def detect_language(sentence: str) -> str:
    ko_count = len(KOREAN_RE.findall(sentence))
    en_count = len(ENGLISH_RE.findall(sentence))

    if ko_count == 0 and en_count == 0:
        return "unknown"
    if ko_count > en_count:
        return "ko"
    if en_count > ko_count:
        return "en"
    return "mixed"
