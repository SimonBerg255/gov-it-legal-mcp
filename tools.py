"""
Tools for Italian Legal Research MCP Server.

Data sources:
- Normattiva (normattiva.it): Official Italian legislation portal covering all
  numbered regulatory acts from 1861 to present. Access via URN URLs + normattiva2md.
- OpenGA (openga.giustizia-amministrativa.it): CKAN-based open data portal for TAR
  and Consiglio di Stato rulings. Provides metadata JSON (no full text).
- giustizia-amministrativa.it / mdp.giustizia-amministrativa.it: Official GA portal
  serving full ruling text as HTML via the dcsnprr Liferay portlet search.

Research findings (2026-03-17 / 2026-03-19):
  - normattiva2md v2.1.10 works well for direct URL fetch + AKN XML conversion.
  - No public Normattiva REST API (pre.api.normattiva.it unreachable, api.normattiva.it 404).
  - Normattiva search requires POST → redirect → session-based results page.
  - OpenGA: 436 packages, 31 courts, JSON contains 17 metadata fields, NO full text.
  - CdS is under org 'cds', all TAR courts under 'tar-{location}-sentenze'.
  - GA full text: search via POST to /web/guest/dcsnprr (Liferay portlet, needs session
    cookie + p_auth CSRF token extracted fresh per session). Results include direct
    mdp.giustizia-amministrativa.it/visualizzah2/ URLs. OpenGA NUMERO_RICORSO = nrg
    param, enabling direct URL construction from OpenGA metadata.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ItalianLegalResearch/1.0)",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.5",
}

OPENGA_BASE = "https://openga.giustizia-amministrativa.it"
NORMATTIVA_BASE = "https://www.normattiva.it"

# GA portal — full ruling text
GA_SEARCH_URL = "https://www.giustizia-amministrativa.it/web/guest/dcsnprr"
GA_DOC_HTML_BASE = "https://mdp.giustizia-amministrativa.it/visualizzah2/"
# Stable Liferay portlet instance ID (extracted dynamically but this is the fallback)
GA_PORTLET_FALLBACK = "decisioni_pareri_web_DecisioniPareriWebPortlet_INSTANCE_XKc17mrB8J10"

# OpenGA NOME_SEDE → GA portal schema code (Italian province codes)
SEDE_TO_SCHEMA: dict[str, str] = {
    "TAR LAZIO - ROMA": "tar_rm",
    "TAR LAZIO - LATINA": "tar_lt",
    "TAR LOMBARDIA - MILANO": "tar_mi",
    "TAR LOMBARDIA - BRESCIA": "tar_bs",
    "TAR CAMPANIA - NAPOLI": "tar_na",
    "TAR CAMPANIA - SALERNO": "tar_sa",
    "TAR TOSCANA - FIRENZE": "tar_fi",
    "TAR PIEMONTE - TORINO": "tar_to",
    "TAR VENETO - VENEZIA": "tar_ve",
    "TAR PUGLIA - BARI": "tar_ba",
    "TAR PUGLIA - LECCE": "tar_le",
    "TAR SICILIA - PALERMO": "tar_pa",
    "TAR SICILIA - CATANIA": "tar_ct",
    "TAR EMILIA ROMAGNA - BOLOGNA": "tar_bo",
    "TAR EMILIA-ROMAGNA - BOLOGNA": "tar_bo",
    "TAR EMILIA ROMAGNA - PARMA": "tar_pr",
    "TAR LIGURIA - GENOVA": "tar_ge",
    "TAR MARCHE - ANCONA": "tar_an",
    "TAR SARDEGNA - CAGLIARI": "tar_ca",
    "TAR ABRUZZO - L'AQUILA": "tar_aq",
    "TAR ABRUZZO - PESCARA": "tar_pe",
    "TAR CALABRIA - CATANZARO": "tar_cz",
    "TAR CALABRIA - REGGIO CALABRIA": "tar_rc",
    "TAR BASILICATA - POTENZA": "tar_pz",
    "TAR MOLISE - CAMPOBASSO": "tar_cb",
    "TAR UMBRIA - PERUGIA": "tar_pg",
    "TAR VALLE D'AOSTA - AOSTA": "tar_ao",
    "TAR FRIULI VENEZIA GIULIA - TRIESTE": "tar_ts",
    "TRGA - TRENTO": "tar_tn",
    "TRGA - BOLZANO": "tar_bz",
    "CdS GIURISDIZIONALE - ROMA": "cds",
    "CONSIGLIO DI STATO": "cds",
}

# Our court key → GA portal sedeProvvedimenti POST value
COURT_TO_SEDE: dict[str, str] = {
    "tar_lazio": "Roma",
    "tar_lombardia": "Milano",
    "tar_campania": "Napoli",
    "tar_toscana": "Firenze",
    "tar_piemonte": "Torino",
    "tar_veneto": "Venezia",
    "tar_puglia": "Bari",
    "tar_sicilia": "Palermo",
    "tar_emilia_romagna": "Bologna",
    "tar_liguria": "Genova",
    "tar_marche": "Ancona",
    "tar_sardegna": "Cagliari",
    "tar_abruzzo": "L",          # L'Aquila abbreviated in GA form
    "tar_calabria": "Catanzaro",
    "tar_basilicata": "Potenza",
    "tar_molise": "Campobasso",
    "tar_umbria": "Perugia",
    "tar_friuli_venezia_giulia": "Trieste",
    "tar_lazio_latina": "Latina",
    "tar_lombardia_brescia": "Brescia",
    "tar_sicilia_catania": "Catania",
    "tar_campania_salerno": "Salerno",
    "tar_puglia_lecce": "Lecce",
    "tar_emilia_romagna_parma": "Parma",
    "tar_calabria_reggio_calabria": "Reggio Calabria",
    "consiglio_di_stato": "Consiglio di Stato",
    "cds": "Consiglio di Stato",
}

ITALIAN_MONTHS = {
    "gennaio": "01", "febbraio": "02", "marzo": "03", "aprile": "04",
    "maggio": "05", "giugno": "06", "luglio": "07", "agosto": "08",
    "settembre": "09", "ottobre": "10", "novembre": "11", "dicembre": "12",
}

ACT_TYPE_URN = {
    "DECRETO LEGISLATIVO": "decreto.legislativo",
    "DECRETO-LEGGE": "decreto.legge",
    "DECRETO LEGGE": "decreto.legge",
    "LEGGE": "legge",
    "DECRETO DEL PRESIDENTE DELLA REPUBBLICA": "decreto.del.presidente.della.repubblica",
    "DECRETO DEL PRESIDENTE DEL CONSIGLIO DEI MINISTRI": "decreto.del.presidente.del.consiglio.dei.ministri",
    "DECRETO MINISTERIALE": "decreto.ministeriale",
    "DECRETO": "decreto",
    "REGIO DECRETO": "regio.decreto",
    "REGIO DECRETO-LEGGE": "regio.decreto.legge",
    "LEGGE COSTITUZIONALE": "legge.costituzionale",
    "DECRETO INTERMINISTERIALE": "decreto.interministeriale",
}

# Known acts: (urn_type, number, year) -> enactment date (ISO)
KNOWN_ACT_DATES: dict[tuple, str] = {
    ("decreto.legislativo", "196", "2003"): "2003-06-30",  # Codice Privacy
    ("decreto.legislativo", "33", "2013"): "2013-03-14",   # Trasparenza
    ("decreto.legislativo", "82", "2005"): "2005-03-07",   # CAD
    ("decreto.legislativo", "165", "2001"): "2001-03-30",  # Pubblico impiego
    ("decreto.legislativo", "50", "2016"): "2016-04-18",   # Codice appalti (abrogato)
    ("decreto.legislativo", "36", "2023"): "2023-03-31",   # Nuovo codice appalti
    ("decreto.legislativo", "150", "2022"): "2022-10-10",  # Riforma giustizia
    ("decreto.legislativo", "152", "2006"): "2006-04-03",  # Ambiente
    ("decreto.legislativo", "267", "2000"): "2000-08-18",  # TUEL
    ("decreto.legislativo", "231", "2001"): "2001-06-08",  # Responsabilità enti
    ("decreto.legislativo", "24", "2023"): "2023-03-10",   # Whistleblowing
    ("decreto.legislativo", "101", "2018"): "2018-08-10",  # Adeguamento GDPR
    ("decreto.legislativo", "104", "2010"): "2010-07-02",  # Codice processo amm.
    ("decreto.legislativo", "286", "1998"): "1998-07-25",  # Immigrazione
    ("legge", "241", "1990"): "1990-08-07",                # Procedimento amm.
    ("legge", "190", "2012"): "2012-11-06",                # Anticorruzione
    ("legge", "124", "2015"): "2015-08-07",                # Riforma PA Madia
    ("legge", "179", "2017"): "2017-11-30",                # Whistleblowing prev.
    ("legge", "69", "2009"): "2009-06-18",
    ("legge", "76", "2016"): "2016-05-20",
    ("legge", "104", "1992"): "1992-02-05",                # Legge 104 disabilità
    ("legge", "833", "1978"): "1978-12-23",                # SSN
    ("legge", "328", "2000"): "2000-11-08",                # Servizi sociali
    ("decreto.del.presidente.della.repubblica", "445", "2000"): "2000-12-28",  # DPR 445
    ("decreto.del.presidente.della.repubblica", "633", "1972"): "1972-10-26",
    ("decreto.del.presidente.della.repubblica", "917", "1986"): "1986-12-22",  # TUIR
    ("decreto.del.presidente.della.repubblica", "380", "2001"): "2001-06-06",  # Edilizia
}

# Court slug → CKAN package ID
COURT_PACKAGES: dict[str, str] = {
    "tar_lazio": "tar-lazio-roma-sentenze",
    "tar_lombardia": "tar-lombardia-milano-sentenze",
    "tar_campania": "tar-campania-napoli-sentenze",
    "tar_toscana": "tar-toscana-sentenze",
    "tar_piemonte": "tar-piemonte-sentenze",
    "tar_veneto": "tar-veneto-sentenze",
    "tar_puglia": "tar-puglia-bari-sentenze",
    "tar_sicilia": "tar-sicilia-palermo-sentenze",
    "tar_emilia_romagna": "tar-emilia-romagna-bologna-sentenze",
    "tar_liguria": "tar-liguria-sentenze",
    "tar_marche": "tar-marche-sentenze",
    "tar_sardegna": "tar-sardegna-sentenze",
    "tar_abruzzo": "tar-abruzzo-l-aquila-sentenze",
    "tar_calabria": "tar-calabria-catanzaro-sentenze",
    "tar_basilicata": "tar-basilicata-sentenze",
    "tar_molise": "tar-molise-sentenze",
    "tar_umbria": "tar-umbria-sentenze",
    "tar_valle_d_aosta": "tar-valle-d-aosta-sentenze",
    "tar_friuli_venezia_giulia": "tar-friuli-venezia-giulia-sentenze",
    "tar_lazio_latina": "tar-lazio-latina-sentenze",
    "tar_lombardia_brescia": "tar-lombardia-brescia-sentenze",
    "tar_sicilia_catania": "tar-sicilia-catania-sentenze",
    "tar_campania_salerno": "tar-campania-salerno-sentenze",
    "tar_puglia_lecce": "tar-puglia-lecce-sentenze",
    "tar_emilia_romagna_parma": "tar-emilia-romagna-parma-sentenze",
    "tar_calabria_reggio_calabria": "tar-calabria-reggio-calabria-sentenze",
    "consiglio_di_stato": "cds-sentenze",
    "cds": "cds-sentenze",
}

# In-memory cache for downloaded OpenGA JSON files
_json_cache: dict[str, list] = {}


# ---------------------------------------------------------------------------
# Citation parser
# ---------------------------------------------------------------------------

CITATION_PATTERNS = [
    (r"d\.lgs\.?\s+(?:n\.?\s*)?(\d+)/(\d{4})", "decreto.legislativo"),
    (r"decreto\s+legislativo\s+(?:n\.?\s*)?(\d+)/(\d{4})", "decreto.legislativo"),
    (r"d\.l\.?\s+(?:n\.?\s*)?(\d+)/(\d{4})", "decreto.legge"),
    (r"decreto[\s-]legge\s+(?:n\.?\s*)?(\d+)/(\d{4})", "decreto.legge"),
    (r"(?<!\w)l\.\s+(?:n\.?\s*)?(\d+)/(\d{4})", "legge"),
    (r"(?<!\w)legge\s+(?:n\.?\s*)?(\d+)/(\d{4})", "legge"),
    (r"D\.?P\.?R\.?\s+(?:n\.?\s*)?(\d+)/(\d{4})", "decreto.del.presidente.della.repubblica"),
    (r"decreto\s+del\s+presidente\s+della\s+repubblica\s+(?:n\.?\s*)?(\d+)/(\d{4})", "decreto.del.presidente.della.repubblica"),
    (r"D\.?P\.?C\.?M\.?\s+(?:n\.?\s*)?(\d+)/(\d{4})", "decreto.del.presidente.del.consiglio.dei.ministri"),
    (r"DPCM\s+(?:n\.?\s*)?(\d+)/(\d{4})", "decreto.del.presidente.del.consiglio.dei.ministri"),
]


def _parse_citation(query: str) -> tuple | None:
    """Parse Italian legal citation. Returns (urn_type, number, year) or None."""
    for pattern, act_type in CITATION_PATTERNS:
        m = re.search(pattern, query, re.IGNORECASE)
        if m:
            return (act_type, m.group(1), m.group(2))
    return None


def _italian_date_to_iso(day: str, month_str: str, year: str) -> str:
    month = ITALIAN_MONTHS.get(month_str.lower(), "01")
    return f"{year}-{month}-{day.zfill(2)}"


def _parse_act_header(text: str) -> dict:
    """
    Parse normattiva result header text like 'DECRETO LEGISLATIVO 14 Marzo 2013, n. 33'
    into structured fields.
    """
    text = re.sub(r"\(GU[^)]+\)", "", text).strip()
    # Match: TYPE [words] DAY MONTH YEAR, n. NUMBER
    m = re.match(
        r"^(.*?)\s+(\d{1,2})\s+(\w+)\s+(\d{4}),?\s*n\.\s*(\d+)",
        text, re.IGNORECASE,
    )
    if m:
        raw_type = m.group(1).strip().upper()
        day, month_str, year, number = m.group(2), m.group(3), m.group(4), m.group(5)
        iso_date = _italian_date_to_iso(day, month_str, year)
        urn_type = ACT_TYPE_URN.get(raw_type, raw_type.lower().replace(" ", "."))
        return {
            "act_type": urn_type,
            "act_type_display": raw_type,
            "date": iso_date,
            "year": int(year),
            "number": number,
        }
    return {}


# ---------------------------------------------------------------------------
# Tool 1: search_legislation
# ---------------------------------------------------------------------------

async def search_legislation(
    query: str,
    act_type: str = "all",
    year: Optional[int] = None,
    max_results: int = 10,
) -> dict:
    """
    Search Italian legislation on Normattiva (normattiva.it).

    Handles both natural language queries and Italian legal citations:
    - Citations: "d.lgs. 33/2013", "l. 190/2012", "D.P.R. 445/2000", "d.l. 44/2021"
    - Natural language: "legge sulla trasparenza amministrativa", "privacy GDPR"

    For citations, builds a direct Normattiva URN URL without needing a search
    (much faster and more reliable). For unknown citations, falls back to the
    Normattiva web search. For natural language, uses the Normattiva search portal.

    Args:
        query: Search query — natural language or Italian legal citation.
        act_type: Filter by act type. One of: "legge", "decreto_legislativo",
                  "decreto_legge", "DPR", "DPCM", "all" (default "all").
        year: Filter by year of enactment (e.g. 2013).
        max_results: Maximum number of results to return (default 10).

    Returns:
        Dict with keys:
          - source: how match was found ("citation_direct", "citation_search", "web_search")
          - query_parsed: citation parsed form if applicable
          - results: list of dicts, each with title, act_type, date, number, year, url, urn
    """
    # Step 1: try citation parsing
    citation = _parse_citation(query)
    if citation:
        urn_type, number, year_str = citation
        key = (urn_type, number, year_str)

        if key in KNOWN_ACT_DATES:
            date = KNOWN_ACT_DATES[key]
            urn = f"urn:nir:stato:{urn_type}:{date};{number}"
            url = f"{NORMATTIVA_BASE}/uri-res/N2Ls?{urn}"
            return {
                "source": "citation_direct",
                "query_parsed": f"{urn_type} n.{number}/{year_str}",
                "results": [{
                    "title": f"{urn_type.replace('.', ' ').upper()} n. {number} del {date}",
                    "act_type": urn_type,
                    "date": date,
                    "number": number,
                    "year": int(year_str),
                    "url": url,
                    "urn": urn,
                }],
            }

        # Unknown citation: search by number + year
        results = await _normattiva_search(f"{number} {year_str}", urn_type, int(year_str), max_results)
        if results:
            return {
                "source": "citation_search",
                "query_parsed": f"{urn_type} n.{number}/{year_str}",
                "results": results,
            }

    # Step 2: natural language search
    type_filter = {
        "legge": "legge",
        "decreto_legislativo": "decreto.legislativo",
        "decreto_legge": "decreto.legge",
        "DPR": "decreto.del.presidente.della.repubblica",
        "DPCM": "decreto.del.presidente.del.consiglio.dei.ministri",
    }.get(act_type)

    results = await _normattiva_search(query, type_filter, year, max_results)
    return {"source": "web_search", "results": results}


async def _normattiva_search(
    query: str,
    act_type_filter: str = None,
    year: int = None,
    max_results: int = 10,
) -> list:
    """
    POST to Normattiva search form and parse results.
    The site uses server-side sessions: POST initiates search, then follows
    redirect to results page — httpx AsyncClient handles this automatically.
    """
    data = {
        "testoRicerca": query,
        "tabID": "",
        "title": "lbl.risultatoRicerca",
    }

    async with httpx.AsyncClient(
        headers=HEADERS, follow_redirects=True, timeout=30
    ) as client:
        try:
            resp = await client.post(
                f"{NORMATTIVA_BASE}/ricerca/semplice",
                data=data,
                headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Normattiva search error: {e}")
            return []

    soup = BeautifulSoup(resp.text, "lxml")
    results = []

    # Each result: <div class="collapse-div boxAtto ...">
    # (Previously documented as class="risultato" but actual HTML uses "boxAtto")
    for div in soup.find_all("div", class_="boxAtto"):
        try:
            # Main link: <a class="font-weight-semibold" title="Dettaglio atto">
            link = div.find("a", class_="font-weight-semibold") or div.find(
                "a", attrs={"title": "Dettaglio atto"}
            )
            if not link:
                continue

            # Normalize whitespace: get_text with separator collapses inline whitespace
            header_text = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip()
            parsed = _parse_act_header(header_text)
            if not parsed:
                continue

            # Apply filters
            if act_type_filter and parsed.get("act_type") != act_type_filter:
                continue
            if year and parsed.get("year") != year:
                continue

            # Title from second <p> (long description wrapped in brackets)
            paragraphs = div.find_all("p")
            title = ""
            if len(paragraphs) >= 2:
                raw_title = re.sub(r"\s+", " ", paragraphs[1].get_text(" ", strip=True)).strip()
                # Strip outer [...] wrapper, double parens, and codice redazionale
                title = re.sub(r"^\[|\]$", "", raw_title).strip()
                title = re.sub(r"\(\(|\)\)", "", title).strip()
                title = re.sub(r"\(\s*\d{2}[A-Z]\d+\s*\)$", "", title).strip()
                title = re.sub(r"\s+", " ", title).strip()

            # GU publication reference
            gu_span = div.find("span", class_="DateGU")
            gu_ref = gu_span.get_text(strip=True) if gu_span else ""

            # Build URN and Normattiva URL
            act_type_urn = parsed["act_type"]
            date = parsed["date"]
            number = parsed["number"]
            urn = f"urn:nir:stato:{act_type_urn}:{date};{number}"
            url = f"{NORMATTIVA_BASE}/uri-res/N2Ls?{urn}"

            results.append({
                "title": title or header_text,
                "act_type": act_type_urn,
                "date": date,
                "number": number,
                "year": parsed.get("year"),
                "url": url,
                "urn": urn,
                "gu_reference": gu_ref,
            })

            if len(results) >= max_results:
                break

        except Exception as e:
            logger.debug(f"Error parsing Normattiva result: {e}")
            continue

    return results


# ---------------------------------------------------------------------------
# Tool 2: get_legislation_text
# ---------------------------------------------------------------------------

async def get_legislation_text(
    act_url: str,
    article: str = None,
    version: str = "vigente",
) -> dict:
    """
    Retrieve the full text (or a specific article) of an Italian law from Normattiva.

    Uses the normattiva2md library to fetch the official Akoma Ntoso XML and convert
    it to readable Markdown. The text returned is the consolidated (vigente) version
    by default, including all subsequent amendments.

    Args:
        act_url: Normattiva URL or URN string from search_legislation results.
                 Formats accepted:
                   - Full URL: "https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:2013-03-14;33"
                   - URN string: "urn:nir:stato:decreto.legislativo:2013-03-14;33"
        article: Optional article number to retrieve a specific article.
                 Examples: "5", "22", "6-bis", "22bis", "3ter"
                 If not provided, returns the full act text.
        version: Version of the text to retrieve:
                 "vigente" (default) — current consolidated text with all amendments
                 "originale" — original published text without amendments
                 "YYYY-MM-DD" — historical version valid on that specific date

    Returns:
        Dict with keys:
          - title: Official title of the act
          - version: Version retrieved
          - article: Article number if specified
          - text: Full text in Markdown format (includes YAML frontmatter with metadata)
          - source_url: The Normattiva URL used
          - length: Character count of the text
          - error: Set only on failure, with error description
    """
    from normattiva2md import convert_url

    url = _normalize_normattiva_url(act_url, version)
    article_norm = _normalize_article(article)

    try:
        result = await asyncio.to_thread(convert_url, url, article=article_norm)
        text = result.markdown or ""
        if not text:
            return {"error": "No text returned from Normattiva", "source_url": url}

        return {
            "title": result.title or "",
            "version": version,
            "article": article_norm,
            "text": text,
            "source_url": url,
            "length": len(text),
        }
    except Exception as e:
        logger.warning(f"normattiva2md failed for {url}: {e}. Trying fallback.")
        return await _legislation_text_fallback(url, article_norm, version)


def _normalize_normattiva_url(act_url: str, version: str = "vigente") -> str:
    """Normalise input to a full Normattiva URL with optional version modifier."""
    url = act_url.strip()

    if url.startswith("urn:nir:"):
        url = f"{NORMATTIVA_BASE}/uri-res/N2Ls?{url}"
    elif not url.startswith("http"):
        url = f"{NORMATTIVA_BASE}/uri-res/N2Ls?{url}"

    # Append version modifier to the URN portion
    if version == "originale" and "~" not in url and "!" not in url:
        url += "~orig"
    elif version and re.match(r"\d{4}-\d{2}-\d{2}$", version) and "!" not in url:
        url += f"!vig={version}"

    return url


def _normalize_article(article: str) -> str | None:
    """Normalise article: '6-bis' → '6bis', ' 22 ' → '22'"""
    if article is None:
        return None
    return re.sub(r"[-\s]", "", article.strip())


async def _legislation_text_fallback(url: str, article: str, version: str) -> dict:
    """Fallback: fetch Normattiva page directly and extract text via BeautifulSoup."""
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as e:
            return {"error": str(e), "source_url": url}

    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()

    content = (
        soup.find("div", id="bodyTesto")
        or soup.find("div", class_="bodyText")
        or soup.find("main")
        or soup.body
    )
    text = content.get_text("\n", strip=True) if content else soup.get_text()
    text = re.sub(r"\n{3,}", "\n\n", text)

    title_tag = soup.find("h1") or soup.title
    title = title_tag.get_text(strip=True) if title_tag else ""

    return {
        "title": title,
        "version": version,
        "article": article,
        "text": text[:60000],
        "source_url": url,
        "length": len(text),
        "note": "Fallback HTML extraction (normattiva2md unavailable for this request)",
    }


# ---------------------------------------------------------------------------
# Tool 3: search_court_rulings
# ---------------------------------------------------------------------------

async def search_court_rulings(
    query: str,
    court: str = "all",
    year: int = None,
    ruling_type: str = None,
    max_results: int = 10,
) -> dict:
    """
    Search Italian administrative court rulings from OpenGA open data portal.

    Searches TAR (Tribunale Amministrativo Regionale) and Consiglio di Stato
    decisions using structured metadata downloaded from the OpenGA CKAN catalogue
    (openga.giustizia-amministrativa.it). Each court provides annual JSON files
    with 17 metadata fields per ruling; full text is not available in open data.

    Keyword search is performed against the OGGETTO_RICORSO field (subject of
    the appeal), which typically contains a 1-3 line description of the dispute.

    Args:
        query: Keywords to search in Italian (searches ruling subject field).
               Examples: "trasparenza accesso documenti", "appalti pubblici",
               "segnalante whistleblowing", "decisione algoritmica"
        court: Court filter. One of:
               "tar_lazio", "tar_lombardia", "tar_campania", "tar_toscana",
               "tar_piemonte", "tar_veneto", "tar_puglia", "tar_sicilia",
               "tar_emilia_romagna", "tar_liguria", "tar_marche", "tar_sardegna",
               "tar_abruzzo", "tar_basilicata", "tar_molise", "tar_umbria",
               "tar_friuli_venezia_giulia", "tar_valle_d_aosta",
               "tar_lazio_latina", "tar_lombardia_brescia", "tar_sicilia_catania",
               "tar_campania_salerno", "tar_puglia_lecce",
               "consiglio_di_stato" (or "cds"), "all" (default)
        year: Filter by publication year (2021–2026). If not specified, searches
              the last 3 years automatically.
        ruling_type: Filter by ruling type: "sentenza", "ordinanza", "decreto",
                     "parere". If not specified, returns all types.
        max_results: Maximum number of results to return (default 10).

    Returns:
        Dict with keys:
          - query, court_filter, year_filter: echo of inputs
          - total_found: number of matching records
          - note: limitation notice about metadata-only content
          - results: list of dicts with court, ruling_number, appeal_number,
                     date, ruling_type, outcome, appeal_type, subject, dataset
    """
    court_lower = court.lower().strip()

    # Determine which packages to search
    if court_lower == "all":
        packages = ["cds-sentenze", "tar-lazio-roma-sentenze", "tar-lombardia-milano-sentenze"]
    elif court_lower in COURT_PACKAGES:
        packages = [COURT_PACKAGES[court_lower]]
    else:
        # Fuzzy match on partial court name
        matched = [v for k, v in COURT_PACKAGES.items() if court_lower in k or court_lower.replace(" ", "_") in k]
        packages = matched[:3] if matched else ["cds-sentenze", "tar-lazio-roma-sentenze"]

    # Determine years to search
    current_year = datetime.now().year
    years_to_try = [year] if year else [current_year, current_year - 1, current_year - 2]

    # Tokenise query for matching
    query_terms = [t.lower() for t in re.split(r"\s+", query.strip()) if len(t) > 2]

    all_results = []

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=60) as client:
        for package_id in packages:
            for yr in years_to_try:
                records = await _get_openga_records(client, package_id, yr)
                if not records:
                    continue

                for record in records:
                    subject = record.get("OGGETTO_RICORSO", "").lower()
                    tipo = record.get("TIPO_PROVVEDIMENTO", "").lower()

                    # Ruling type filter
                    if ruling_type and ruling_type.lower() not in tipo:
                        continue

                    # Keyword score: count distinct query terms found in subject
                    score = sum(1 for t in query_terms if t in subject)
                    if score == 0:
                        continue

                    all_results.append((_score_record(record, query_terms), record, package_id, yr))

                if len(all_results) >= max_results * len(packages):
                    break
            if len(all_results) >= max_results * 5:
                break

    # Sort by score then by date descending
    all_results.sort(key=lambda x: (x[0], x[1].get("DATA_PUBBLICAZIONE", "")), reverse=True)

    formatted = [
        _format_ruling(record, pkg_id, yr)
        for _, record, pkg_id, yr in all_results[:max_results]
    ]

    return {
        "query": query,
        "court_filter": court,
        "year_filter": year,
        "total_found": len(formatted),
        "note": (
            "OpenGA provides ruling metadata only — full text is not in the open data. "
            "Use get_ruling_text() with a ruling_number to retrieve all available details."
        ),
        "results": formatted,
    }


def _score_record(record: dict, terms: list[str]) -> int:
    """Score a record by how many query terms appear in its subject field."""
    subject = record.get("OGGETTO_RICORSO", "").lower()
    return sum(1 for t in terms if t in subject)


async def _get_openga_records(
    client: httpx.AsyncClient, package_id: str, year: int
) -> list:
    """
    Fetch OpenGA JSON for a given court package and year.
    Results are cached in memory for the lifetime of the server process.
    """
    cache_key = f"{package_id}_{year}"
    if cache_key in _json_cache:
        return _json_cache[cache_key]

    try:
        # Step 1: get package metadata to find the correct JSON resource URL
        pkg_resp = await client.get(
            f"{OPENGA_BASE}/api/3/action/package_show",
            params={"id": package_id},
            timeout=15,
        )
        pkg_resp.raise_for_status()
        pkg_data = pkg_resp.json()

        if not pkg_data.get("success"):
            logger.warning(f"CKAN package_show failed for {package_id}")
            return []

        resources = pkg_data["result"].get("resources", [])
        year_str = str(year)

        # Find JSON resource matching the year
        json_resource = None
        for r in resources:
            if r.get("format", "").upper() == "JSON" and (
                year_str in r.get("url", "") or year_str in r.get("name", "")
            ):
                json_resource = r
                break

        # Fallback: any JSON resource
        if not json_resource:
            for r in resources:
                if r.get("format", "").upper() == "JSON":
                    json_resource = r
                    break

        if not json_resource:
            logger.debug(f"No JSON resource for {package_id}/{year}")
            return []

        # Step 2: download the JSON file
        download_url = json_resource.get("url", "")
        if not download_url:
            return []

        data_resp = await client.get(download_url, timeout=90)
        data_resp.raise_for_status()
        records = data_resp.json()

        if isinstance(records, dict):
            records = records.get("data", records.get("records", []))

        if not isinstance(records, list):
            return []

        _json_cache[cache_key] = records
        return records

    except Exception as e:
        logger.error(f"Failed to fetch OpenGA data for {package_id}/{year}: {e}")
        return []


def _format_ruling(record: dict, package_id: str, year: int) -> dict:
    """Format an OpenGA ruling record for API output."""
    return {
        "court": record.get("NOME_SEDE", ""),
        "section": record.get("NOME_SEZIONE", ""),
        "ruling_number": str(record.get("NUMERO_PROVVEDIMENTO", "")),
        "appeal_number": str(record.get("NUMERO_RICORSO", "")),
        "date": record.get("DATA_PUBBLICAZIONE", ""),
        "year": record.get("ANNO_PUBBLICAZIONE", year),
        "ruling_type": record.get("TIPO_PROVVEDIMENTO", ""),
        "hearing_type": record.get("TIPO_UDIENZA", ""),
        "outcome": record.get("ESITO_PROVVEDIMENTO", ""),
        "appeal_type": record.get("TIPO_RICORSO", ""),
        "subject": record.get("OGGETTO_RICORSO", ""),
        "dataset": package_id,
        "openga_dataset_url": f"{OPENGA_BASE}/dataset/{package_id}",
    }


# ---------------------------------------------------------------------------
# GA portal helpers — full ruling text from giustizia-amministrativa.it
# ---------------------------------------------------------------------------

def _extract_portlet_id(html: str) -> str:
    """Extract Liferay portlet instance ID from page HTML, fall back to known value."""
    m = re.search(r"decisioni_pareri_web_DecisioniPareriWebPortlet_INSTANCE_(\w+)", html)
    return m.group(0) if m else GA_PORTLET_FALLBACK


def _extract_p_auth(html: str) -> str | None:
    """Extract Liferay p_auth CSRF token from page HTML."""
    m = re.search(r'Liferay\.authToken\s*=\s*["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)
    m = re.search(r'p_auth=([a-zA-Z0-9_\-]+)', html)
    return m.group(1) if m else None


def _sede_to_schema(nome_sede: str) -> str | None:
    """Map OpenGA NOME_SEDE string to GA portal schema code."""
    # Exact match first
    schema = SEDE_TO_SCHEMA.get(nome_sede.strip().upper())
    if schema:
        return schema
    # Partial match on city name
    upper = nome_sede.upper()
    for key, val in SEDE_TO_SCHEMA.items():
        if key in upper or upper in key:
            return val
    return None


async def _ga_search_session(
    client: httpx.AsyncClient,
    query: str,
    sede: str = "",
    year: int = None,
    ruling_type: str = "Sentenza",
    page_size: int = 20,
) -> list[dict]:
    """
    Run a full-text search on the GA portal.
    Returns list of result dicts with visualizza_url, ruling_number, schema, nrg.
    """
    # Step 1: GET the search page → extract session cookie + p_auth + portlet ID
    try:
        page_resp = await client.get(
            GA_SEARCH_URL,
            headers={**HEADERS, "Accept": "text/html,application/xhtml+xml"},
        )
        page_resp.raise_for_status()
    except Exception as e:
        logger.error(f"GA portal GET failed: {e}")
        return []

    p_auth = _extract_p_auth(page_resp.text)
    portlet_id = _extract_portlet_id(page_resp.text)
    ns = f"_{portlet_id}_"  # portlet namespace prefix for form fields

    if not p_auth:
        logger.warning("Could not extract p_auth from GA portal page")
        return []

    # Step 2: POST search form
    action_url = (
        f"{GA_SEARCH_URL}"
        f"?p_p_id={portlet_id}"
        f"&p_p_lifecycle=1&p_p_state=normal&p_p_mode=view"
        f"&{ns}javax.portlet.action=search"
        f"&p_auth={p_auth}"
    )
    data = {
        f"{ns}searchtextProvvedimenti": query,
        f"{ns}isAdvancedSearch": "false",
        f"{ns}pageSize": str(page_size),
        f"{ns}TipoProvvedimentoItem": ruling_type or "",
    }
    if sede:
        data[f"{ns}sedeProvvedimenti"] = sede
    if year:
        data[f"{ns}DataYearItem"] = str(year)

    try:
        search_resp = await client.post(
            action_url,
            data=data,
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        )
        search_resp.raise_for_status()
    except Exception as e:
        logger.error(f"GA portal POST failed: {e}")
        return []

    return _parse_ga_results(search_resp.text)


def _parse_ga_results(html: str) -> list[dict]:
    """Parse GA portal search results page into structured records."""
    soup = BeautifulSoup(html, "lxml")
    results = []

    for article in soup.find_all("article", class_="ricerca--item"):
        # Main link with data attributes and visualizza URL
        anchor = article.find("a", href=re.compile(r"mdp\.giustizia-amministrativa\.it"))
        if not anchor:
            continue

        href = anchor.get("href", "")
        schema = anchor.get("data-sede", "")
        nrg = anchor.get("data-nrg", "")

        # Convert /visualizza/ → /visualizzah2/ for HTML output
        html_url = href.replace("/visualizza/", "/visualizzah2/")

        # Extract ruling metadata from article text
        text = re.sub(r"\s+", " ", article.get_text(" ", strip=True))
        ruling_num_m = re.search(r"numero provv\.?:?\s*(\d+)", text, re.IGNORECASE)
        ruling_number = ruling_num_m.group(1) if ruling_num_m else ""

        tipo_m = re.search(r"^(SENTENZA(?:\s+BREVE)?|ORDINANZA|DECRETO|PARERE)", text, re.IGNORECASE)
        ruling_type = tipo_m.group(1) if tipo_m else ""

        sede_m = re.search(r"sede di\s+([^,]+),\s*sezione\s+([^,]+),", text, re.IGNORECASE)
        court = sede_m.group(1).strip() if sede_m else ""
        section = sede_m.group(2).strip() if sede_m else ""

        results.append({
            "ruling_number": ruling_number,
            "nrg": nrg,
            "schema": schema,
            "court": court,
            "section": section,
            "ruling_type": ruling_type,
            "visualizza_url": html_url,
            "source_url": html_url,
        })

    return results


async def _fetch_ruling_html_from_metadata(
    client: httpx.AsyncClient,
    schema: str,
    nrg: str,
    ruling_number: str,
) -> str | None:
    """
    Try to fetch ruling HTML directly from mdp.giustizia-amministrativa.it
    using OpenGA metadata. Tries multiple nomeFile suffixes.
    Returns raw HTML string or None if not found.
    """
    # Suffixes in order of likelihood
    for suffix in ["_01", "_00", "_11", "_20", "_02", "_10"]:
        url = (
            f"{GA_DOC_HTML_BASE}"
            f"?nodeRef=&schema={schema}&nrg={nrg}"
            f"&nomeFile={ruling_number}{suffix}.html&subDir=Provvedimenti"
        )
        try:
            resp = await client.get(url, timeout=20)
            if resp.status_code == 200 and len(resp.text) > 2000:
                # Validate it's a real ruling — GA 404s return HTTP 200 with short page
                if any(kw in resp.text for kw in ["P.Q.M", "FATTO", "DIRITTO", "Provvedimento", "dispositivo"]):
                    return resp.text
        except Exception:
            continue
    return None


def _extract_ruling_text(html: str) -> dict:
    """
    Parse GA portal visualizzah2 HTML and extract structured ruling text.
    Returns dict with sections: header, fatto_diritto, dispositivo, full_text.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    # Try to find the main document body
    body = (
        soup.find("div", class_=re.compile(r"document|provvedimento|body|content", re.I))
        or soup.find("div", id=re.compile(r"document|provvedimento|body|content", re.I))
        or soup.body
    )

    full_text = re.sub(r"\n{3,}", "\n\n", body.get_text("\n", strip=True)) if body else ""

    # Try to extract specific sections
    text_upper = full_text.upper()

    fatto_start = max(text_upper.find("FATTO E DIRITTO"), text_upper.find("IN FATTO"))
    pqm_start = text_upper.find("P.Q.M")
    if pqm_start == -1:
        pqm_start = text_upper.find("PQM")

    fatto_diritto = ""
    dispositivo = ""

    if fatto_start >= 0 and pqm_start > fatto_start:
        fatto_diritto = full_text[fatto_start:pqm_start].strip()
        dispositivo = full_text[pqm_start:].strip()
    elif pqm_start >= 0:
        dispositivo = full_text[pqm_start:].strip()
        fatto_diritto = full_text[:pqm_start].strip()

    return {
        "full_text": full_text[:80000],
        "fatto_diritto": fatto_diritto[:40000] if fatto_diritto else "",
        "dispositivo": dispositivo[:10000] if dispositivo else "",
        "char_count": len(full_text),
    }


