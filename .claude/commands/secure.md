# NeoDemos Security — Skill Reference

Use this skill when hardening the application for deployment, adding authentication, auditing code for vulnerabilities, or managing secrets. For infrastructure setup, service orchestration, and deployment topology, see `/deploy`.

## Current Security Posture

**Known vulnerabilities (as of 2026-04):**

| Issue | Severity | Location | Status |
|-------|----------|----------|--------|
| No authentication on web app | Critical | `main.py` — all routes public | Not implemented |
| Hardcoded DB credentials | High | 50+ files with `postgres:postgres@localhost` | Needs env var migration |
| No CORS middleware | High | `main.py` — no origin restrictions | Not implemented |
| No rate limiting on AI endpoints | High | `/api/analyse/*` costs Gemini credits + CPU | Not implemented |
| Qdrant has no API key | Medium | `config/config.yaml` — no `api_key` set | Not implemented |
| No CSP / security headers | Medium | No middleware or reverse proxy headers | Not implemented |
| SQL via string formatting | Low | `rag_service.py:433` — f-string for method name | Controlled input, low risk |

## 1. Authentication (User Testing Priority)

The web app has **zero authentication**. Anyone with the URL can:
- Access all meeting data
- Trigger AI analysis (burns Gemini API credits)
- Query the full Qdrant corpus

### Option A: Simple Shared Password (Fastest for User Testing)

Use FastAPI's built-in HTTP Basic Auth:

```python
# Add to main.py
import os
import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic Auth credentials against environment variables."""
    correct_user = os.getenv("APP_USERNAME", "neodemos")
    correct_pass = os.getenv("APP_PASSWORD")
    if not correct_pass:
        return  # Auth disabled if no password set (dev mode)
    
    is_user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        correct_user.encode("utf-8"),
    )
    is_pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        correct_pass.encode("utf-8"),
    )
    if not (is_user_ok and is_pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldige inloggegevens",
            headers={"WWW-Authenticate": "Basic"},
        )

# Apply to all routes (add as dependency):
app = FastAPI(title="NeoDemos", lifespan=lifespan, dependencies=[Depends(verify_credentials)])
```

```env
# .env
APP_USERNAME=neodemos
APP_PASSWORD=<generate with: python -c "import secrets; print(secrets.token_urlsafe(16))">
```

When `APP_PASSWORD` is not set, auth is skipped (local dev). When set, every page and API call requires the password.

### Option B: Token-Based API Auth (For MCP SSE endpoint)

For the MCP server running in SSE mode, use a static API key:

```python
# Add to mcp_server.py (for SSE mode)
MCP_API_KEY = os.getenv("MCP_API_KEY")

@mcp.middleware
async def check_api_key(request, call_next):
    if MCP_API_KEY:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != MCP_API_KEY:
            return JSONResponse(status_code=401, content={"error": "Invalid API key"})
    return await call_next(request)
```

```env
MCP_API_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(32))">
```

### Option C: Full Auth System (Future, Production)

For multi-user deployment with roles (councillors, staff, public):
- Use `fastapi-users` or `authlib` with OAuth2/OIDC
- Rotterdam municipality likely has Azure AD / Microsoft Entra ID — use OIDC flow
- Roles: `viewer` (read-only), `analyst` (can trigger AI), `admin` (can configure)

## 2. Secrets Management

### Hardcoded Credentials Inventory

There are **50+ files** with `postgresql://postgres:postgres@localhost:5432/neodemos` hardcoded. These are in:
- **Critical (production-deployed):** `services/rag_service.py`, `services/storage.py`, `pipeline/ingestion.py`, `mcp_server.py`
- **Scripts (dev-only):** `scripts/*.py` — less urgent since these never run in production

### Fix: Centralized Database URL

Create a shared config module:

```python
# config/database.py
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', 'postgres')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
    f"/{os.getenv('DB_NAME', 'neodemos')}"
)

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
```

Then in services:
```python
# services/rag_service.py
from config.database import DATABASE_URL, QDRANT_URL, QDRANT_API_KEY

class RAGService:
    def __init__(self):
        self.db_connection_string = DATABASE_URL
        # ...
        _qdrant_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
```

