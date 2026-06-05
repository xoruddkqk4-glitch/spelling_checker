from __future__ import annotations

import os
import json
import html
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List

from engineio.payload import Payload
Payload.max_decode_packets = 10000

import chainlit as cl
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from src.pipeline import (
    build_input_text_snapshot,
    run_document_check_with_questions,
    save_findings_csv,
    save_findings_json,
)

import difflib

def get_diff_spans(a: str, b: str) -> tuple[List[tuple[int, int]], List[tuple[int, int]]]:
    matcher = difflib.SequenceMatcher(None, a, b)
    opcodes = matcher.get_opcodes()
    a_spans = []
    b_spans = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'replace':
            if i1 < i2:
                a_spans.append((i1, i2))
            if j1 < j2:
                b_spans.append((j1, j2))
        elif tag == 'delete':
            if i1 < i2:
                a_spans.append((i1, i2))
        elif tag == 'insert':
            if j1 < j2:
                b_spans.append((j1, j2))
    return a_spans, b_spans


def highlight_html_by_spans(text: str, spans: List[tuple[int, int]], bg_color: str = "#ff8a80") -> str:
    if not spans:
        return html.escape(text)
    parts = []
    last_idx = 0
    span_start = f'<span style="background-color:{bg_color};color:#111;padding:0 1px;">'
    span_end = '</span>'
    for start, end in spans:
        parts.append(html.escape(text[last_idx:start]))
        parts.append(f"{span_start}{html.escape(text[start:end])}{span_end}")
        last_idx = end
    parts.append(html.escape(text[last_idx:]))
    return "".join(parts)


def draw_mixed_line(c, x_start: float, y: float, chunks: List[tuple[str, bool]], font_name: str = "HYSMyeongJo-Medium", font_size: float = 10.0, hl_color = colors.Color(1.0, 0.7, 0.7), line_height: float = 14.0) -> float:
    x = x_start
    margin_x = 36.0
    right_limit = 595.0 - 36.0
    c.setFont(font_name, font_size)
    for text, hl in chunks:
        if not text:
            continue
        for ch in text:
            w = pdfmetrics.stringWidth(ch, font_name, font_size)
            if x + w > right_limit:
                y -= line_height
                if y < 40:
                    c.showPage()
                    y = 842.0 - 40.0
                    c.setFont(font_name, font_size)
                x = margin_x + 15.0
            if hl:
                c.setFillColor(hl_color)
                c.rect(x - 0.5, y - 2, w + 1.0, font_size + 2, stroke=0, fill=1)
            c.setFillColor(colors.black)
            c.drawString(x, y, ch)
            x += w
    return y


def build_diff_chunks(original: str, first: str, original_spans: List[tuple[int, int]], first_spans: List[tuple[int, int]]) -> List[tuple[str, bool]]:
    chunks = [("- 수정 제안: '", False)]
    last_idx = 0
    for start, end in original_spans:
        if start > last_idx:
            chunks.append((original[last_idx:start], False))
        chunks.append((original[start:end], True))
        last_idx = end
    if last_idx < len(original):
        chunks.append((original[last_idx:], False))
        
    chunks.append(("'를 '", False))
    
    last_idx = 0
    for start, end in first_spans:
        if start > last_idx:
            chunks.append((first[last_idx:start], False))
        chunks.append((first[start:end], True))
        last_idx = end
    if last_idx < len(first):
        chunks.append((first[last_idx:], False))
        
    chunks.append(("'로 수정", False))
    return chunks


def build_diff_chunks_single(first: str, first_spans: List[tuple[int, int]]) -> List[tuple[str, bool]]:
    chunks = [("- 수정 제안: '", False)]
    last_idx = 0
    for start, end in first_spans:
        if start > last_idx:
            chunks.append((first[last_idx:start], False))
        chunks.append((first[start:end], True))
        last_idx = end
    if last_idx < len(first):
        chunks.append((first[last_idx:], False))
    chunks.append(("'로 수정", False))
    return chunks


class TemporaryInputFile:
    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path


