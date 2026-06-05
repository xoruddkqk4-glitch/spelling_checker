# 01spelling_checker

로컬 PC에서 실행하는 Chainlit 기반 **한/영 맞춤법·띄어쓰기·구두점·철자** 검사 앱입니다.

- 지원 업로드: `.hwpx`, `.pdf`, `.docx`, `.txt` 및 대화창 직접 텍스트 복사/붙여넣기
- 외부 API 없이 로컬에서 문서 파싱·검사·저장
- 시험지 특성에 맞춰 **문항 번호(`1. ` 형식)** 기준으로 결과 표시

> 상세 설계·구현 과정은 [`.chats/cursor_spelling_and_grammar_checker_pro.md`](.chats/cursor_spelling_and_grammar_checker_pro.md) 대화 기록을 참고하세요.

## 검사 범위

**포함**

- 맞춤법 / 철자 (`spelling`, `orthography`)
- 띄어쓰기 (`spacing`)
- 구두점 (`punctuation`)

**제외**

- 내용·논리·문법 의미 검사
- 문체·스타일·문법 구조 평가

파이프라인에서 위 범위만 통과하도록 필터링합니다.

## 설치

### 요구 사항

- Python 3.10+
- Java 17+ (LanguageTool 로컬 엔진용, 영어·한글 검사 정확도에 권장, 최신 **JDK 26.0.1** 완벽 호환)
- Windows / macOS / Linux

Java가 없으면 영어 검사는 정규식 fallback으로 동작하며, 철자·대소문자 검출이 크게 약해집니다.  
Java는 venv 안이 아니라 **시스템에 설치**되며, 앱은 최신 **JDK 26**을 포함한 윈도우 설치 경로를 **동적으로 자동 탐색(Dynamic Auto-Scanning)**합니다.

### 패키지 설치

```bash
cd 01spelling_checker
python -m venv .venv

# Windows
.\.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

LanguageTool은 **최초 1회** 약 260MB 리소스를 다운로드한 뒤 캐시를 재사용합니다. 이후 요청부터는 훨씬 빨라집니다.

## 실행

```bash
chainlit run app.py
```

브라우저에서 앱이 열리면 **파일 업로드**와 **대화창 직접 텍스트 붙여넣기** 모두 즉각 수신 가능합니다.  
별도 채팅 메시지 입력 없이 파일만 올려도 분석이 시작되며, 텍스트 입력 시에는 로컬 `direct_input.txt` 임시 파일로 자동 래핑하여 분석합니다.

## 사용 방법

1. `.hwpx`, `.pdf`, `.docx`, `.txt` 파일 업로드 또는 대화창 직접 텍스트 복사/붙여넣기 (최대 50MB, 타임아웃 180초)
2. 채팅창에서 단계별 진행 확인
   - 업로드 확인 (파일명, 확장자, 크기)
   - `inputs/`에 검사 직전 텍스트 JSON 저장
   - 문서 파싱 → 문장 분리 → 문항 인식 → 맞춤법 점검
   - `문서 분석 진행 중` 스텝(스피너) 표시
3. 결과 확인
   - **문항별 요약**: 인식된 모든 문항에 대해 `오류 N건` / `오류 없음`
   - **상세 내역**: 오류가 있는 항목만 (채팅 최대 120건, 초과분은 JSON/CSV 참고)
   - **PDF 다운로드**: 오류 구간 노란 형광 강조 포함 (임시 파일)
   - **페이지 필터 버튼**: 보조 기능으로 페이지별 결과 조회

### 채팅 출력 형식

- **가시성이 뛰어난 헤더**: `### **N. M번 문항**` (H3 크기 및 굵게) 또는 `(K페이지)`로 시인성 증대
- **이모지 기반의 굵은 글머리표**:
  - `* 📝 **오류 문장:**` (오류 구간은 노란 배경 HTML 강조)
  - `* 📍 **오류 구간:**`
  - `* 💡 **오류 이유:**` (교정 근거 및 이유 해설 추가)
  - `* 🛠️ **수정 제안:**` (두 텍스트의 달라진 부분만 **빨간색 형광펜**으로 정교하게 하이라이트 강조)
