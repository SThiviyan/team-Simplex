"""Run the pipeline end-to-end from the command line — no HTTP, no request timeouts.

This is the robust path for the judges' test CSV:

    cd backend && uv run python -m app.pipeline.cli path/to/test.csv -o results.csv

Requires ANTHROPIC_API_KEY in the environment (or PIPELINE_MOCK=true for a dry run).
Input delimiter (comma/semicolon) is auto-detected; output is comma-delimited.
"""

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from app.pipeline.csv_io import read_queries
from app.pipeline.runner import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="Input CSV (query_id, name, jurisdiction)")
    parser.add_argument("-o", "--output", type=Path, help="Where to write the result CSV")
    parser.add_argument("--limit", type=int, help="Only process the first N rows")
    args = parser.parse_args()

    queries = read_queries(args.input)
    if not queries:
        sys.exit(f"No rows parsed from {args.input}")
    print(f"{len(queries)} queries loaded from {args.input}", file=sys.stderr)

    summary = asyncio.run(run_pipeline(queries=queries, limit=args.limit))

    output = Path(summary.output_csv)
    if args.output:
        shutil.copy(output, args.output)
        output = args.output
    print(f"{summary.rows_processed} rows -> {output}", file=sys.stderr)
    print(output)


if __name__ == "__main__":
    main()