ALLOWED_EXTS = [".hwpx", ".pdf", ".docx", ".txt"]
ACCEPTED_FILE_TYPES = [".hwpx", ".pdf", ".docx", ".txt"]
MAX_CHAT_CHARS = 7000
MAX_DETAIL_ITEMS_IN_CHAT = 120
INPUTS_DIR = Path("inputs")
TMP_REPORT_DIR = Path(tempfile.gettempdir()) / "01spelling_checker_reports"

# Chainlit의 파일 업로드 시 발생할 수 있는 WinError 3(경로 찾기 오류)를 방지하기 위해 .files 폴더를 미리 생성합니다.
Path(".files").mkdir(parents=True, exist_ok=True)
def render_findings(findings: List[dict]) -> str:
    if not findings:
        return "오류가 있는 문항 세부 내역은 없습니다."

    def 오류_구간_텍스트(sentence: str, span: dict) -> str:
        text = str(sentence)
        offset = int(span.get("offset", 0) or 0)
        length = int(span.get("length", 1) or 1)
        if offset < 0:
            offset = 0
        if offset >= len(text):
            return ""
        end = min(len(text), offset + max(1, length))
        return text[offset:end].strip()

    def 노란_강조_문장(sentence: str, span: dict) -> str:
        text = str(sentence)
        offset = int(span.get("offset", 0) or 0)
        length = int(span.get("length", 1) or 1)
        if offset < 0:
            offset = 0
        if offset >= len(text):
            return html.escape(text)
        end = min(len(text), offset + max(1, length))
        before = html.escape(text[:offset])
        target = html.escape(text[offset:end])
        after = html.escape(text[end:])
        return (
            f"{before}<span style=\"background-color:#fff59d;color:#111;padding:0 1px;\">{target}</span>{after}"
        )

    def 수정_제안_문구(sentence: str, span: dict, suggestions: List[str]) -> str:
        text = str(sentence)
        offset = int(span.get("offset", 0) or 0)
        length = int(span.get("length", 1) or 1)
        if offset < 0:
            offset = 0
        end = min(len(text), offset + max(1, length))
        original = text[offset:end].strip() if offset < len(text) else ""

        if not suggestions:
            return "수정 제안: 제안 없음"

        first = str(suggestions[0]).strip()
        if not original:
            return f"수정 제안: '{first}'로 수정"
        return f"수정 제안: '{original}'를 '{first}'로 수정"

    lines: List[str] = []
    limited = findings[:MAX_DETAIL_ITEMS_IN_CHAT]
    for i, item in enumerate(limited, start=1):
        span = item.get("span", {}) or {}
        question_no = str(item.get("question_no", "")).strip()
        unit_text = f"{question_no}번 문항" if question_no else f"{item['page']}페이지"
        sentence = str(item.get("sentence", ""))
        강조문장 = 노란_강조_문장(sentence, span)
        오류구간 = 오류_구간_텍스트(sentence, span)
        suggestions = item.get("suggestions", []) or []
        
        if not suggestions:
            수정문구 = "* 🛠️ **수정 제안:** 제안 없음"
        else:
            first = str(suggestions[0]).strip()
            offset = int(span.get("offset", 0) or 0)
            length = int(span.get("length", 1) or 1)
            if offset < 0:
                offset = 0
            end = min(len(sentence), offset + max(1, length))
            original = sentence[offset:end].strip() if offset < len(sentence) else ""
            
            if not original:
                first_hl = highlight_html_by_spans(first, [(0, len(first))])
                수정문구 = f"* 🛠️ **수정 제안:** '{first_hl}'로 수정"
            else:
                orig_spans, first_spans = get_diff_spans(original, first)
                orig_hl = highlight_html_by_spans(original, orig_spans)
                first_hl = highlight_html_by_spans(first, first_spans)
                수정문구 = f"* 🛠️ **수정 제안:** '{orig_hl}'를 '{first_hl}'로 수정"

        오류이유 = str(item.get("message", "")).strip()
        오류이유문구 = f"* 💡 **오류 이유:** {오류이유}\n" if 오류이유 else ""
        오류구간문구 = (
            f"* 📍 **오류 구간:** '{오류구간}'" if 오류구간 else "* 📍 **오류 구간:** 특정 불가"
        )
        lines.append(
            (
                f"### **{i}. {unit_text}**\n"
                f"* 📝 **오류 문장:** {강조문장}\n"
                f"{오류구간문구}\n"
                f"{오류이유문구}"
                f"{수정문구}"
            )
        )
    text = "\n\n".join(lines)
    if len(findings) > MAX_DETAIL_ITEMS_IN_CHAT:
        remain = len(findings) - MAX_DETAIL_ITEMS_IN_CHAT
        text += (
            f"\n\n채팅창에는 상세 {MAX_DETAIL_ITEMS_IN_CHAT}건만 표시했습니다. "
            f"나머지 {remain}건은 저장 파일(JSON/CSV)에서 확인해 주세요."
        )
    return text


