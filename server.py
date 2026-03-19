from fastmcp import FastMCP
from mcp.server.fastmcp import Icon
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from tools import (
    search_legislation,
    get_legislation_text,
    search_court_rulings,
    get_ruling_text,
    search_rulings_fulltext,
)

####### SERVER #######

icon = Icon(
    src="https://raw.githubusercontent.com/SimonBerg255/gov-it-legal-mcp/main/icon.png",
)

mcp = FastMCP(
    name="Italian Legal Research Server",
    instructions=(
        "Use this server to research Italian legislation and administrative court rulings.\n\n"
        "Available tools:\n"
        "- search_legislation: find laws by keyword or citation (e.g. 'd.lgs. 33/2013', 'l. 241/1990')\n"
        "- get_legislation_text: retrieve full or article-level text of a law from Normattiva\n"
        "- search_court_rulings: browse TAR and Consiglio di Stato decisions from OpenGA by court/year\n"
        "- get_ruling_text: retrieve full decision text for a ruling found via search_court_rulings\n"
        "- search_rulings_fulltext: keyword search across all courts with full decision text "
        "(fatto, diritto, dispositivo) — use this for topic-based research\n\n"
        "Data sources: Normattiva (normattiva.it) for official legislation; "
        "OpenGA (openga.giustizia-amministrativa.it) for court metadata; "
        "GA portal (giustizia-amministrativa.it) for full decision text.\n"
        "All tools run automatically without user confirmation."
    ),
    version="1.0.0",
    website_url="https://www.normattiva.it",
    icons=[icon],
)

####### TOOLS #######

mcp.tool(meta={"requires_permission": False})(search_legislation)
mcp.tool(meta={"requires_permission": False})(get_legislation_text)
mcp.tool(meta={"requires_permission": False})(search_court_rulings)
mcp.tool(meta={"requires_permission": False})(get_ruling_text)
mcp.tool(meta={"requires_permission": False})(search_rulings_fulltext)

####### ROUTES #######


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


####### APP #######
# Run with: uvicorn server:app --host 0.0.0.0 --port $PORT
# No authentication — open access

app = mcp.http_app()