# ---------------------------------------------------------------------------
# Tool 4: get_ruling_text
# ---------------------------------------------------------------------------

async def get_ruling_text(ruling_reference: str) -> dict:
    """
    Retrieve the full text of an Italian administrative court ruling.

    Searches cached OpenGA metadata (from previous search_court_rulings calls) to
    identify the ruling, then fetches the complete decision text directly from the
    official GA portal (giustizia-amministrativa.it). Returns full reasoning text
    (fatto, in diritto) and the operative section (P.Q.M. / dispositivo).

    If the ruling is not in cache, it falls back to a keyword search on the GA portal.

    Args:
        ruling_reference: Ruling identifier. Accepts:
          - Ruling number as returned by search_court_rulings (e.g. "202400196")
          - Court + number (e.g. "TAR LAZIO 202400196")
          - Appeal number / NRG (NUMERO_RICORSO, e.g. "1234/2023")
          Any numeric string of 6+ digits will be matched against cached records.

    Returns:
        Dict with keys:
          - found: True if ruling was located
          - full_text_available: True if complete text was retrieved from GA portal
          - court, section, ruling_number, appeal_number: identifiers
          - date, year: publication date
          - ruling_type, hearing_type, outcome, appeal_type: classification
          - subject: OGGETTO_RICORSO — brief subject description
          - full_text: complete ruling text (if available, up to 80 000 chars)
          - fatto_diritto: fact and law sections extracted from full text
          - dispositivo: operative section starting at P.Q.M.
          - char_count: total character length of full text
          - source_url: URL where full text was retrieved
          - openga_dataset_url: link to source metadata dataset
    """
    formatted_results = _find_in_cache(ruling_reference)
    raw_results = _find_raw_in_cache(ruling_reference)

    async with httpx.AsyncClient(
        headers=HEADERS, follow_redirects=True, timeout=40
    ) as client:

        if formatted_results and raw_results:
            record = formatted_results[0]
            raw = raw_results[0]

            nome_sede = raw.get("NOME_SEDE", record.get("court", ""))
            nrg = str(raw.get("NUMERO_RICORSO", record.get("appeal_number", "")))
            ruling_num = str(raw.get("NUMERO_PROVVEDIMENTO", record.get("ruling_number", "")))

            schema = _sede_to_schema(nome_sede)
            full_text_data = None
            source_url = ""

            # Attempt 1: direct filename construction (fast, no extra request)
            if schema and nrg and ruling_num:
                html = await _fetch_ruling_html_from_metadata(
                    client, schema, nrg, ruling_num
                )
                if html:
                    full_text_data = _extract_ruling_text(html)
                    source_url = (
                        f"{GA_DOC_HTML_BASE}?schema={schema}&nrg={nrg}"
                        f"&nomeFile={ruling_num}_01.html&subDir=Provvedimenti"
                    )

            # Attempt 2: GA portal keyword search to find the visualizza URL
            if not full_text_data:
                ga_hits = await _ga_search_session(
                    client,
                    query=ruling_num or ruling_reference,
                    sede=SEDE_TO_SCHEMA.get(nome_sede.upper(), ""),
                    page_size=5,
                )
                for hit in ga_hits:
                    url = hit.get("visualizza_url", "")
                    if not url:
                        continue
                    try:
                        resp = await client.get(url, timeout=30)
                        if resp.status_code == 200 and len(resp.text) > 2000:
                            if any(kw in resp.text for kw in ["P.Q.M", "FATTO", "DIRITTO", "dispositivo"]):
                                full_text_data = _extract_ruling_text(resp.text)
                                source_url = url
                                break
                    except Exception:
                        continue

            result: dict = {
                "found": True,
                "full_text_available": full_text_data is not None,
                "court": record.get("court", ""),
                "section": record.get("section", ""),
                "ruling_number": record.get("ruling_number", ""),
                "appeal_number": record.get("appeal_number", ""),
                "date": record.get("date", ""),
                "year": record.get("year", ""),
                "ruling_type": record.get("ruling_type", ""),
                "hearing_type": record.get("hearing_type", ""),
                "outcome": record.get("outcome", ""),
                "appeal_type": record.get("appeal_type", ""),
                "subject": record.get("subject", ""),
                "openga_dataset_url": record.get("openga_dataset_url", ""),
            }

            if full_text_data:
                result.update({
                    "full_text": full_text_data["full_text"],
                    "fatto_diritto": full_text_data["fatto_diritto"],
                    "dispositivo": full_text_data["dispositivo"],
                    "char_count": full_text_data["char_count"],
                    "source_url": source_url,
                })
            else:
                result["note"] = (
                    "Metadata retrieved from OpenGA but full text could not be fetched "
                    "from the GA portal — the ruling may use a non-standard filename or "
                    "the document is not yet published online. "
                    f"Try searching directly: {GA_SEARCH_URL}"
                )

            return result

    # Not found in cache at all
    return {
        "found": False,
        "full_text_available": False,
        "reference": ruling_reference,
        "note": (
            "Ruling not found in cached data. Call search_court_rulings() first to "
            "load rulings into cache, then pass the ruling_number here. "
            "Alternatively, use search_rulings_fulltext() to search by keywords directly."
        ),
    }


