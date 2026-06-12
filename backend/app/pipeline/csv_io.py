"""CSV input/output for the pipeline.

Input: delimiter is auto-detected (comma or semicolon), so both the judges' test
CSV and German-Excel exports parse without manual steps.
Output: clean semicolon-delimited CSV (the convention German-locale Excel and
the provided reference files use; addresses contain commas, so ';' also keeps
columns visually intact), header + one row per query — machine-readable for
scoring (no comment lines; uncertainty lives in confidence/no_match_reason).
"""

import csv
from datetime import datetime
from pathlib import Path

from app.pipeline.models import ExtractionResult, QueryRow

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
QUERIES_CSV = DATA_DIR / "queries.csv"
OUTPUT_DIR = DATA_DIR / "output"

RESULT_COLUMNS = list(ExtractionResult.model_fields)
OUTPUT_DELIMITER = ";"


def _detect_delimiter(header_line: str) -> str:
    try:
        return csv.Sniffer().sniff(header_line, delimiters=",;").delimiter
    except csv.Error:
        return ";" if header_line.count(";") >= header_line.count(",") else ","


def read_queries_text(text: str) -> list[QueryRow]:
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        return []
    delimiter = _detect_delimiter(lines[0])
    rows = csv.DictReader(lines, delimiter=delimiter)
    return [
        QueryRow(**{(k or "").strip(): (v or "").strip() for k, v in row.items()})
        for row in rows
    ]


def read_queries(path: Path = QUERIES_CSV) -> list[QueryRow]:
    # utf-8-sig strips the BOM that Excel prepends to exported CSVs.
    return read_queries_text(path.read_text(encoding="utf-8-sig"))


def write_results(results: list[ExtractionResult], output_dir: Path = OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"results_{datetime.now():%Y%m%d_%H%M%S}.csv"

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS, delimiter=OUTPUT_DELIMITER)
        writer.writeheader()
        for r in results:
            writer.writerow(r.model_dump())
    return path
