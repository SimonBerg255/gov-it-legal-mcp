from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from tools import (
    search_legislation,
    get_legislation_text,
    search_court_rulings,
    get_ruling_text,
)

####### SERVER #######

mcp = FastMCP(
    name="Italian Legal Research Server",
    instructions=(
        "Use this server to research Italian legislation and administrative court rulings.\n\n"
        "Available tools:\n"
        "- search_legislation: find laws by keyword or citation (e.g. 'd.lgs. 33/2013', 'l. 241/1990')\n"
        "- get_legislation_text: retrieve full or article-level text of a law from Normattiva\n"
        "- search_court_rulings: find TAR and Consiglio di Stato decisions from OpenGA\n"
        "- get_ruling_text: retrieve all available metadata for a specific court ruling\n\n"
        "Data sources: Normattiva (normattiva.it) for official legislation; "
        "OpenGA (openga.giustizia-amministrativa.it) for court ruling metadata.\n"
        "All tools run automatically without user confirmation."
    ),
    version="1.0.0",
    website_url="https://www.normattiva.it",
)

####### TOOLS #######

mcp.tool(meta={"requires_permission": False})(search_legislation)
mcp.tool(meta={"requires_permission": False})(get_legislation_text)
mcp.tool(meta={"requires_permission": False})(search_court_rulings)
mcp.tool(meta={"requires_permission": False})(get_ruling_text)

####### ROUTES #######


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


####### APP #######
# Run with: uvicorn server:app --host 0.0.0.0 --port $PORT
# No authentication — open access

app = mcp.http_app()
