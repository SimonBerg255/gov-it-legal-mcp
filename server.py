from fastmcp import FastMCP
from mcp.server.fastmcp import Icon
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from tools import (
    search_legislation,
    get_legislation_text,
    search_court_rulings,
    summarize_court_rulings,
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
        "Server for researching Italian legislation and administrative court rulings.\n\n"
        "DECISION TREE — pick the right tool based on the user's question:\n\n"
        "1. USER ASKS ABOUT A LAW (legislation, decreto, legge, codice, normativa):\n"
        "   → search_legislation — find the law by citation or keywords\n"
        "   → get_legislation_text — read the actual law text\n"
        "   WORKFLOW: search first to get the URL, then get_legislation_text to read it.\n"
        "   For large laws (Codice Appalti, TUEL, CAD): NEVER fetch full text.\n"
        "   Use get_legislation_text with articles='1-10' or article='22' instead.\n\n"
        "2. USER ASKS ABOUT COURT RULINGS (sentenza, decisione, giurisprudenza, TAR, CdS):\n"
        "   a) Wants to find specific rulings → search_court_rulings (fast, metadata)\n"
        "   b) Wants to read a ruling's text → get_ruling_text (fetches full text from GA)\n"
        "   c) Wants topic-based research with full text → search_rulings_fulltext\n"
        "   d) Wants statistics/counts/trends → summarize_court_rulings\n"
        "   IMPORTANT: search_court_rulings queries must be in ITALIAN legal terms.\n"
        "   Translate English terms: 'whistleblowing' → 'segnalazione illeciti',\n"
        "   'procurement' → 'appalti', 'access to documents' → 'accesso atti'.\n\n"
        "3. USER ASKS 'HOW MANY' / 'WHAT PERCENTAGE' / 'TREND' / 'COMPARISON':\n"
        "   → summarize_court_rulings — aggregates counts by outcome, court, month, etc.\n"
        "   Do NOT use search_court_rulings for counting — it returns individual records.\n\n"
        "CONTEXT LIMITS: All responses are capped to fit LLM context windows.\n"
        "If a ruling text is truncated, the response includes a navigation_hint\n"
        "explaining how to fetch the remaining text.\n\n"
        "Data sources: Normattiva (legislation), OpenGA (court metadata),\n"
        "GA portal (full court decision text). All tools run without user confirmation."
    ),
    version="1.0.0",
    website_url="https://www.normattiva.it",
    icons=[icon],
)

####### TOOLS #######

mcp.tool(meta={"requires_permission": False})(search_legislation)
mcp.tool(meta={"requires_permission": False})(get_legislation_text)
mcp.tool(meta={"requires_permission": False})(search_court_rulings)
mcp.tool(meta={"requires_permission": False})(summarize_court_rulings)
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