def render_question_overview(question_results: List[dict]) -> str:
    if not question_results:
        return "문항 패턴(예: 1.)이 인식되지 않았습니다."

    lines: List[str] = []
    for item in question_results:
        q_no = str(item.get("question_no", ""))
        error_count = int(item.get("error_count", 0))
        if error_count > 0:
            lines.append(f"{q_no}번 문항: 오류 {error_count}건")
        else:
            lines.append(f"{q_no}번 문항: 오류 없음")
    return "문항별 점검 결과입니다.\n" + "\n".join(lines)


def create_temp_report_pdf(file_name: str, overview_text: str, findings: List[dict]) -> Path:
    TMP_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(file_name).stem
    pdf_path = TMP_REPORT_DIR / f"{stem}_{timestamp}_report.pdf"

    pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    width, height = A4
    margin_x = 36
    y = height - 40
    line_height = 14

    def draw_line(line: str, font_size: int = 10) -> None:
        nonlocal y
        if y < 40:
            c.showPage()
            y = height - 40
        c.setFont("HYSMyeongJo-Medium", font_size)
        c.drawString(margin_x, y, line[:140])
        y -= line_height

    def draw_highlight_sentence(sentence: str, span: dict) -> None:
        nonlocal y
        text = str(sentence)
        offset = int(span.get("offset", 0) or 0)
        length = int(span.get("length", 1) or 1)
        start = max(0, min(len(text), offset))
        end = max(start, min(len(text), start + max(1, length)))

        before = text[:start]
        target = text[start:end]
        after = text[end:]

        chunks = [
            ("- 오류 문장: ", False),
            (before, False),
            (target, True),
            (after, False)
        ]
        
        if y < 40:
            c.showPage()
            y = height - 40
            
        y = draw_mixed_line(c, margin_x, y, chunks, font_size=10, hl_color=colors.Color(1.0, 0.96, 0.62))
        y -= line_height

    c.setTitle("맞춤법 점검 결과")
    c.setFont("HYSMyeongJo-Medium", 10)
    draw_line("맞춤법 점검 결과")
    draw_line(f"원본 파일: {file_name}")
    draw_line("")
    for line in overview_text.splitlines():
        draw_line(line)
    draw_line("")

    if not findings:
        draw_line("오류가 있는 항목이 없습니다.")
    else:
        for i, item in enumerate(findings, start=1):
            span = item.get("span", {}) or {}
            sentence = str(item.get("sentence", ""))
            suggestions = item.get("suggestions", []) or []
            first = str(suggestions[0]).strip() if suggestions else ""

            offset = int(span.get("offset", 0) or 0)
            length = int(span.get("length", 1) or 1)
            start = max(0, min(len(sentence), offset))
            end = max(start, min(len(sentence), start + max(1, length)))
            original = sentence[start:end].strip()

            question_no = str(item.get("question_no", "")).strip()
            unit_text = f"{question_no}번 문항" if question_no else f"{item['page']}페이지"
            오류이유 = str(item.get("message", "")).strip()
            
            draw_line(f"{i}. {unit_text}", font_size=12)
            draw_highlight_sentence(sentence, span)
            
            오류구간문구 = f"- 오류 구간: '{original}'" if original else "- 오류 구간: 특정 불가"
            if y < 40:
                c.showPage()
                y = height - 40
            y = draw_mixed_line(c, margin_x, y, [(오류구간문구, False)], font_size=10)
            y -= line_height
            
            if 오류이유:
                오류이유문구 = f"- 오류 이유: {오류이유}"
                if y < 40:
                    c.showPage()
                    y = height - 40
                y = draw_mixed_line(c, margin_x, y, [(오류이유문구, False)], font_size=10)
                y -= line_height
                
            if first and original:
                orig_spans, first_spans = get_diff_spans(original, first)
                chunks = build_diff_chunks(original, first, orig_spans, first_spans)
                
                if y < 40:
                    c.showPage()
                    y = height - 40
                y = draw_mixed_line(c, margin_x, y, chunks, font_size=10)
                y -= line_height
            elif first:
                _, first_spans = get_diff_spans("", first)
                chunks = build_diff_chunks_single(first, first_spans)
                
                if y < 40:
                    c.showPage()
                    y = height - 40
                y = draw_mixed_line(c, margin_x, y, chunks, font_size=10)
                y -= line_height
            else:
                if y < 40:
                    c.showPage()
                    y = height - 40
                y = draw_mixed_line(c, margin_x, y, [("- 수정 제안: 제안 없음", False)], font_size=10)
                y -= line_height
            draw_line("")

    c.save()
    return pdf_path


