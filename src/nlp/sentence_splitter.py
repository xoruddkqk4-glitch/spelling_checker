from __future__ import annotations

import re
from typing import Dict, List

import kss
import nltk

from .language_detector import detect_language


QUESTION_NO_INLINE_RE = re.compile(r"^\s*(\d+)\. ")
QUESTION_NO_STANDALONE_RE = re.compile(r"^\s*(\d+)\.\s*$")


def ensure_nltk_punkt() -> None:
    try:
        nltk.data.find("tokenizers/punkt")
    except LookupError:
        nltk.download("punkt", quiet=True)
    try:
        nltk.data.find("tokenizers/punkt_tab/english")
    except LookupError:
        nltk.download("punkt_tab", quiet=True)


def split_page_sentences(
    page: int,
    text: str,
    initial_question_no: str = "",
) -> tuple[List[Dict[str, object]], str]:
    ensure_nltk_punkt()

    ko_sentences = kss.split_sentences(text)
    en_sentences = nltk.sent_tokenize(text)
    base = ko_sentences if len(ko_sentences) >= len(en_sentences) else en_sentences

    # 1. 원본 텍스트의 라인 시작부에서만 진짜 문항 번호를 추출하여 오프셋 경계를 생성합니다.
    boundaries = []
    current_offset = 0
    lines = text.split('\n')
    for line in lines:
        inline_match = QUESTION_NO_INLINE_RE.match(line)
        standalone_match = QUESTION_NO_STANDALONE_RE.match(line)
        
        if inline_match:
            boundaries.append((current_offset, inline_match.group(1)))
        elif standalone_match:
            boundaries.append((current_offset, standalone_match.group(1)))
            
        current_offset += len(line) + 1 # +1 for \n

    rows: List[Dict[str, object]] = []
    current_search_idx = 0
    last_q_no = initial_question_no

    for idx, sentence in enumerate(base):
        s = sentence.strip()
        if not s:
            continue

        # 문장의 원본 텍스트 내 시작 오프셋 탐색
        sent_start_idx = text.find(s, current_search_idx)
        if sent_start_idx == -1:
            sent_start_idx = current_search_idx
        else:
            current_search_idx = sent_start_idx + len(s)

        # 문장 시작지점 이전의 가장 최근 진짜 문항 번호 맵핑
        question_no = last_q_no
        for b_offset, q_no in boundaries:
            if b_offset <= sent_start_idx:
                question_no = q_no
            else:
                break

        last_q_no = question_no

        rows.append(
            {
                "page": page,
                "sent_idx": idx,
                "sentence": s,
                "lang": detect_language(s),
                "question_no": question_no,
            }
        )
    return rows, last_q_no