def _find_in_cache(reference: str) -> list:
    """Search in-memory OpenGA cache for a ruling by reference number."""
    results = []
    num_match = re.search(r"\d{6,}", reference)
    search_num = num_match.group(0) if num_match else reference.strip()

    for cache_key, records in _json_cache.items():
        parts = cache_key.rsplit("_", 1)
        pkg_id = parts[0]
        yr = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0

        for record in records:
            prov_num = str(record.get("NUMERO_PROVVEDIMENTO", ""))
            ricorso_num = str(record.get("NUMERO_RICORSO", ""))
            if search_num and (search_num in prov_num or search_num in ricorso_num):
                results.append(_format_ruling(record, pkg_id, yr))

    return results


def _find_raw_in_cache(reference: str) -> list[dict]:
    """Return raw OpenGA records matching a ruling reference (for GA portal lookups)."""
    num_match = re.search(r"\d{6,}", reference)
    search_num = num_match.group(0) if num_match else reference.strip()
    results = []
    for records in _json_cache.values():
        for record in records:
            prov_num = str(record.get("NUMERO_PROVVEDIMENTO", ""))
            ricorso_num = str(record.get("NUMERO_RICORSO", ""))
            if search_num and (search_num in prov_num or search_num in ricorso_num):
                results.append(record)
    return results