**Priority order for fixing:**
1. `services/rag_service.py` (line 31) — used by all retrieval
2. `services/storage.py` — used by all data access
3. `mcp_server.py` (line 70) — already uses `os.getenv` with fallback (partially fixed)
4. `pipeline/ingestion.py` — used during embedding runs
5. Scripts — fix with `os.getenv("DB_URL") or "postgresql://..."` pattern (some already do this)

### .env File Safety

`.env` is already in `.gitignore` (verified). Additional checks:

```bash
# Verify .env is not tracked
git ls-files --error-unmatch .env 2>/dev/null && echo "DANGER" || echo "OK"

# Check for secrets accidentally committed in history
git log --all --diff-filter=A -- .env              # Should be empty
git log --all -p -- .env                           # Should be empty
git grep -l "GEMINI_API_KEY=.*[^your_]" HEAD       # Should only match .env.example
```

### Required Production Secrets

```env
# Generate all at once:
python3 -c "
import secrets
print(f'SECRET_KEY={secrets.token_urlsafe(32)}')
print(f'DB_PASSWORD={secrets.token_urlsafe(24)}')
print(f'APP_PASSWORD={secrets.token_urlsafe(16)}')
print(f'QDRANT_API_KEY={secrets.token_urlsafe(32)}')
print(f'MCP_API_KEY={secrets.token_urlsafe(32)}')
"
```

## 3. CORS & Trusted Hosts

Add to `main.py` after app initialization:

```python
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

allowed_hosts = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
allowed_hosts = [h.strip() for h in allowed_hosts]

# Trusted Host — blocks requests with spoofed Host headers
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

# CORS — controls which origins can make API requests
if os.getenv("ENVIRONMENT") == "production":
    allowed_origins = [f"https://{h}" for h in allowed_hosts]
else:
    allowed_origins = ["*"]  # Permissive in development

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
```

## 4. Rate Limiting

The AI analysis endpoints are expensive:
- `/api/analyse/unified/*` — 3 Gemini API calls + RAG retrieval
- `/api/analyse/party-lens/*` — 1 Gemini call + RAG
- `/api/search?deep=true` — 1 Gemini call + RAG

```bash
pip install slowapi
```

```python
# Add to main.py
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Then decorate expensive endpoints:
@app.get("/api/analyse/unified/{agenda_item_id}")
@limiter.limit("10/minute")
async def api_analyse_unified(request: Request, agenda_item_id: str, party: str = "GroenLinks-PvdA"):
    ...

@app.get("/api/search")
@limiter.limit("30/minute")
async def api_search(request: Request, q: str, ...):
    ...
```

## 5. Security Headers

Add via Caddy (preferred) or FastAPI middleware.

### Via Caddyfile (recommended — see /deploy for Caddy setup)

```
yourdomain.com {
    reverse_proxy localhost:8000

    header {
        # Prevent MIME-sniffing
        X-Content-Type-Options "nosniff"
        # Prevent clickjacking
        X-Frame-Options "DENY"
        # Control referrer leaking
        Referrer-Policy "strict-origin-when-cross-origin"
        # Force HTTPS for 1 year
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        # Content Security Policy — restrict script/style sources
        Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        # Hide server identity
        -Server
    }
}
```

### Via FastAPI middleware (fallback if no reverse proxy)

```python
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        )
        return response

app.add_middleware(SecurityHeadersMiddleware)
```

**Note:** The CSP includes `'unsafe-inline'` for scripts/styles because the meeting.html template uses inline `<script>` blocks. After refactoring those to external files, tighten to just `'self'`.

## 6. Database Security

### PostgreSQL

```bash
# Bind to localhost only (docker-compose.prod.yml — see /deploy):
ports:
  - "127.0.0.1:5432:5432"

# Use scram-sha-256 authentication (pg_hba.conf):
# host all all 0.0.0.0/0 scram-sha-256

# Never use default password in production
# POSTGRES_PASSWORD must be set via environment, not defaults
```

### Qdrant

