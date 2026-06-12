"""CSV input/output for the pipeline (semicolon-delimited, German Excel convention)."""

import csv
from datetime import datetime
from pathlib import Path

from app.pipeline.models import ExtractionResult, QueryRow

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
QUERIES_CSV = DATA_DIR / "queries.csv"
OUTPUT_DIR = DATA_DIR / "output"

RESULT_COLUMNS = list(ExtractionResult.model_fields)

# Below this confidence (and on explicit no-match) the row gets a TODO comment
# in the output CSV marking it for manual review / the future recursive pass.
UNCERTAIN_BELOW = 0.8


def read_queries(path: Path = QUERIES_CSV) -> list[QueryRow]:
    with path.open(encoding="utf-8") as f:
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    return [QueryRow(**row) for row in csv.DictReader(lines, delimiter=";")]


def write_results(results: list[ExtractionResult], output_dir: Path = OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"results_{datetime.now():%Y%m%d_%H%M%S}.csv"

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS, delimiter=";")
        writer.writeheader()
        for r in results:
            if r.no_match_reason or r.confidence < UNCERTAIN_BELOW:
                # TODO: uncertain result — candidate for the recursive refinement
                # pass (Layer 1 'Recursive' arrow) / Layer 2 detailed query.
                f.write(
                    f"# TODO [{r.query_id}]: uncertain (confidence={r.confidence:.2f}"
                    f"{', no match: ' + r.no_match_reason if r.no_match_reason else ''}) — review or re-run\n"
                )
            writer.writerow(r.model_dump())
    return path
