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


def run_document_check(file_path: str, original_name: str | None = None, settings: dict | None = None) -> List[Dict[str, object]]:
    findings, _ = run_document_check_with_questions(file_path, original_name=original_name, settings=settings)
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
    settings: dict | None = None,
) -> tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    pages = extract_by_extension(file_path, original_name=original_name)

    checker_mode = settings.get("checker_mode", "local") if settings else "local"
    llm_url = settings.get("llm_url", "http://localhost:1234/v1") if settings else "http://localhost:1234/v1"
    llm_model = settings.get("llm_model", "google/gemma-4-12b") if settings else "google/gemma-4-12b"

    gemma_checker = None
    if checker_mode in ("llm", "hybrid"):
        try:
            from .checkers.gemma_checker import GemmaChecker
            gemma_checker = GemmaChecker(api_url=llm_url, model_name=llm_model)
            if not gemma_checker.is_available():
                if progress_callback:
                    progress_callback("⚠️ 로컬 LLM(LM Studio) 서버 연결 실패. 기존 로컬 엔진으로 대체합니다.")
                checker_mode = "local"
                gemma_checker = None
        except Exception as e:
            if progress_callback:
                progress_callback(f"⚠️ 로컬 LLM 초기화 실패 ({e}). 기존 로컬 엔진으로 대체합니다.")
            checker_mode = "local"
            gemma_checker = None

    # 기존 로컬 엔진은 local 모드이거나 hybrid 모드일 때, 또는 LLM 사용 중 fallback 되었을 때 필요합니다.
    english_checker = None
    korean_checker = None
    if checker_mode in ("local", "hybrid"):
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
        batch_size = 10
        for b_start in range(0, total_sents, batch_size):
            batch_rows = sentence_rows[b_start : b_start + batch_size]

            # 1. 진행 상태 갱신
            if progress_callback:
                first_sent = str(batch_rows[0]["sentence"])
                trunc_sent = first_sent[:30] + "..." if len(first_sent) > 30 else first_sent
                is_slow_mode = (checker_mode in ("llm", "hybrid"))
                if is_slow_mode or (b_start % 10 == 0) or (b_start + len(batch_rows) >= total_sents):
                    progress_callback(
                        f"페이지 {p_idx+1}/{total_pages} - 문장 {b_start+1}~{min(b_start+batch_size, total_sents)}/{total_sents} 분석 중...\n"
                        f"📝 분석 중인 문장: \"{trunc_sent}\""
                    )

            # 2. 문항 사전 초기화
            for row in batch_rows:
                question_no = str(row.get("question_no", ""))
                if question_no:
                    if question_no not in question_map:
                        question_map[question_no] = {"question_no": question_no, "page": page, "error_count": 0}

            # 3. 분석 결과 수집용 컨테이너
            batch_issues = [[] for _ in range(len(batch_rows))]

            if checker_mode == "local":
                # 기존 로컬 모드
                for idx, row in enumerate(batch_rows):
                    sentence = str(row["sentence"])
                    lang = str(row["lang"])
                    if korean_checker is not None and english_checker is not None:
                        if lang == "ko":
                            batch_issues[idx] = korean_checker.check(sentence)
                        elif lang == "en":
                            batch_issues[idx] = english_checker.check(sentence)
                        else:
                            batch_issues[idx] = korean_checker.check(sentence) + english_checker.check(sentence)

            elif checker_mode == "llm":
                # 로컬 LLM 모드 (배치 일괄 검사)
                if gemma_checker is not None:
                    sentences_to_check = [str(r["sentence"]) for r in batch_rows]
                    llm_issues = gemma_checker.check_batch(sentences_to_check)
                    for issue in llm_issues:
                        sent_idx = issue.get("sent_idx", -1)
                        if 0 <= sent_idx < len(batch_rows):
                            batch_issues[sent_idx].append(issue)

            elif checker_mode == "hybrid":
                # 하이브리드 모드 (로컬 1차 고속 감출 후 LLM 일괄 검토)
                local_sent_indices = []
                sentences_to_verify = []
                
                for idx, row in enumerate(batch_rows):
                    sentence = str(row["sentence"])
                    lang = str(row["lang"])
                    issues = []
                    if korean_checker is not None and english_checker is not None:
                        if lang == "ko":
                            issues = korean_checker.check(sentence)
                        elif lang == "en":
                            issues = english_checker.check(sentence)
                        else:
                            issues = korean_checker.check(sentence) + english_checker.check(sentence)
                    
                    if issues:
                        local_sent_indices.append(idx)
                        sentences_to_verify.append((sentence, issues))
                
                if sentences_to_verify and gemma_checker is not None:
                    llm_issues = gemma_checker.check_batch_with_local_suggestions(sentences_to_verify)
                    for issue in llm_issues:
                        v_idx = issue.get("sent_idx", -1)
                        if 0 <= v_idx < len(local_sent_indices):
                            actual_batch_idx = local_sent_indices[v_idx]
                            batch_issues[actual_batch_idx].append(issue)
                else:
                    # LLM 오프라인 시 기존 로컬 검사 결과를 그대로 반환
                    for idx, local_idx in enumerate(local_sent_indices):
                        batch_issues[local_idx] = sentences_to_verify[idx][1]

            # 4. 검출 결과 findings에 등록
            for idx, row in enumerate(batch_rows):
                sentence = str(row["sentence"])
                question_no = str(row.get("question_no", ""))
                context = build_context(sentence_rows, b_start + idx)
                
                issues = batch_issues[idx]
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
