from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List

import language_tool_python
from kiwipiepy import Kiwi

try:
    from pykospacing import Spacing
except Exception:
    Spacing = None


def clean_spacing_with_kiwi(original: str, corrected: str, kiwi: Kiwi) -> str:
    orig_map = []
    for idx, ch in enumerate(original):
        if ch != " ":
            orig_map.append(idx)
            
    corr_map = []
    for idx, ch in enumerate(corrected):
        if ch != " ":
            corr_map.append(idx)
            
    if len(orig_map) != len(corr_map):
        return corrected

    try:
        tokens = kiwi.tokenize(original)
    except Exception:
        return corrected

    token_by_char_idx = {}
    for t in tokens:
        for offset in range(t.len):
            token_by_char_idx[t.start + offset] = t

    disallowed_spaces = set()
    
    for i in range(len(orig_map) - 1):
        orig_curr = orig_map[i]
        orig_next = orig_map[i+1]
        
        orig_had_no_space = (orig_next == orig_curr + 1)
        
        corr_curr = corr_map[i]
        corr_next = corr_map[i+1]
        corr_has_space = (corr_next > corr_curr + 1)
        
        if orig_had_no_space and corr_has_space:
            t_curr = token_by_char_idx.get(orig_curr)
            t_next = token_by_char_idx.get(orig_next)
            
            is_same_token = (t_curr is not None and t_next is not None and t_curr.start == t_next.start)
            
            if is_same_token:
                disallowed_spaces.add(orig_curr)
                continue
                
            if t_next is not None:
                tag = t_next.tag
                if tag.startswith("J") or tag.startswith("XS") or tag.startswith("E") or tag in {"VCP", "VCN"}:
                    disallowed_spaces.add(orig_curr)
                    continue

    final_parts = []
    for i in range(len(orig_map)):
        orig_curr = orig_map[i]
        corr_curr = corr_map[i]
        
        final_parts.append(corrected[corr_curr])
        
        if i < len(orig_map) - 1:
            orig_next = orig_map[i+1]
            corr_next = corr_map[i+1]
            
            corr_has_space = (corr_next > corr_curr + 1)
            
            if corr_has_space:
                if orig_curr not in disallowed_spaces:
                    space_len = corr_next - corr_curr - 1
                    final_parts.append(" " * space_len)
                    
    return "".join(final_parts)


MULTI_SPACE_RE = re.compile(r"[가-힣A-Za-z0-9]\s{2,}[가-힣A-Za-z0-9]")
MULTI_PUNCT_RE = re.compile(r"([.!?]{2,}|[,]{2,}|[~]{2,})")
PAREN_UNBALANCED_RE = re.compile(r"[()]")


def _is_unbalanced(regex: re.Pattern[str], text: str) -> bool:
    return len(regex.findall(text)) % 2 == 1


def _find_quote_pair_issues(text: str) -> List[Dict[str, int | str]]:
    pairs = {"'": "'", '"': '"', "‘": "’", "“": "”"}
    openers = set(pairs.keys())
    closers = set(pairs.values())
    stack: List[Dict[str, int | str]] = []
    issues: List[Dict[str, int | str]] = []

    for idx, ch in enumerate(text):
        if ch in openers:
            # ASCII ' 와 " 는 여닫이가 동일하므로 스택 top과 같으면 닫는 부호로 처리
            if ch in {"'", '"'} and stack and stack[-1]["expected"] == ch:
                stack.pop()
            else:
                stack.append({"char": ch, "expected": pairs[ch], "index": idx})
            continue

        if ch in closers:
            if not stack:
                issues.append({"index": idx, "char": ch, "reason": "unopened"})
                continue
            top = stack[-1]
            if top["expected"] != ch:
                issues.append({"index": idx, "char": ch, "reason": "mismatch"})
                continue
            stack.pop()

    for item in stack:
        issues.append({"index": int(item["index"]), "char": str(item["char"]), "reason": "unclosed"})

    return issues