```yaml
# config/config.yaml — add API key:
storage:
  storage_path: ./data/qdrant_storage
  optimizer_config:
    deleted_threshold: 0.2
    vacuum_min_vector_number: 1000
    indexing_threshold: 10000
    flush_interval_sec: 5
service:
  http_port: 6333
  grpc_port: 6334
  api_key: "${QDRANT_API_KEY}"  # Set via environment variable
```

Then update the Python client:
```python
from config.database import QDRANT_URL, QDRANT_API_KEY
_qdrant_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
```

## 7. Input Validation & Injection Prevention

### Current Status

- **SQL injection:** Low risk. Most queries use parameterized `%s` placeholders (psycopg2). One exception at `rag_service.py:433` uses f-string for the tsquery method name, but the value is controlled (enum of `to_tsquery`, `plainto_tsquery`, `websearch_to_tsquery`).
- **XSS:** Jinja2 auto-escapes by default. The `tojson_filter` in `main.py` uses `Markup()` which is safe for JSON in script tags. The `ts_headline` snippets from PostgreSQL use `<b>` tags which are rendered via `innerHTML` — verify the template uses `| safe` only on these trusted fields.
- **Path traversal:** No user-uploaded files. Document content comes from ORI API and iBabs, not user input.

### Recommendations

```python
# Validate query length (prevent abuse of Gemini API with huge prompts)
@app.get("/api/search")
async def api_search(q: str, ...):
    if not q or len(q) < 3:
        return {"results": [], "ai_answer": None}
    if len(q) > 500:  # Add this
        return {"results": [], "ai_answer": None, "error": "Vraag te lang (max 500 tekens)"}

# Validate agenda_item_id format (prevent SQL surprises)
import re
UUID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

@app.get("/api/analyse/unified/{agenda_item_id}")
async def api_analyse_unified(agenda_item_id: str, ...):
    if not UUID_PATTERN.match(agenda_item_id):
        return StreamingResponse(...)  # error
```

## 8. Dependency Auditing

```bash
# Check for known vulnerabilities in Python packages
pip install pip-audit
pip-audit

# Check for outdated packages
pip list --outdated

# Generate a locked requirements file for reproducible deploys
pip freeze > requirements.lock
```

Run `pip-audit` before every deployment. Add to CI if you set up GitHub Actions.

**Key packages to watch:**
- `uvicorn`, `fastapi`, `starlette` — web framework (CVEs occasionally)
- `psycopg2-binary` — database driver
- `qdrant-client` — vector DB client
- `google-genai` — Gemini API (API key in memory)

## 9. Data Privacy (GDPR Considerations)

The app serves municipal council data which is **public by law** (Wet open overheid / Woo). However:

- **Meeting minutes** may contain names of citizens who spoke during public hearings (insprekers)
- **Party profiles** contain political positions — public information
- **Search queries** from users could be sensitive (what a councillor is researching)

**Recommendations:**
- Do NOT log search queries with user identity in production
- Do NOT store user session data beyond the request lifecycle
- The `/health` endpoint should not expose internal service details to unauthenticated users
- If adding user accounts (future), comply with AVG/GDPR: consent, right to deletion, data minimization

## Security Implementation Checklist

Run this before exposing the app to any users:

| # | Item | Priority | Effort |
|---|------|----------|--------|
| 1 | Add HTTP Basic Auth to `main.py` | Critical | 15 min |
| 2 | Set `APP_PASSWORD` in `.env` | Critical | 1 min |
| 3 | Add CORS + TrustedHost middleware | Critical | 10 min |
| 4 | Add query length validation | High | 5 min |
| 5 | Add rate limiting on `/api/analyse/*` | High | 15 min |
| 6 | Create `config/database.py` centralized config | High | 30 min |
| 7 | Update `rag_service.py` to use env-based DB URL | High | 5 min |
| 8 | Set Qdrant API key in `config.yaml` | Medium | 5 min |
| 9 | Add security headers (Caddy or middleware) | Medium | 10 min |
| 10 | Run `pip-audit` | Medium | 5 min |
| 11 | Update `rag_service.py` QdrantClient to use API key | Medium | 5 min |
| 12 | Refactor inline scripts to external files for strict CSP | Low | 1-2 hr |
| 13 | Add `agenda_item_id` format validation | Low | 5 min |

**Minimum for user testing:** Items 1-5 (about 45 minutes of work).