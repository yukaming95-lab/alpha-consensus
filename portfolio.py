"""
Fetches real 13F portfolio holdings from SEC EDGAR for institutional traders.
No API key required — SEC EDGAR is public.
"""
import requests
import xml.etree.ElementTree as ET
import json
from datetime import datetime

HEADERS = {"User-Agent": "AlphaConsensus research@alphaconsensus.local"}

# Map X handle → SEC CIK (zero-padded to 10 digits)
FUND_CIKS = {
    "CathieDWood":  "0001779895",  # ARK Investment Management LLC
    "BillAckman":   "0001336528",  # Pershing Square Capital Management LP
    "DanielSLoeb":  "0001418814",  # Third Point LLC
    "BurryArchive": "0001649339",  # Scion Asset Management LLC
    "chamath":      "0001766850",  # Social Capital Hedosophia
}


def _get_latest_13f_accession(cik: str) -> tuple[str, str] | None:
    """Return (accession_number, filing_date) for the most recent 13F-HR."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[portfolio] submissions fetch error for CIK {cik}: {e}")
        return None

    filings = data.get("filings", {}).get("recent", {})
    forms   = filings.get("form", [])
    acc_nos = filings.get("accessionNumber", [])
    dates   = filings.get("filingDate", [])

    for form, acc, date in zip(forms, acc_nos, dates):
        if form in ("13F-HR", "13F-HR/A"):
            return acc.replace("-", ""), date
    return None


def _fetch_holdings(cik: str, accession: str) -> list[dict]:
    """Parse the infotable XML from a 13F filing and return holdings list."""
    import re as _re
    cik_int = int(cik)
    dir_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/"

    # Fetch the filing directory listing (HTML) and find the infotable XML
    try:
        r = requests.get(dir_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        print(f"[portfolio] directory fetch error: {e}")
        return []

    # Find infotable XML filename from directory listing
    candidates = _re.findall(r'href="([^"]+\.xml)"', html, _re.IGNORECASE)
    info_file = None
    for c in candidates:
        name = c.lower().split("/")[-1]
        if "infotable" in name or "form13finfotable" in name:
            info_file = c.split("/")[-1]
            break
    if not info_file:
        # fallback: any XML not named after primary form
        for c in candidates:
            name = c.lower().split("/")[-1]
            if name.endswith(".xml") and "primary" not in name and "form13fhr" not in name:
                info_file = c.split("/")[-1]
                break
    if not info_file and candidates:
        # last resort: just pick any XML
        info_file = candidates[-1].split("/")[-1]

    if not info_file:
        print(f"[portfolio] no XML file found in directory for CIK {cik}")
        return []

    xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{info_file}"
    try:
        r = requests.get(xml_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[portfolio] xml fetch error: {e}")
        return []

    # Parse XML — handle namespace variations
    root = ET.fromstring(r.content)
    ns_map = {
        "n1": "com/sc/13F",
        "ns1": "com/sc/13F",
    }
    # Try to detect namespace from root tag
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    holdings = []
    for entry in root.iter(f"{ns}infoTable"):
        try:
            name  = (entry.findtext(f"{ns}nameOfIssuer") or "").strip()
            value = entry.findtext(f"{ns}value") or "0"
            cusip = entry.findtext(f"{ns}cusip") or ""
            shares_el = entry.find(f"{ns}shrsOrPrnAmt")
            shares = "0"
            if shares_el is not None:
                shares = shares_el.findtext(f"{ns}sshPrnamt") or "0"
            holdings.append({
                "name":   name,
                "cusip":  cusip,
                "value":  int(str(value).replace(",", "")),   # thousands USD
                "shares": int(str(shares).replace(",", "")),
            })
        except Exception:
            continue

    # Sort by value descending, compute percentages
    holdings.sort(key=lambda x: x["value"], reverse=True)
    total_value = sum(h["value"] for h in holdings) or 1

    # SEC 13F value is in thousands of USD — convert to millions for display
    # If raw values look like they're already in dollars (very large), scale accordingly
    sample = holdings[0]["value"] if holdings else 0
    divisor = 1_000_000 if sample > 1_000_000_000 else 1_000

    for h in holdings:
        h["pct"]     = round(h["value"] / total_value * 100, 2)
        h["value_m"] = round(h["value"] / divisor, 1)

    return holdings


def get_portfolio(handle: str) -> dict | None:
    """Return portfolio data for a trader handle, or None if not available."""
    cik = FUND_CIKS.get(handle)
    if not cik:
        return None

    result = _get_latest_13f_accession(cik)
    if not result:
        return None

    accession, filing_date = result
    holdings = _fetch_holdings(cik, accession)
    if not holdings:
        return None

    return {
        "handle":      handle,
        "cik":         cik,
        "filing_date": filing_date,
        "total_value": round(sum(h["value"] for h in holdings) / 1000, 1),
        "positions":   len(holdings),
        "holdings":    holdings[:30],   # top 30
    }