def cleanup_temp_reports(paths: List[str]) -> None:
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            continue


def split_for_chat(text: str, max_chars: int = MAX_CHAT_CHARS) -> List[str]:
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in text.splitlines():
        add_len = len(line) + 1
        if current and current_len + add_len > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_len = add_len
        else:
            current.append(line)
            current_len += add_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def filter_by_page(findings: List[dict], page: int | None) -> List[dict]:
    if page is None:
        return findings
    return [item for item in findings if int(item["page"]) == page]


def save_uploaded_input(file_path: str, file_name: str) -> Path:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    name = Path(file_name).name
    stem = Path(name).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = INPUTS_DIR / f"{stem}_{timestamp}_input_text.json"

    snapshot = build_input_text_snapshot(file_path, original_name=file_name)
    with dst.open("w", encoding="utf-8") as fp:
        json.dump(snapshot, fp, ensure_ascii=False, indent=2)
    return dst


async def send_page_filter_actions(findings: List[dict]) -> None:
    pages = sorted({int(item["page"]) for item in findings})
    if not pages:
        return

    actions = [
        cl.Action(name="filter_page", payload={"page": "all"}, label="전체 페이지"),
    ]
    for page in pages[:20]:
        actions.append(
            cl.Action(name="filter_page", payload={"page": str(page)}, label=f"페이지 {page}")
        )

    await cl.Message(
        content="아래 버튼으로 페이지별 결과를 필터링할 수 있습니다.",
        actions=actions,
    ).send()


@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set("findings", [])
    cl.user_session.set("temp_report_paths", [])
    await cl.Message(
        content=(
            "문서를 업로드하거나 검사할 텍스트를 직접 입력창에 붙여넣어 전송해 주세요.\n"
            "오프라인으로 한/영 맞춤법, 띄어쓰기, 구두점을 검사합니다.\n"
            "지원 문서 형식: .hwpx, .pdf, .docx, .txt"
        )
    ).send()


async def _pick_supported_file_from_message(message: cl.Message):
    files = []
    for element in getattr(message, "elements", []) or []:
        path = getattr(element, "path", "")
        name = getattr(element, "name", "")
        if not path or not name:
            continue
        ext = Path(name).suffix.lower()
        if ext in ALLOWED_EXTS:
            files.append(element)
    return files[0] if files else None