- 이전/다음 문장, 위치 정보(offset)는 채팅에 표시하지 않음

## 폴더 구조

```
01spelling_checker/
├── app.py                 # Chainlit UI, PDF 생성, 진행 메시지 (비동기 스레드 풀 오프로딩 적용)
├── src/
│   ├── pipeline.py        # 파이프라인, 저장, 문항 집계
│   ├── extractors/        # hwpx / pdf / docx / txt 텍스트 추출
│   ├── nlp/               # 문장 분리, 언어 감지
│   └── checkers/          # 한글·영어 검사기
├── inputs/                # 검사 직전 텍스트 JSON (디버깅·추적용)
├── outputs/               # 오류 항목만 JSON/CSV 저장
├── tests/smoke_test.py    # CLI 스모크 테스트
└── .chainlit/config.toml  # HTML 렌더링 등 UI 설정
```

### `inputs/` — 검사 직전 텍스트

업로드 원본 사본이 아니라, **검사기로 넘어가기 직전 텍스트**를 JSON으로 저장합니다.

파일명: `<원본파일명>_<타임스탬프>_input_text.json`

| 필드 | 설명 |
|------|------|
| `원본파일명` | 업로드된 파일 이름 |
| `페이지별원문` | 추출기가 읽은 페이지(또는 가상 페이지) 단위 원문 |
| `검사용문장` | 문장 분리 후 문항번호·언어 포함 |
| `검사용최종텍스트` | 검사기에 실제 전달되는 문장 합본 |

문항 인식이 안 될 때 이 파일에서 `1.` / `1. ` 형태가 어떻게 분리됐는지 확인할 수 있습니다.

### `outputs/` — 오류만 저장

| 파일 | 내용 |
|------|------|
| `<파일명>_findings.json` | 오류 항목 JSON |
| `<파일명>_findings.csv` | 오류 항목 CSV (한국어 컬럼명) |

저장 필드 예: `문항`, `페이지`, `언어`, `오류유형`, `설명`, `문제문장`, `수정제안`, `위치정보` 등

채팅창은 **모든 문항 요약**을 보여주지만, 저장 파일에는 **오류가 있는 항목만** 기록됩니다.

### PDF 결과 (임시)

- `reportlab`으로 생성, 시스템 **임시 폴더**에만 저장 및 세션 종료 시 자동 삭제
- 프로젝트 폴더·로컬 디스크에 영구 저장하지 않음
- 오류 구간에 노란 형광색 하이라이트 적용
- 수정 제안에 대해 달라진 글자만 **연한 빨간색 형광펜 사각형**으로 감싸 렌더링
- **글자 겹침 버그 조치**: 폰트 문자폭 호환을 고려해 `•` 대신 `- ` 기호로 교체하여 한글 겹침 문제 해결
- **자동 줄바꿈(Word Wrap)**: 긴 문장이나 긴 수정 제안이 잘리지 않고 자동으로 다음 행으로 개행되도록 알고리즘 적용

## 텍스트 추출

### 파일 형식 분기

| 확장자 | 방식 | 비고 |
|--------|------|------|
| `.hwpx` | `zipfile` + `xml.etree` + `re` | 문단 + **표 셀**(`tc`, `cell`, `td`) |
| `.pdf` | PyMuPDF | `dict`(블록/라인) + `words` + `text` fallback |
| `.docx` | `python-docx` + XML 직접 파싱 | 문단 + **텍스트 상자** + **표 셀** |
| `.txt` | UTF-8 / CP949 하이브리드 파서 | 순수 본문 텍스트 2000자 단위 가상 페이지 분할 |

- Chainlit 업로드 시 임시 경로가 `.bin`으로 저장될 수 있어, 포맷 판별은 **원본 파일명 확장자**를 우선 사용합니다.
- `docx` / `hwpx` / `txt`의 페이지는 문자 수 기반 **가상 페이지**입니다.
- `pdf`는 실제 페이지 번호를 사용합니다.

### DOCX 추출 상세

- `python-docx` 문단 순회
- XML에서 `w:txbxContent`(워드 텍스트 상자), `a:txBody`(DrawingML 도형 텍스트)
- `tables → rows → cells` 표 셀 텍스트
- 정규화 후 중복 제거

