# Italian Legal Research MCP Server

An MCP server that gives AI assistants real-time access to Italian legislation and administrative court rulings. Built for public sector caseworkers, administrators, HR officers, and legal staff who need to look up laws and court decisions as part of their daily work.

Connects to:
- **[Normattiva](https://www.normattiva.it)** — the official Italian legislation portal (all acts, decrees, codes)
- **[OpenGA](https://openga.giustizia-amministrativa.it)** — metadata for all TAR and Consiglio di Stato rulings
- **[GA Portal](https://www.giustizia-amministrativa.it)** — full text of administrative court decisions

## Tools

| Tool | What it does |
|------|-------------|
| `search_legislation` | Find a law by citation (`d.lgs. 33/2013`) or keywords (`trasparenza appalti`) |
| `get_legislation_text` | Read the actual text of a law — with article-level navigation for large codes |
| `search_court_rulings` | Search TAR and CdS rulings by topic, court, and year — returns structured metadata |
| `summarize_court_rulings` | Aggregate rulings by outcome, court, month, or type — answers "how many / what %" questions |
| `get_ruling_text` | Fetch the full text of a specific ruling from the GA portal |
| `search_rulings_fulltext` | Topic-based search with full reasoning text extracted |

## Deploy on Railway

### 1. Fork this repository

Click **Fork** on GitHub.

### 2. Create a new Railway project

1. Go to [railway.app](https://railway.app) and create a new project
2. Choose **Deploy from GitHub repo** and select your fork
3. In the Railway dashboard, go to **Settings → Source** and connect the environment to the `main` branch
4. Railway will auto-detect Python and deploy using the `Procfile`

### 3. Get your server URL

Once deployed, Railway assigns a URL like `https://your-app.up.railway.app`. Your MCP endpoint is:

```
https://your-app.up.railway.app/mcp
```

No environment variables are required.

## Connect to Intric

1. In your Intric workspace, go to **Assistants → Tools → Add MCP Server**
2. Paste your Railway URL: `https://your-app.up.railway.app/mcp`
3. The server will appear with the Normattiva icon and all 6 tools auto-registered
4. All tools run automatically — no confirmation prompts

## Run Locally

```bash
# Clone and install
git clone https://github.com/SimonBerg255/gov-it-legal-mcp.git
cd gov-it-legal-mcp

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run (requires Python 3.10+)
uvicorn server:app --host 0.0.0.0 --port 8000
```

MCP endpoint: `http://localhost:8000/mcp`
Health check: `http://localhost:8000/health`

## Data Sources

### Normattiva
Italy's official legislation portal. The server fetches Akoma Ntoso XML directly and converts it to clean Markdown. Supports direct citation parsing (no search needed for known acts) and full-text search.

Known acts with instant lookup (no search needed): Codice Appalti (D.Lgs. 36/2023), GDPR (D.Lgs. 196/2003), Codice PA (D.Lgs. 82/2005), Trasparenza (D.Lgs. 33/2013), Anticorruzione (L. 190/2012), and 20+ more.

### OpenGA
Official open data from the Italian administrative justice system. Contains metadata for all TAR and Consiglio di Stato sentenze. Covers 26 courts, updated annually. Searched by OGGETTO_RICORSO (subject description).

**Note:** Keyword queries must be in Italian legal terminology. Examples:
- `accesso atti` (not "access to documents")
- `appalti pubblici` (not "public procurement")
- `segnalazione illeciti` (not "whistleblowing")

### GA Portal — Full Text
Full ruling text served from `mdp.giustizia-amministrativa.it`. No authentication required. The server constructs direct URLs from OpenGA metadata (schema + NRG number) and extracts fatto/diritto and dispositivo (P.Q.M.) sections.

## Context Safety

All tools enforce hard response caps to prevent LLM context overflow:

- Legislation: 25K chars default, with TOC navigation for large codes
- Ruling text: 20K chars default for fatto/diritto, full dispositivo always included
- Search results: max 20 rulings per call
- Aggregations: max 2,000 rulings sampled with clear coverage warnings

## Architecture

```
server.py          FastMCP server, tool registration, /health route
tools.py           All 6 tool implementations + helper functions
requirements.txt   Python dependencies
Procfile           Railway start command
.python-version    Pins Python 3.11+ for Railway
```

No database, no cache persistence, no authentication. OpenGA JSON files are cached in memory per server instance (cleared on redeploy).

## License

MIT — see [LICENSE](LICENSE)
