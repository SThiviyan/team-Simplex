"""Client for Norway's Brønnøysundregistrene (Brreg) Enhetsregisteret.

Free, keyless open API. Docs: https://data.brreg.no/enhetsregisteret/api/docs
"""

from app.integrations.http import shared_client

API = "https://data.brreg.no/enhetsregisteret/api/enheter"
WEB = "https://virksomhet.brreg.no/nb/oppslag/enheter"
_UA = "team-simplex-hackathon/1.0 (company search demo)"


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the Norwegian business register by name."""
    client = shared_client(
        "brreg", timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    )
    resp = await client.get(API, params={"navn": name, "size": max(1, min(limit, 50))})
    resp.raise_for_status()
    data = resp.json()

    out: list[dict] = []
    for e in data.get("_embedded", {}).get("enheter", []):
        addr = e.get("forretningsadresse") or e.get("postadresse") or {}
        num = e.get("organisasjonsnummer")
        street = ", ".join(line for line in (addr.get("adresse") or []) if line)
        full_address = ", ".join(
            part for part in (street, addr.get("postnummer"), addr.get("poststed"), "NO") if part
        )
        out.append(
            {
                "number": num,
                "name": e.get("navn"),
                "legal_form": (e.get("organisasjonsform") or {}).get("beskrivelse"),
                "city": addr.get("poststed") or addr.get("kommune"),
                "status": "slettet" if e.get("slettedato") else None,
                "country": "NO",
                "url": f"{WEB}/{num}" if num else None,
                "address": full_address or None,
                "incorporation_date": e.get("stiftelsesdato")
                or e.get("registreringsdatoIEnhetsregisteret"),
                "industry_code": (e.get("naeringskode1") or {}).get("kode"),
                "industry": (e.get("naeringskode1") or {}).get("beskrivelse"),
            }
        )
    return out