### PDF 추출 상세

- 블록/단어 단위(`dict`, `words`)로 상자·블록 텍스트 누락 최소화
- 텍스트 레이어가 있는 PDF는 대부분 읽을 수 있음
- **스캔 PDF·이미지 기반 상자**는 OCR 없이는 추출 불가

## 언어·검사기 분기

문장별 언어 감지 후 아래 검사기를 순차 실행합니다.

| 언어 | 검사기 | 주요 도구 |
|------|--------|-----------|
| 한글 | `KoreanChecker` | `kss`, 규칙 기반, `LanguageTool(ko-KR)`, `kiwipiepy` |
| 영어 | `EnglishChecker` | `nltk`, `LanguageTool(en-US)` (Java 없으면 regex fallback) |
| 혼합 | 양쪽 순차 실행 | — |

### 한글 검사

- `LanguageTool(ko-KR)`: 맞춤법·띄어쓰기·구두점
- 규칙 기반: 연속 공백, 반복 구두점, 괄호 불균형
- 인용부호: ASCII(`'`, `"`) + 스마트(`‘ ’`, `“ ”`) **스택 기반 쌍 검증**
- `pykospacing` 기반 띄어쓰기 보정 엔진 활성화
- **오탐 사후 필터링 (`clean_spacing_with_kiwi`)**: `pykospacing`이 단어 중간을 찢어놓는 오탐(예: `기본적` -> `기 본 적`, `시간적` -> `시간 적`)이 발생하면, Kiwi 형태소 분석 결과를 대조하여 형태소 내부나 어근/접사/조사 결합 경계의 공백을 자동으로 삭제하여 원래대로 복원

### 영어 검사

- `LanguageTool(en-US)`: 철자, 대소문자, 띄어쓰기, 구두점
- 프로세스 전역에서 LanguageTool 인스턴스 **재사용** (요청마다 재기동 방지)
- `errorLength` / `error_length` 버전 차이 호환 처리

## 문항 인식

시험지 기본 규칙: **`숫자 + 마침표 + 공백 1개`** (예: `1. `)

| 패턴 | 동작 |
|------|------|
| `1. 본문...` | 해당 문장부터 해당 번호 문항 |
| `1.` (단독 문장) | 번호만 저장, **다음 문장부터** 해당 문항 |

문장 분리(`kss` / `nltk`) 때문에 번호가 `1.` 단독으로 떨어지는 경우에도 안정적으로 이어 붙입니다.  
문항 패턴이 없으면 페이지 기준으로 fallback합니다.

## 스모크 테스트

CLI에서 파이프라인만 빠르게 확인할 때:

```bash
python tests/smoke_test.py "샘플파일경로.pdf"
python tests/smoke_test.py "샘플파일경로.docx" --output-dir outputs
```

## 제약·알려진 한계

| 항목 | 내용 |
|------|------|
| 한글 맞춤법 | 영어만큼 완전한 단일 오프라인 엔진은 없음. LanguageTool + 규칙 + PyKoSpacing + Kiwi 조합 |
| 스캔 PDF | OCR 미지원 |
| 대용량 결과 | 채팅 상세 120건 제한, 메시지 자동 분할(약 7,000자) |
| LanguageTool 초기화 | 첫 실행 1~2분 소요 가능 (비동기 스레드 풀 격리 패치 적용으로 웹소켓 끊김/UI 멈춤 방지 완료) |
| PDF 결과 | **가시성 패치 완료** (글자 겹침 해결 및 아주 긴 문장의 자동 줄바꿈 및 페이지 넘김 완벽 지원) |
| 문항 규칙 | `1. ` 외 형식(`2)`, `【3】` 등)은 미지원 |

## 의존성

```
chainlit, pymupdf, python-docx, kss, nltk,
language-tool-python, kiwipiepy, reportlab, pykospacing
```

## 참고

- 구현·트러블슈팅 기록: [`.chats/cursor_spelling_and_grammar_checker_pro.md`](.chats/cursor_spelling_and_grammar_checker_pro.md)
- Chainlit HTML 강조: `.chainlit/config.toml`의 `unsafe_allow_html = true`
