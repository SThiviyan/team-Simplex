"""Per-country ranked MCP server lists ("Listen an Quellen").

TODO: replace this CSV mock with the real DB of viable MCPs per country.
"""

import csv
from pathlib import Path

from app.pipeline.models import McpServerEntry

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
MCP_DIR = DATA_DIR / "mcp_servers"

# Country code -> CSV file. Everything not listed falls into the extra_eu bucket.
_COUNTRY_FILES = {
    "DE": "de.csv",
    "UK": "uk.csv",
    "GB": "uk.csv",
}
_FALLBACK_FILE = "extra_eu.csv"


def _read_csv_skipping_comments(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    return list(csv.DictReader(lines, delimiter=";"))


def get_mcp_servers(country_code: str) -> list[McpServerEntry]:
    """Return the ranked MCP server list for a country code, best rank first."""
    filename = _COUNTRY_FILES.get(country_code.strip().upper(), _FALLBACK_FILE)
    path = MCP_DIR / filename
    if not path.is_file():
        return []
    entries = [McpServerEntry(**row) for row in _read_csv_skipping_comments(path)]
    entries.sort(key=lambda e: e.rank)
    return entries
