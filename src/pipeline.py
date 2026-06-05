from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Callable

from .checkers.english_checker import EnglishChecker
from .checkers.korean_checker import KoreanChecker
from .extractors.router import extract_by_extension
from .nlp.sentence_splitter import split_page_sentences


ALLOWED_CHECK_CATEGORIES = {"spacing", "punctuation", "spelling", "orthography"}


def _언어표시(lang: str) -> str:
    if lang == "ko":
        return "한글"
    if lang == "en":
        return "영어"
    return "혼합"


def _저장용_레코드(row: Dict[str, object]) -> Dict[str, object]:
    span = row.get("span", {}) or {}
    offset = span.get("offset", "")
    length = span.get("length", "")
    suggestions = row.get("suggestions", []) or []

    return {
        "문항": row.get("question_no", ""),
        "페이지": row.get("page", ""),
        "언어": _언어표시(str(row.get("lang", ""))),
        "오류유형": row.get("category", ""),
        "설명": row.get("message", ""),
        "문제문장": row.get("sentence", ""),
        "이전문장": row.get("prev_sentence", ""),
        "다음문장": row.get("next_sentence", ""),
        "수정제안": "; ".join(str(s) for s in suggestions),
        "위치정보": f"시작={offset}, 길이={length}",
    }


def _정책허용_오류(row: Dict[str, object]) -> bool:
    category = str(row.get("category", "")).lower()
    if any(token in category for token in ALLOWED_CHECK_CATEGORIES):
        return True

    message = str(row.get("message", "")).lower()
    keywords = [
        "띄어쓰기",
        "공백",
        "구두점",
        "맞춤법",
        "철자",
        "punctuation",
        "space",
        "whitespace",
        "spelling",
        "misspell",
        "typo",
        "orthography",
        "capitalization",
        "casing",
    ]
    return any(keyword in message for keyword in keywords)


def build_context(sentences: List[Dict[str, object]], index: int) -> Dict[str, str]:
    prev_sentence = sentences[index - 1]["sentence"] if index > 0 else ""
    next_sentence = sentences[index + 1]["sentence"] if index < len(sentences) - 1 else ""
    return {"prev_sentence": str(prev_sentence), "next_sentence": str(next_sentence)}


def run_document_check(file_path: str, original_name: str | None = None) -> List[Dict[str, object]]:
    findings, _ = run_document_check_with_questions(file_path, original_name=original_name)
    return findings


def build_input_text_snapshot(file_path: str, original_name: str | None = None) -> Dict[str, object]:
    pages = extract_by_extension(file_path, original_name=original_name)
    page_rows: List[Dict[str, object]] = []
    sentence_rows: List[Dict[str, object]] = []

    for page_row in pages:
        page = int(page_row["page"])
        text = str(page_row["text"])
        page_rows.append({"page": page, "text": text})
        if not text.strip():
            continue
        rows, _ = split_page_sentences(page, text)
        for row in rows:
            sentence_rows.append(
                {
                    "page": int(row.get("page", page)),
                    "sent_idx": int(row.get("sent_idx", 0)),
                    "question_no": str(row.get("question_no", "")),
                    "lang": str(row.get("lang", "")),
                    "sentence": str(row.get("sentence", "")),
                }
            )

    full_text = "\n".join(str(row["sentence"]) for row in sentence_rows)
    return {
        "원본파일명": original_name or Path(file_path).name,
        "페이지별원문": page_rows,
        "검사용문장": sentence_rows,
        "검사용최종텍스트": full_text,
    }


def run_document_check_with_questions(
    file_path: str,
    original_name: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    pages = extract_by_extension(file_path, original_name=original_name)

    if progress_callback:
        progress_callback("로컬 검사기(LanguageTool) 엔진 초기화 중...")

    english_checker = EnglishChecker()
    korean_checker = KoreanChecker(use_spacing=True)

    findings: List[Dict[str, object]] = []
    question_map: Dict[str, Dict[str, object]] = {}
    total_pages = len(pages)
    current_q_no = ""

    for p_idx, page_row in enumerate(pages):
        page = int(page_row["page"])
        text = str(page_row["text"])
        if not text.strip():
            continue

        sentence_rows, current_q_no = split_page_sentences(page, text, current_q_no)
        total_sents = len(sentence_rows)
        for idx, row in enumerate(sentence_rows):
            if progress_callback and (idx % 5 == 0 or idx == total_sents - 1):
                progress_callback(
                    f"페이지 {p_idx+1}/{total_pages} - 문장 {idx+1}/{total_sents} 분석 중..."
                )

            sentence = str(row["sentence"])
            lang = str(row["lang"])
            question_no = str(row.get("question_no", ""))
            context = build_context(sentence_rows, idx)

            if question_no:
                if question_no not in question_map:
                    question_map[question_no] = {"question_no": question_no, "page": page, "error_count": 0}

            if lang == "ko":
                issues = korean_checker.check(sentence)
            elif lang == "en":
                issues = english_checker.check(sentence)
            else:
                ko_issues = korean_checker.check(sentence)
                en_issues = english_checker.check(sentence)
                issues = ko_issues + en_issues

            for issue in issues:
                finding = {
                    "question_no": question_no,
                    "page": page,
                    "sentence": sentence,
                    "prev_sentence": context["prev_sentence"],
                    "next_sentence": context["next_sentence"],
                    "lang": issue["lang"],
                    "category": issue["category"],
                    "message": issue["message"],
                    "suggestions": issue["suggestions"],
                    "span": issue["span"],
                }
                if _정책허용_오류(finding):
                    findings.append(finding)
                    if question_no and question_no in question_map:
                        question_map[question_no]["error_count"] = int(
                            question_map[question_no]["error_count"]
                        ) + 1

    question_results = sorted(question_map.values(), key=lambda x: int(str(x["question_no"])))
    return findings, question_results


def save_findings_json(findings: List[Dict[str, object]], output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    korean_findings = [_저장용_레코드(row) for row in findings]
    with path.open("w", encoding="utf-8") as fp:
        json.dump(korean_findings, fp, ensure_ascii=False, indent=2)


def save_findings_csv(findings: List[Dict[str, object]], output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "문항",
        "페이지",
        "언어",
        "오류유형",
        "설명",
        "문제문장",
        "이전문장",
        "다음문장",
        "수정제안",
        "위치정보",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=columns)
        writer.writeheader()
        for row in findings:
            writer.writerow(_저장용_레코드(row))