# ---------------------------------------------------------------------------
# Tool 5: search_rulings_fulltext
# ---------------------------------------------------------------------------

async def search_rulings_fulltext(
    query: str,
    court: str = "",
    year: int = None,
    ruling_type: str = "Sentenza",
    max_results: int = 3,
) -> dict:
    """
    Search Italian administrative court decisions with full text via the GA portal.

    Unlike search_court_rulings (which searches OpenGA metadata by court and year),
    this tool sends a free-text keyword query directly to giustizia-amministrativa.it
    and retrieves the complete decision text — fatto, diritto, and dispositivo — for
    each matching ruling. Use this when you need to find decisions on a specific legal
    topic or understand how courts have ruled on a particular issue.

    Args:
        query: Free-text search query in Italian (e.g. "accesso atti whistleblowing",
               "silenzio inadempimento 30 giorni", "appalti pubblici subappalto").
               More specific terms yield better results.
        court: Optional court filter. Accepts common names such as:
               "TAR Lazio", "TAR Lombardia", "Consiglio di Stato", "TAR Campania", etc.
               Leave empty to search all courts.
        year:  Optional year filter (e.g. 2023, 2024). Leave as None for all years.
        ruling_type: Type of decision to retrieve — "Sentenza" (default), "Ordinanza",
                     "Decreto", or "Parere".
        max_results: How many rulings to retrieve full text for. Range 1–8, default 3.
                     Higher values take longer (one HTTP request per ruling).

    Returns:
        Dict with:
          - query: the search query used
          - court_filter, year_filter, ruling_type_filter: active filters
          - total_found: total results returned by GA portal search
          - results_returned: number of results included (≤ max_results)
          - results: list of dicts, each containing:
              - court, section, ruling_type, ruling_number, nrg (appeal number)
              - full_text_available: True if full text was successfully retrieved
              - full_text: complete decision text (up to 80 000 chars)
              - fatto_diritto: reasoning section (facts + law analysis)
              - dispositivo: operative/dispositif section (P.Q.M.)
              - char_count: text length in characters
              - source_url: direct URL to the HTML document on the GA portal
          - note: any relevant notes or warnings
    """
    # Resolve court name to GA portal sede filter value
    sede = ""
    if court:
        court_lower = court.lower().strip()
        for key, val in COURT_TO_SEDE.items():
            if key.replace("_", " ") in court_lower or court_lower in key.replace("_", " "):
                sede = val
                break
        if not sede:
            # Attempt a loose match on value strings
            for val in COURT_TO_SEDE.values():
                if val.lower() in court_lower or court_lower in val.lower():
                    sede = val
                    break
        if not sede:
            sede = court  # pass through as-is; the portal may still accept it

    max_results = max(1, min(8, max_results))

    async with httpx.AsyncClient(
        headers=HEADERS, follow_redirects=True, timeout=60
    ) as client:
        ga_results = await _ga_search_session(
            client,
            query=query,
            sede=sede,
            year=year,
            ruling_type=ruling_type,
            page_size=max(20, max_results * 4),
        )

        enriched: list[dict] = []

        for item in ga_results:
            if len(enriched) >= max_results:
                break

            html_url = item.get("visualizza_url", "")
            full_text_data = None

            if html_url:
                try:
                    resp = await client.get(html_url, timeout=35)
                    if resp.status_code == 200 and len(resp.text) > 2000:
                        if any(kw in resp.text for kw in ["P.Q.M", "FATTO", "DIRITTO", "dispositivo", "Provvedimento"]):
                            full_text_data = _extract_ruling_text(resp.text)
                except Exception as e:
                    logger.warning(f"Failed to fetch ruling text from {html_url}: {e}")

            enriched_item = dict(item)
            if full_text_data:
                enriched_item.update({
                    "full_text_available": True,
                    "full_text": full_text_data["full_text"],
                    "fatto_diritto": full_text_data["fatto_diritto"],
                    "dispositivo": full_text_data["dispositivo"],
                    "char_count": full_text_data["char_count"],
                })
            else:
                enriched_item["full_text_available"] = False

            enriched.append(enriched_item)

    note = ""
    if not ga_results:
        note = (
            "No results found on the GA portal for this query. Try broader search terms, "
            "remove filters, or use search_court_rulings() to browse by court and year."
        )
    elif not enriched:
        note = "Results were found but full text could not be retrieved. Try again or check source_url directly."

    return {
        "query": query,
        "court_filter": sede or "all courts",
        "year_filter": year or "all years",
        "ruling_type_filter": ruling_type,
        "total_found": len(ga_results),
        "results_returned": len(enriched),
        "results": enriched,
        "note": note,
    }