def make_loader_html(status_text: str, is_done: bool = False) -> str:
    if is_done:
        return f"""<div class="spelling-loader-container" style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 24px; border-radius: 16px; background: linear-gradient(135deg, rgba(20, 20, 35, 0.8), rgba(10, 10, 20, 0.95)); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border: 1px solid rgba(16, 185, 129, 0.4); box-shadow: 0 10px 30px rgba(16, 185, 129, 0.15); margin: 20px 0; max-width: 480px; color: #fff; font-family: 'Inter', 'Outfit', sans-serif;">
<div style="display: flex; align-items: center; justify-content: center; width: 60px; height: 60px; background: linear-gradient(135deg, #10B981, #059669); border-radius: 50%; box-shadow: 0 0 20px rgba(16, 185, 129, 0.4); margin-bottom: 16px;">
<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor" style="width: 32px; height: 32px; color: #fff;"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>
</div>
<h3 style="margin: 0 0 8px 0; font-size: 18px; font-weight: 700; letter-spacing: -0.5px; background: linear-gradient(90deg, #34D399, #059669); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">맞춤법 검사 완료!</h3>
<p style="margin: 0; font-size: 14px; color: #9CA3AF; text-align: center;">{status_text}</p>
</div>"""
    
    return f"""<div class="spelling-loader-container" style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 24px; border-radius: 16px; background: linear-gradient(135deg, rgba(20, 20, 35, 0.8), rgba(10, 10, 20, 0.95)); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border: 1px solid rgba(99, 102, 241, 0.3); box-shadow: 0 10px 30px rgba(99, 102, 241, 0.15); margin: 20px 0; max-width: 480px; color: #fff; font-family: 'Inter', 'Outfit', sans-serif; position: relative; overflow: hidden;">
<div style="position: absolute; width: 150px; height: 150px; background: radial-gradient(circle, rgba(99, 102, 241, 0.15) 0%, rgba(99, 102, 241, 0) 70%); top: -50px; left: -50px; pointer-events: none;"></div>
<div style="display: flex; align-items: center; justify-content: center; margin-bottom: 20px; height: 70px;">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="hourglass-svg" style="width: 55px; height: 55px; color: #8b5cf6; filter: drop-shadow(0 0 8px rgba(139, 92, 246, 0.5)); animation: spelling-hourglass-rotate 2.5s cubic-bezier(0.77, 0, 0.175, 1) infinite;">
<path d="M5 2h14" />
<path d="M5 22h14" />
<path d="M19 2v4c0 3.867-3.134 7-7 7s-7-3.133-7-7V2" />
<path d="M19 22v-4c0-3.867-3.134-7-7-7s-7 3.133-7 7v4" />
<path d="M12 11v6" stroke="#ec4899" stroke-dasharray="4" style="animation: spelling-sand-dripping 1.2s linear infinite;" />
<circle cx="12" cy="18" r="1.5" fill="#ec4899" />
</svg>
</div>
<h3 style="margin: 0 0 8px 0; font-size: 18px; font-weight: 700; letter-spacing: -0.5px; background: linear-gradient(90deg, #a5b4fc, #c084fc); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">맞춤법 검사 진행 중</h3>
<p style="margin: 0 0 16px 0; font-size: 14px; color: #9CA3AF; text-align: center; min-height: 20px; font-weight: 500;">{status_text}</p>
<div style="width: 100%; height: 4px; background: rgba(255, 255, 255, 0.05); border-radius: 2px; position: relative; overflow: hidden;">
<div style="position: absolute; height: 100%; width: 40%; background: linear-gradient(90deg, #6366f1, #ec4899); border-radius: 2px; animation: spelling-loading-bar 1.5s ease-in-out infinite;"></div>
</div>
<style>
@keyframes spelling-hourglass-rotate {{
0% {{ transform: rotate(0deg); }}
40% {{ transform: rotate(0deg); }}
60% {{ transform: rotate(180deg); }}
100% {{ transform: rotate(180deg); }}
}}
@keyframes spelling-sand-dripping {{
from {{ stroke-dashoffset: 8; }}
to {{ stroke-dashoffset: 0; }}
}}
@keyframes spelling-loading-bar {{
0% {{ left: -40%; }}
50% {{ left: 100%; }}
100% {{ left: 100%; }}
}}
</style>
</div>"""


