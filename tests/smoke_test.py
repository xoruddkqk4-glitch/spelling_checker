from __future__ import annotations

import argparse
from pathlib import Path

from src.pipeline import run_document_check, save_findings_csv, save_findings_json


def main() -> None:
    parser = argparse.ArgumentParser(
        description="샘플 문서(.hwpx/.pdf/.docx) 오프라인 스모크 테스트"
    )
    parser.add_argument("input_file", help="검사할 샘플 파일 경로")
    parser.add_argument(
        "--output-dir",
        default="outputs",
        help="결과(JSON/CSV) 저장 폴더 (기본: outputs)",
    )
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {input_path}")

    findings = run_document_check(str(input_path))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{input_path.stem}_findings.json"
    csv_path = output_dir / f"{input_path.stem}_findings.csv"

    save_findings_json(findings, str(json_path))
    save_findings_csv(findings, str(csv_path))

    print(f"검사 완료: 총 {len(findings)}건")
    print(f"JSON 저장: {json_path}")
    print(f"CSV 저장: {csv_path}")


if __name__ == "__main__":
    main()
