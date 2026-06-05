from __future__ import annotations

import re
import json
import requests
from typing import Dict, List, Optional


class GemmaChecker:
    def __init__(self, api_url: str = "http://localhost:1234/v1", model_name: str = "google/gemma-4-12b") -> None:
        self.api_url = api_url.rstrip("/")
        self.model_name = model_name

    def is_available(self) -> bool:
        """LM Studio 서버가 실행 중이고 접근 가능한지 빠르게 확인합니다."""
        try:
            # GET /v1/models 엔드포인트를 호출하여 서버 상태 확인
            response = requests.get(f"{self.api_url}/models", timeout=2.0)
            return response.status_code == 200
        except Exception:
            return False

    def _parse_llm_json(self, response_text: str) -> List[Dict[str, object]]:
        """LLM 응답 텍스트에서 JSON 배열을 안전하게 추출하여 파싱합니다."""
        cleaned = response_text.strip()
        
        # 1. ```json ... ``` 혹은 ``` ... ``` 블록 추출 시도
        match_code = re.search(r'```(?:json)?\s*(.*?)\s*```', cleaned, re.DOTALL)
        if match_code:
            cleaned = match_code.group(1).strip()
            
        # 2. 대괄호 [로 시작해서 ]로 끝나는 부분만 찾아내기 시도 (줄글 설명 방지)
        match_bracket = re.search(r'\[\s*\{.*\}\s*\]', cleaned, re.DOTALL)
        if match_bracket:
            cleaned = match_bracket.group(0)

        try:
            data = json.loads(cleaned)
            if isinstance(data, list):
                return data
        except Exception:
            # 파싱 오류 발생 시 예외 기록 후 빈 리스트 반환
            pass
        return []

    def _map_spans(self, raw_issues: List[Dict[str, object]], sentence: str) -> List[Dict[str, object]]:
        """LLM이 반환한 원본 오탈자 구간 문자열("original")을 입력 문장에서 찾아 offset과 length를 채워 넣습니다."""
        issues: List[Dict[str, object]] = []
        for item in raw_issues:
            if not isinstance(item, dict):
                continue

            orig = str(item.get("original", ""))
            corr = str(item.get("corrected", ""))
            cat = str(item.get("category", "spelling")).strip().lower()
            msg = str(item.get("message", ""))
            lang = str(item.get("lang", "ko")).strip().lower()

            if not orig or not corr or orig == corr:
                continue

            # 카테고리 규격화
            if cat not in ("spelling", "spacing", "punctuation"):
                cat = "spelling"

            # 원본 문장에서의 offset 및 length 탐색
            offset = sentence.find(orig)
            if offset == -1:
                # 대소문자가 있는 영어 문맥인 경우 대소문자 구분 없이 재탐색
                if lang == "en":
                    match_ci = re.search(re.escape(orig), sentence, re.IGNORECASE)
                    if match_ci:
                        offset = match_ci.start()
                        length = len(match_ci.group(0))
                    else:
                        continue
                else:
                    continue
            else:
                length = len(orig)

            issues.append({
                "lang": lang,
                "category": cat,
                "message": msg if msg else f"'{orig}'를 '{corr}'로 수정",
                "suggestions": [corr],
                "span": {"offset": offset, "length": length}
            })
        return issues

    def check(self, sentence: str, prev_sentence: str = "", next_sentence: str = "") -> List[Dict[str, object]]:
        """단일 문장을 로컬 LLM에 전송하여 맞춤법/띄어쓰기/구두점 검사를 수행합니다."""
        if not sentence.strip():
            return []

        system_prompt = (
            "당신은 한국어 및 영어 맞춤법, 띄어쓰기, 구두점, 철자 오류를 교정하는 최고 전문가입니다.\n"
            "사용자가 제공한 문장과 문맥(이전/다음 문장)을 분석하여, 오직 해당 문장의 오류만 교정하고 아래 지정된 JSON 배열 형식으로만 답변하세요.\n\n"
            "[출력 형식]\n"
            "반드시 JSON 배열 형태로만 출력해야 합니다. 오류가 전혀 없다면 빈 배열 `[]`을 출력하십시오.\n"
            "각 오류 객체는 다음 필드를 반드시 정확히 가져야 합니다:\n"
            '- "original": 오류가 있는 부분의 원본 문자열 (원본 문장과 대소문자, 띄어쓰기까지 글자 그대로 완벽히 일치해야 함)\n'
            '- "corrected": 올바르게 수정한 문자열\n'
            '- "category": "spelling" (맞춤법/철자 오류), "spacing" (띄어쓰기 오류), "punctuation" (구두점 오류) 중 하나\n'
            '- "message": 교정 사유 및 해설 (한국어로 작성)\n'
            '- "lang": 해당 단어/어절의 언어 ("ko" 또는 "en")\n\n'
            "[주의사항]\n"
            "1. JSON 응답 외에 어떠한 인사말, 설명, 마크다운 코드 블록(```json 등)도 절대 포함하지 마십시오. 오직 순수 JSON 배열만 출력해야 합니다.\n"
            "2. 문장 전체를 하나로 묶어 교정하기보다, 교정이 필요한 단어/어절 단위로 세분화하여 개별 오류 객체로 반환하십시오.\n"
            "3. \"original\" 필드에 들어가는 문자열은 분석 대상 문장 내에 완벽하게 존재하는 하위 문자열이어야 합니다. 임의로 수정하여 존재하지 않는 단어를 넣지 마십시오.\n"
            "4. 문맥상 명백한 오류가 아닌 일반적인 어휘나 표현(예: 고유 명사, 전문 용어 등)은 교정하지 마십시오."
        )

        user_prompt = (
            f"[문맥 정보]\n"
            f"이전 문장: {prev_sentence}\n"
            f"다음 문장: {next_sentence}\n\n"
            f"[분석할 문장]\n"
            f"{sentence}"
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 1024
        }

        try:
            response = requests.post(
                f"{self.api_url}/chat/completions",
                json=payload,
                timeout=30.0
            )
            if response.status_code != 200:
                return []

            result = response.json()
            response_text = result["choices"][0]["message"]["content"]
            raw_issues = self._parse_llm_json(response_text)
            return self._map_spans(raw_issues, sentence)
        except Exception:
            return []

    def check_with_local_suggestions(
        self,
        sentence: str,
        local_issues: List[Dict[str, object]],
        prev_sentence: str = "",
        next_sentence: str = ""
    ) -> List[Dict[str, object]]:
        """단일 문장용 하이브리드 검증 메소드"""
        if not sentence.strip() or not local_issues:
            return []

        formatted_suggestions = []
        for iss in local_issues:
            span = iss.get("span", {}) or {}
            offset = span.get("offset", 0)
            length = span.get("length", 1)
            original_segment = sentence[offset : offset + length]
            suggestion_text = iss.get("suggestions", [""])[0] if iss.get("suggestions") else ""
            
            formatted_suggestions.append({
                "original": original_segment,
                "corrected": suggestion_text,
                "category": iss.get("category", "spelling"),
                "message": iss.get("message", "")
            })

        system_prompt = (
            "당신은 한국어 및 영어 맞춤법 교정 전문가입니다.\n"
            "사용자가 제공한 문장과 기존 로컬 규칙 기반 검사기가 탐지한 맞춤법 오류 후보 목록을 분석하십시오.\n"
            "그 후, 로컬 검사기가 감지한 내용 중 잘못 분석된 오류(오탐)를 제외(필터링)하고, 실제 유효한 오류에 대해서만 더 정확한 교정 결과와 명확한 사유를 포함하여 아래 지정된 JSON 배열 형식으로만 답변하세요.\n\n"
            "[출력 형식]\n"
            "반드시 JSON 배열 형태로만 출력해야 합니다. 오류가 전혀 없거나 모든 후보군이 오탐인 경우 빈 배열 `[]`을 출력하십시오.\n"
            "각 오류 객체는 다음 필드를 반드시 정확히 가져야 합니다:\n"
            '- "original": 오류가 있는 부분의 원본 문자열 (원본 문장과 대소문자, 띄어쓰기까지 글자 그대로 완벽히 일치해야 함)\n'
            '- "corrected": 올바르게 수정한 문자열\n'
            '- "category": "spelling" (맞춤법/철자 오류), "spacing" (띄어쓰기 오류), "punctuation" (구두점 오류) 중 하나\n'
            '- "message": 교정 사유 및 해설 (한국어로 작성)\n'
            '- "lang": 해당 단어/어절의 언어 ("ko" 또는 "en")\n\n'
            "[주의사항]\n"
            "1. JSON 응답 외에 어떠한 인사말, 설명, 마크다운 코드 블록(```json 등)도 절대 포함하지 마십시오. 오직 순수 JSON 배열만 출력해야 합니다.\n"
            "2. 로컬 검사기가 감지한 목록에 누락된 명백한 오류가 대상 문장에 더 있다면 추가로 포함시킬 수 있습니다.\n"
            "3. \"original\" 필드에 들어가는 문자열은 분석 대상 문장 내에 완벽하게 존재하는 하위 문자열이어야 합니다. 임의로 변경하지 마십시오.\n"
            "4. 문맥상 명백한 오류가 아닌 일반적인 표현은 교정하지 마십시오."
        )

        user_prompt = (
            f"[문맥 정보]\n"
            f"이전 문장: {prev_sentence}\n"
            f"다음 문장: {next_sentence}\n\n"
            f"[분석할 문장]\n"
            f"{sentence}\n\n"
            f"[로컬 규칙 검사기 감지 후보군]\n"
            f"{json.dumps(formatted_suggestions, ensure_ascii=False)}"
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 1024
        }

        try:
            response = requests.post(
                f"{self.api_url}/chat/completions",
                json=payload,
                timeout=30.0
            )
            if response.status_code != 200:
                return local_issues

            result = response.json()
            response_text = result["choices"][0]["message"]["content"]
            raw_issues = self._parse_llm_json(response_text)
            return self._map_spans(raw_issues, sentence)
        except Exception:
            return local_issues

    def check_batch(self, sentences: List[str]) -> List[Dict[str, object]]:
        """여러 문장을 하나의 배치로 묶어서 로컬 LLM에 맞춤법 검사를 수행합니다."""
        if not sentences:
            return []

        system_prompt = (
            "당신은 한국어 및 영어 맞춤법, 띄어쓰기, 구두점, 철자 오류를 교정하는 최고 전문가입니다.\n"
            "사용자가 제공한 번호가 매겨진 문장 목록을 분석하여 오직 해당 문장들의 오류만 교정하고 아래 지정된 JSON 배열 형식으로만 답변하세요.\n\n"
            "[출력 형식]\n"
            "반드시 JSON 배열 형태로만 출력해야 합니다. 오류가 전혀 없다면 빈 배열 `[]`을 출력하십시오.\n"
            "각 오류 객체는 다음 필드를 반드시 정확히 가져야 합니다:\n"
            '- "sent_idx": 오류가 감지된 문장의 번호 (1부터 시작하는 정수, 예: 1)\n'
            '- "original": 오류가 있는 부분의 원본 문자열 (해당 문장 내와 대소문자, 띄어쓰기까지 글자 그대로 완벽히 일치해야 함)\n'
            '- "corrected": 올바르게 수정한 문자열\n'
            '- "category": "spelling" (맞춤법/철자 오류), "spacing" (띄어쓰기 오류), "punctuation" (구두점 오류) 중 하나\n'
            '- "message": 교정 사유 및 해설 (한국어로 작성)\n'
            '- "lang": 해당 단어/어절의 언어 ("ko" 또는 "en")\n\n'
            "[주의사항]\n"
            "1. JSON 응답 외에 어떠한 인사말, 설명, 마크다운 코드 블록(```json 등)도 절대 포함하지 마십시오. 오직 순수 JSON 배열만 출력해야 합니다.\n"
            "2. 각 문장에 대해 교정이 필요한 단어/어절 단위로 세분화하여 개별 오류 객체로 반환하십시오.\n"
            "3. \"original\" 필드에 들어가는 문자열은 번호에 매핑되는 해당 문장 내에 완벽하게 존재하는 하위 문자열이어야 합니다. 다른 문장의 단어와 섞지 마십시오.\n"
            "4. 문맥상 명백한 오류가 아닌 일반적인 표현은 교정하지 마십시오."
        )

        formatted_sentences = []
        for idx, sent in enumerate(sentences, start=1):
            formatted_sentences.append(f"{idx}. {sent}")
        
        joined_sentences = '\n'.join(formatted_sentences)
        user_prompt = (
            f"[검사할 문장 목록]\n"
            f"{joined_sentences}"
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 2048
        }

        try:
            response = requests.post(
                f"{self.api_url}/chat/completions",
                json=payload,
                timeout=45.0
            )
            if response.status_code != 200:
                return []

            result = response.json()
            response_text = result["choices"][0]["message"]["content"]
            raw_issues = self._parse_llm_json(response_text)
            
            issues: List[Dict[str, object]] = []
            for item in raw_issues:
                if not isinstance(item, dict):
                    continue
                
                try:
                    sent_idx = int(item.get("sent_idx", 0)) - 1
                except (ValueError, TypeError):
                    continue
                
                if 0 <= sent_idx < len(sentences):
                    sentence = sentences[sent_idx]
                    mapped_issues = self._map_spans([item], sentence)
                    for m_iss in mapped_issues:
                        m_iss["sent_idx"] = sent_idx
                        issues.append(m_iss)
            
            return issues
        except Exception:
            return []

    def check_batch_with_local_suggestions(
        self,
        batch_data: List[tuple[str, List[Dict[str, object]]]]
    ) -> List[Dict[str, object]]:
        """여러 문장과 그 문장들에 대해 로컬 검사기가 탐지한 후보 목록을 한꺼번에 전달받아 검증을 수행합니다."""
        if not batch_data:
            return []

        system_prompt = (
            "당신은 한국어 및 영어 맞춤법 교정 전문가입니다.\n"
            "사용자가 제공한 번호가 매겨진 문장들과 각 문장에 대해 기존 로컬 규칙 기반 검사기가 탐지한 맞춤법 오류 후보 목록을 분석하십시오.\n"
            "그 후, 로컬 검사기가 감지한 내용 중 잘못 분석된 오류(오탐)를 제외하고, 실제 유효한 오류에 대해서만 더 정확한 교정 결과와 명확한 사유를 포함하여 아래 지정된 JSON 배열 형식으로만 답변하세요.\n\n"
            "[출력 형식]\n"
            "반드시 JSON 배열 형태로만 출력해야 합니다. 오류가 전혀 없거나 모든 후보군이 오탐인 경우 빈 배열 `[]`을 출력하십시오.\n"
            "각 오류 객체는 다음 필드를 반드시 정확히 가져야 합니다:\n"
            '- "sent_idx": 오류가 감지된 문장의 번호 (1부터 시작하는 정수, 예: 1)\n'
            '- "original": 오류가 있는 부분의 원본 문자열 (원본 문장과 대소문자, 띄어쓰기까지 글자 그대로 완벽히 일치해야 함)\n'
            '- "corrected": 올바르게 수정한 문자열\n'
            '- "category": "spelling" (맞춤법/철자 오류), "spacing" (띄어쓰기 오류), "punctuation" (구두점 오류) 중 하나\n'
            '- "message": 교정 사유 및 해설 (한국어로 작성)\n'
            '- "lang": 해당 단어/어절의 언어 ("ko" 또는 "en")\n\n'
            "[주의사항]\n"
            "1. JSON 응답 외에 어떠한 인사말, 설명, 마크다운 코드 블록(```json 등)도 절대 포함하지 마십시오. 오직 순수 JSON 배열만 출력해야 합니다.\n"
            "2. 로컬 검사기가 감지한 목록에 누락된 명백한 오류가 대상 문장에 더 있다면 추가로 포함시킬 수 있습니다.\n"
            "3. \"original\" 필드에 들어가는 문자열은 번호에 매핑되는 해당 문장 내에 완벽하게 존재하는 하위 문자열이어야 합니다.\n"
            "4. 문맥상 명백한 오류가 아닌 일반적인 표현은 교정하지 마십시오."
        )

        formatted_items = []
        sentences = []
        original_local_issues_flat = []

        for idx, (sentence, local_issues) in enumerate(batch_data, start=1):
            sentences.append(sentence)
            
            formatted_suggestions = []
            for iss in local_issues:
                span = iss.get("span", {}) or {}
                offset = span.get("offset", 0)
                length = span.get("length", 1)
                original_segment = sentence[offset : offset + length]
                suggestion_text = iss.get("suggestions", [""])[0] if iss.get("suggestions") else ""
                
                formatted_suggestions.append({
                    "original": original_segment,
                    "corrected": suggestion_text,
                    "category": iss.get("category", "spelling"),
                    "message": iss.get("message", "")
                })
                
                backup_iss = dict(iss)
                backup_iss["sent_idx"] = idx - 1
                original_local_issues_flat.append(backup_iss)

            formatted_items.append(
                f"문장 {idx}: {sentence}\n"
                f"감지 후보: {json.dumps(formatted_suggestions, ensure_ascii=False)}"
            )

        joined_items = '\n\n'.join(formatted_items)
        user_prompt = (
            f"[분석할 문장 및 로컬 검사기 감지 후보 목록]\n\n"
            f"{joined_items}"
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 2048
        }

        try:
            response = requests.post(
                f"{self.api_url}/chat/completions",
                json=payload,
                timeout=45.0
            )
            if response.status_code != 200:
                return original_local_issues_flat

            result = response.json()
            response_text = result["choices"][0]["message"]["content"]
            raw_issues = self._parse_llm_json(response_text)
            
            issues: List[Dict[str, object]] = []
            for item in raw_issues:
                if not isinstance(item, dict):
                    continue
                
                try:
                    sent_idx = int(item.get("sent_idx", 0)) - 1
                except (ValueError, TypeError):
                    continue
                
                if 0 <= sent_idx < len(sentences):
                    sentence = sentences[sent_idx]
                    mapped_issues = self._map_spans([item], sentence)
                    for m_iss in mapped_issues:
                        m_iss["sent_idx"] = sent_idx
                        issues.append(m_iss)
            
            return issues
        except Exception:
            return original_local_issues_flat