async def _process_uploaded_file(file) -> None:
    file_name = str(getattr(file, "name", "uploaded_file"))
    file_path = str(getattr(file, "path", ""))
    if not file_path:
        await cl.Message(content="업로드 파일 경로를 읽지 못했습니다. 다시 업로드해 주세요.").send()
        return

    ext = Path(file_name).suffix.lower()
    if ext not in ALLOWED_EXTS:
        if ext == ".hwp":
            await cl.Message(
                content=(
                    "⚠️ **HWP 파일은 직접 지원하지 않습니다.**\n\n"
                    "아래 방법 중 하나를 이용하여 점검해 주세요:\n"
                    "1. 한글 프로그램(아래아한글)에서 **'다른 이름으로 저장'**을 누르고, 파일 형식을 **'한글 표준 문서 (*.hwpx)'**로 변경하여 저장한 후 업로드해 주세요. (본 프로그램은 최신 개방형 표준인 `.hwpx`만 지원합니다.)\n"
                    "2. 문서 본문 내용을 전체 선택(Ctrl+A) 및 복사(Ctrl+C)한 뒤, 아래 입력창에 붙여넣어(Ctrl+V) 텍스트로 바로 전송해 주세요."
                )
            ).send()
        else:
            await cl.Message(
                content=(
                    f"⚠️ **지정하신 파일 형식({ext})은 지원하지 않는 파일 형식입니다.**\n\n"
                    f"본 검사기는 다음 형식만 지원합니다: {', '.join(ALLOWED_EXTS)}\n"
                    "확장자를 확인한 후 다시 업로드해 주세요."
                )
            ).send()
        return

    size_bytes = Path(file_path).stat().st_size if Path(file_path).exists() else 0
    await cl.Message(
        content=(
            f"업로드 확인 완료: {file_name}\n"
            f"확장자: {ext}\n"
            f"파일 크기: {size_bytes} bytes"
        )
    ).send()

    # 문서 파싱 및 입력 추적 저장 전단계부터 즉시 동그라미 로더(동적 개체)를 출력합니다.
    loader_msg = cl.Message(
        content=make_loader_html(
            "문서 파싱 및 입력 추적 데이터 저장 중..."
        )
    )
    await loader_msg.send()

    import anyio
    saved_input_path = await anyio.to_thread.run_sync(save_uploaded_input, file_path, file_name)
    
    loader_msg.content = make_loader_html(
        f"입력 텍스트 JSON 저장 완료: {saved_input_path.name}\n"
        "영어 엔진(LanguageTool)은 최초 1회 초기화에 시간이 걸릴 수 있습니다. "
        "첫 실행에서는 1~2분 정도 소요될 수 있습니다."
    )
    await loader_msg.update()

    try:
        async with cl.Step(name="문서 분석 진행 중") as step:
            step.input = (
                "영어 엔진(LanguageTool)은 최초 1회 초기화에 시간이 걸릴 수 있습니다. "
                "첫 실행에서는 1~2분 정도 소요될 수 있습니다."
            )
            def update_progress(msg: str) -> None:
                step.input = msg
                cl.run_sync(step.update())
                
                # 실시간 로더 메시지 갱신
                loader_msg.content = make_loader_html(msg)
                cl.run_sync(loader_msg.update())

            import anyio
            findings, question_results = await anyio.to_thread.run_sync(
                run_document_check_with_questions, file_path, file_name, update_progress
            )
            step.output = "문서 분석이 완료되었습니다."
            
        loader_msg.content = make_loader_html(
            f"분석 완료! 총 {len(question_results)}개 문항, {len(findings)}개 오류 검증됨.",
            is_done=True
        )
        await loader_msg.update()
        
        import asyncio
        await asyncio.sleep(1.0)
        question_count = len(question_results)
        finding_count = len(findings)
        await cl.Message(
            content=(
                "분석이 완료되었습니다.\n"
                f"인식된 문항 수: {question_count}\n"
                f"오류 항목 수: {finding_count}"
            )
        ).send()
    except Exception as exc:
        await cl.Message(content=f"분석 중 오류가 발생했습니다: {exc}").send()
        return
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

    cl.user_session.set("findings", findings)

    out_dir = Path("outputs")
    stem = Path(file_name).stem
    json_path = out_dir / f"{stem}_findings.json"
    csv_path = out_dir / f"{stem}_findings.csv"
    save_findings_json(findings, str(json_path))
    save_findings_csv(findings, str(csv_path))

    overview_text = render_question_overview(question_results)
    detail_text = render_findings(findings)
    save_text = f"저장이 완료되었습니다. JSON 파일은 {json_path}이고, CSV 파일은 {csv_path}입니다."

    for chunk in split_for_chat(overview_text):
        await cl.Message(content=chunk).send()
    for chunk in split_for_chat(detail_text):
        await cl.Message(content=chunk).send()
    await cl.Message(content=save_text).send()

    pdf_path = create_temp_report_pdf(file_name, overview_text, findings)
    temp_paths = cl.user_session.get("temp_report_paths") or []
    temp_paths.append(str(pdf_path))
    cl.user_session.set("temp_report_paths", temp_paths)
    await cl.Message(
        content="PDF 결과 파일을 내려받을 수 있습니다. 이 파일은 채팅이 종료되면 자동 삭제됩니다.",
        elements=[cl.File(name=pdf_path.name, path=str(pdf_path), display="inline")],
    ).send()

    await send_page_filter_actions(findings)