class KoreanChecker:
    _shared_tool = None
    _shared_backend = None

    def __init__(self, use_spacing: bool = True) -> None:
        self.kiwi = Kiwi()
        self.spacing = Spacing() if use_spacing and Spacing is not None else None
        self._ensure_java_runtime()

        if KoreanChecker._shared_backend is not None:
            self.tool = KoreanChecker._shared_tool
            self.backend = str(KoreanChecker._shared_backend)
            return

        self.tool = None
        self.backend = "regex-only"
        try:
            self.tool = language_tool_python.LanguageTool("ko-KR")
            self.backend = "language-tool"
        except Exception:
            self.tool = None

        KoreanChecker._shared_tool = self.tool
        KoreanChecker._shared_backend = self.backend

    def _ensure_java_runtime(self) -> None:
        if os.environ.get("JAVA_HOME"):
            return

        candidates = [
            Path("C:/Program Files/Eclipse Adoptium/jdk-17.0.19.10-hotspot"),
            Path("C:/Program Files/Eclipse Adoptium/jdk-17"),
            Path("C:/Program Files/Java/jdk-17"),
        ]
        
        java_bases = [
            Path("C:/Program Files/Java"),
            Path("C:/Program Files/Microsoft"),
        ]
        for base in java_bases:
            if base.exists():
                for p in base.iterdir():
                    if p.is_dir() and (p.name.startswith("jdk-") or p.name == "latest"):
                        candidates.append(p)

        java_home = next((p for p in candidates if p.exists()), None)
        if not java_home:
            return

        os.environ["JAVA_HOME"] = str(java_home)
        java_bin = str(java_home / "bin")
        current_path = os.environ.get("PATH", "")
        if java_bin not in current_path:
            os.environ["PATH"] = java_bin + os.pathsep + current_path

    def _classify_category(self, category_id: str, message: str) -> str:
        text = f"{category_id} {message}".lower()
        if any(token in text for token in ["space", "whitespace", "띄어쓰기", "공백"]):
            return "spacing"
        if any(token in text for token in ["punct", "comma", "quote", "구두점", "괄호", "인용"]):
            return "punctuation"
        if any(
            token in text
            for token in ["spell", "typo", "orthograph", "맞춤법", "오탈자", "철자", "capitalization", "casing"]
        ):
            return "spelling"
        return "spelling"

    def check(self, sentence: str) -> List[Dict[str, object]]:
        issues: List[Dict[str, object]] = []

        if MULTI_SPACE_RE.search(sentence):
            issues.append(
                {
                    "lang": "ko",
                    "category": "spacing",
                    "message": "연속된 공백이 있습니다.",
                    "suggestions": ["불필요한 공백을 1칸으로 줄이세요."],
                    "span": {"offset": 0, "length": len(sentence)},
                }
            )

        mp = MULTI_PUNCT_RE.search(sentence)
        if mp:
            issues.append(
                {
                    "lang": "ko",
                    "category": "punctuation",
                    "message": "반복 구두점이 감지되었습니다.",
                    "suggestions": [f"'{mp.group(0)}'를 1개 구두점으로 정리하세요."],
                    "span": {"offset": mp.start(), "length": len(mp.group(0))},
                }
            )

        if _is_unbalanced(PAREN_UNBALANCED_RE, sentence):
            issues.append(
                {
                    "lang": "ko",
                    "category": "punctuation",
                    "message": "괄호의 짝이 맞지 않을 수 있습니다.",
                    "suggestions": ["괄호 열고 닫힘을 확인하세요."],
                    "span": {"offset": 0, "length": len(sentence)},
                }
            )

        quote_issues = _find_quote_pair_issues(sentence)
        if quote_issues:
            first_issue = quote_issues[0]
            issue_index = int(first_issue["index"])
            issues.append(
                {
                    "lang": "ko",
                    "category": "punctuation",
                    "message": "인용부호의 여닫이 짝이 맞지 않거나 종류가 섞여 있습니다.",
                    "suggestions": [
                        "인용부호를 같은 종류의 여는/닫는 쌍으로 맞추세요. (예: ‘…’, “…”, '…', \"…\")"
                    ],
                    "span": {"offset": issue_index, "length": 1},
                }
            )

        if self.spacing is not None:
            corrected = self.spacing(sentence)
            corrected = clean_spacing_with_kiwi(sentence, corrected, self.kiwi)
            if corrected != sentence:
                issues.append(
                    {
                        "lang": "ko",
                        "category": "spacing",
                        "message": "띄어쓰기 보정 후보가 있습니다.",
                        "suggestions": [corrected],
                        "span": {"offset": 0, "length": len(sentence)},
                    }
                )

        if self.tool is not None:
            try:
                matches = self.tool.check(sentence)
            except Exception:
                matches = []

            for m in matches:
                category_id = str(getattr(m.category, "id", "") or "")
                message = str(getattr(m, "message", "") or "")
                category = self._classify_category(category_id, message)

                offset = int(getattr(m, "offset", 0) or 0)
                raw_length = getattr(m, "errorLength", None)
                if raw_length is None:
                    raw_length = getattr(m, "error_length", None)
                if raw_length is None:
                    matched = str(getattr(m, "matchedText", "") or getattr(m, "matched_text", ""))
                    raw_length = len(matched) if matched else 1
                length = max(1, int(raw_length))

                issues.append(
                    {
                        "lang": "ko",
                        "category": category,
                        "message": message if message else "한글 문장 점검 항목이 발견되었습니다.",
                        "suggestions": list(getattr(m, "replacements", [])[:5]),
                        "span": {"offset": offset, "length": length},
                    }
                )

        try:
            tokens = self.kiwi.tokenize(sentence)
            for i in range(len(tokens)):
                # 1. 조사(J) 앞 불필요한 공백 검출 (예: "사과 가" -> "사과가")
                if i > 0:
                    t2 = tokens[i]
                    if t2.tag.startswith("J") and t2.start > 0 and sentence[t2.start - 1] == " ":
                        t1 = tokens[i - 1]
                        word_start = t1.start
                        while word_start > 0 and sentence[word_start - 1] != " ":
                            word_start -= 1
                        
                        original_segment = sentence[word_start : t2.start + t2.len]
                        suggested = original_segment.replace(" ", "")
                        
                        if not any(iss["span"]["offset"] == word_start and iss["span"]["length"] == len(original_segment) for iss in issues):
                            issues.append({
                                "lang": "ko",
                                "category": "spacing",
                                "message": f"조사 '{t2.form}'는 앞 말에 붙여 써야 합니다.",
                                "suggestions": [suggested],
                                "span": {"offset": word_start, "length": len(original_segment)},
                            })

                # 2. 의존 명사(NNB) 앞 띄어쓰기 누락 검출 (예: "할수" -> "할 수")
                if i < len(tokens) - 1:
                    t1 = tokens[i]
                    t2 = tokens[i + 1]
                    if t1.tag == "ETM" and t2.tag == "NNB" and t2.form in {"수", "것", "지", "데", "바", "줄", "중", "적", "겸", "등", "뿐"}:
                        if t2.start > 0 and sentence[t2.start - 1] != " ":
                            word_start = t1.start
                            while word_start > 0 and sentence[word_start - 1] != " ":
                                word_start -= 1
                            
                            original_segment = sentence[word_start : t2.start + t2.len]
                            offset_diff = t2.start - word_start
                            suggested = original_segment[:offset_diff] + " " + original_segment[offset_diff:]
                            
                            if not any(iss["span"]["offset"] == word_start and iss["span"]["length"] == len(original_segment) for iss in issues):
                                issues.append({
                                    "lang": "ko",
                                    "category": "spacing",
                                    "message": f"의존 명사 '{t2.form}' 앞은 띄어 써야 합니다.",
                                    "suggestions": [suggested],
                                    "span": {"offset": word_start, "length": len(original_segment)},
                                })
        except Exception:
            pass

        return issues
