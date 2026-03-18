import os

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

load_dotenv()

from tools import (
    search_legislation,
    get_legislation_text,
    search_court_rulings,
    get_ruling_text,
)

####### AUTH #######

verifier = JWTVerifier(
    public_key=os.getenv("MCP_SERVER_JWT_SECRET"),
    issuer=os.getenv("MCP_SERVER_JWT_ISSUER", ""),
    audience=os.getenv("MCP_SERVER_JWT_AUDIENCE", ""),
    algorithm="HS256",
)

####### MIDDLEWARE #######


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_ips: list[str]):
        super().__init__(app)
        self.allowed_ips = set(allowed_ips)
        self.allow_all = "*" in self.allowed_ips

    async def dispatch(self, request, call_next):
        if self.allow_all:
            return await call_next(request)
        client_ip = request.client.host if request.client else None
        if client_ip not in self.allowed_ips:
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "your_ip": client_ip},
            )
        return await call_next(request)


ALLOWED_IPS = ["*"]  # open for development; restrict in production
middleware = [Middleware(IPAllowlistMiddleware, allowed_ips=ALLOWED_IPS)]

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
    auth=verifier,
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
# Run with: uvicorn server:app --host 0.0.0.0 --port 8000

app = mcp.http_app(middleware=middleware)
