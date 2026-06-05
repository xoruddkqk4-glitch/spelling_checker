from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List

import language_tool_python


class EnglishChecker:
    _shared_tool = None
    _shared_backend = None

    def __init__(self) -> None:
        self._ensure_java_runtime()
        if EnglishChecker._shared_backend is not None:
            self.tool = EnglishChecker._shared_tool
            self.backend = str(EnglishChecker._shared_backend)
            return

        self.tool = None
        self.backend = "regex-fallback"
        try:
            self.tool = language_tool_python.LanguageTool("en-US")
            self.backend = "language-tool"
        except Exception:
            self.tool = None

        EnglishChecker._shared_tool = self.tool
        EnglishChecker._shared_backend = self.backend

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

    def _regex_fallback_check(self, sentence: str) -> List[Dict[str, object]]:
        issues: List[Dict[str, object]] = []

        doubled_space = re.search(r"[A-Za-z0-9]\s{2,}[A-Za-z0-9]", sentence)
        if doubled_space:
            issues.append(
                {
                    "lang": "en",
                    "category": "spacing",
                    "message": "Multiple spaces detected in English sentence.",
                    "suggestions": ["Reduce consecutive spaces to one space."],
                    "span": {"offset": doubled_space.start(), "length": len(doubled_space.group(0))},
                }
            )

        repeated_punct = re.search(r"([.!?]{2,}|,{2,}|;{2,}|:{2,})", sentence)
        if repeated_punct:
            issues.append(
                {
                    "lang": "en",
                    "category": "punctuation",
                    "message": "Repeated punctuation detected.",
                    "suggestions": [f"Use a single punctuation mark instead of '{repeated_punct.group(0)}'."],
                    "span": {"offset": repeated_punct.start(), "length": len(repeated_punct.group(0))},
                }
            )

        no_terminal_punct = re.search(r"[A-Za-z]", sentence) and not re.search(r"[.!?]$", sentence.strip())
        if no_terminal_punct:
            issues.append(
                {
                    "lang": "en",
                    "category": "punctuation",
                    "message": "Sentence may be missing terminal punctuation.",
                    "suggestions": ["Consider adding '.', '?', or '!' at the end."],
                    "span": {"offset": max(0, len(sentence) - 1), "length": 1},
                }
            )

        return issues

    def check(self, sentence: str) -> List[Dict[str, object]]:
        if self.tool is None:
            return self._regex_fallback_check(sentence)

        issues: List[Dict[str, object]] = []
        matches = self.tool.check(sentence)
        for m in matches:
            category_id = str(getattr(m.category, "id", "")).lower()
            rule_id = str(getattr(m, "ruleId", "")).lower()
            message = str(getattr(m, "message", "")).lower()

            text = f"{category_id} {rule_id} {message}"
            allowed_keywords = [
                "punctuation",
                "comma",
                "quote",
                "apostrophe",
                "whitespace",
                "space",
                "spelling",
                "misspell",
                "typo",
                "typo",
                "orthography",
                "capitalization",
                "casing",
                "hyphen",
            ]
            if not any(keyword in text for keyword in allowed_keywords):
                continue

            offset = int(getattr(m, "offset", 0) or 0)
            # language_tool_python 버전에 따라 errorLength가 없을 수 있어 안전하게 보정
            raw_length = getattr(m, "errorLength", None)
            if raw_length is None:
                raw_length = getattr(m, "error_length", None)
            if raw_length is None:
                matched = str(getattr(m, "matchedText", "") or getattr(m, "matched_text", ""))
                raw_length = len(matched) if matched else 1
            length = max(1, int(raw_length))

            issues.append(
                {
                    "lang": "en",
                    "category": getattr(m.category, "id", "spelling"),
                    "message": m.message,
                    "suggestions": m.replacements[:5],
                    "span": {"offset": offset, "length": length},
                }
            )
        return issues