async def _pick_unsupported_file_from_message(message: cl.Message) -> str | None:
    for element in getattr(message, "elements", []) or []:
        path = getattr(element, "path", "")
        name = getattr(element, "name", "")
        if not path or not name:
            continue
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXTS:
            return ext
    return None


@cl.on_message
async def on_message(message: cl.Message) -> None:
    file = await _pick_supported_file_from_message(message)
    unsupported_ext = await _pick_unsupported_file_from_message(message)

    if file is None:
        if unsupported_ext == ".hwp":
            await cl.Message(
                content=(
                    "⚠️ **HWP 파일은 직접 지원하지 않습니다.**\n\n"
                    "아래 방법 중 하나를 이용하여 점검해 주세요:\n"
                    "1. 한글 프로그램(아래아한글)에서 **'다른 이름으로 저장'**을 누르고, 파일 형식을 **'한글 표준 문서 (*.hwpx)'**로 변경하여 저장한 후 업로드해 주세요. (본 프로그램은 최신 개방형 표준인 `.hwpx`만 지원합니다.)\n"
                    "2. 문서 본문 내용을 전체 선택(Ctrl+A) 및 복사(Ctrl+C)한 뒤, 아래 입력창에 붙여넣어(Ctrl+V) 텍스트로 바로 전송해 주세요."
                )
            ).send()
            return
        elif unsupported_ext:
            await cl.Message(
                content=(
                    f"⚠️ **지정하신 파일 형식({unsupported_ext})은 지원하지 않는 파일 형식입니다.**\n\n"
                    f"본 검사기는 다음 형식만 지원합니다: {', '.join(ALLOWED_EXTS)}\n"
                    "확장자를 확인한 후 다시 업로드해 주세요."
                )
            ).send()
            return

        text_content = (message.content or "").strip()
        if text_content:
            # 직접 입력된 텍스트 처리
            import tempfile
            from pathlib import Path

            temp_dir = Path(tempfile.gettempdir()) / "spelling_checker_direct_inputs"
            temp_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            temp_file_path = temp_dir / f"direct_input_{timestamp}.txt"

            with open(temp_file_path, "w", encoding="utf-8") as f:
                f.write(text_content)

            virtual_file = TemporaryInputFile(
                name="direct_input.txt",
                path=str(temp_file_path)
            )

            await cl.Message(content="직접 입력하신 텍스트를 로컬 분석용 txt 포맷 파일로 변환하여 분석을 개시합니다.").send()
            await _process_uploaded_file(virtual_file)
            return
        else:
            files = await cl.AskFileMessage(
                content="검사할 파일을 업로드하거나, 텍스트를 직접 입력해 주세요.",
                accept=ACCEPTED_FILE_TYPES,
                max_size_mb=50,
                timeout=180,
            ).send()
            if not files:
                await cl.Message(content="입력된 내용 또는 업로드된 파일이 없습니다.").send()
                return
            file = files[0]
    else:
        await cl.Message(content="요청을 받았습니다. 업로드 파일과 확장자를 확인합니다.").send()

    await _process_uploaded_file(file)


@cl.action_callback("filter_page")
async def on_filter_page(action: cl.Action) -> None:
    findings = cl.user_session.get("findings") or []
    payload = getattr(action, "payload", {}) or {}
    value = str(payload.get("page", getattr(action, "value", "all")))
    page = None if value == "all" else int(value)
    filtered = filter_by_page(findings, page)

    title = "전체 페이지 결과입니다." if page is None else f"{page}페이지 결과입니다."
    await cl.Message(content=f"{title}\n\n{render_findings(filtered)}").send()


@cl.on_chat_end
async def on_chat_end() -> None:
    temp_paths = cl.user_session.get("temp_report_paths") or []
    cleanup_temp_reports([str(p) for p in temp_paths])
