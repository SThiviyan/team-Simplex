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
    "US": "us.csv", 
    "BR": "br.csv", 
    "AT": "at.csv"
}
_FALLBACK_FILE = "extra_eu.csv"


def _read_csv_skipping_comments(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    return list(csv.DictReader(lines, delimiter=";"))


def get_mcp_servers(country_code: str) -> list[McpServerEntry]:
    """Return the ranked MCP server list for a country code, best rank first.

    CSV entries (external/remote MCPs, team-curated) come first by rank. Our own
    per-country endpoint (`internal:<bucket>`) is always appended as the baseline
    so every country has a working MCP endpoint — the correct national bucket
    when we have providers for it, the global bucket (GLEIF/Wikidata) otherwise.
    """
    from app.mcp_servers.country_endpoints import bucket_for_country

    cc = country_code.strip().upper()
    filename = _COUNTRY_FILES.get(cc.split("-")[0], _FALLBACK_FILE)
    path = MCP_DIR / filename
    entries: list[McpServerEntry] = []
    if path.is_file():
        entries = [McpServerEntry(**row) for row in _read_csv_skipping_comments(path)]
        entries.sort(key=lambda e: e.rank)

    bucket = bucket_for_country(cc)
    internal_url = f"internal:{bucket}"
    if not any(e.url == internal_url for e in entries):
        entries.append(
            McpServerEntry(
                rank=(entries[-1].rank + 1) if entries else 1,
                name=f"company-registry-{bucket}",
                url=internal_url,
                notes="built-in per-country MCP endpoint",
            )
        )
    return entries
