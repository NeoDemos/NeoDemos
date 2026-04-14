"""API routes — search, analyse, summarize, tokens, MCP token generation.

Includes:
- Keyword + AI search (`/api/search`, `/api/search/limit`, `/api/search/stream`)
- Document summarize (`/api/summarize/{doc_id}`)
- Agenda item analyses (agenda / party-lens / unified / speech)
- User token CRUD (`/api/tokens[...]`)
- MCP auto-installer token generator (`/api/mcp/generate-token`)

Module-level helpers live here rather than in app_state because they are
used only by these routes (IP-based rate limiting, party-lens cache lookup).
"""
import os
import json
import asyncio
import logging
from datetime import date

from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse, JSONResponse

from services.auth_dependencies import (
    auth_service,
    require_login,
    get_api_user,
    get_current_user,
)
from services.policy_lens_evaluation_service import PolicyLensEvaluationService

from app_state import (
    templates,
    storage,
    ai_service,
    web_intel,
    _party_lens_cache,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── MCP Installer token generator (public to logged-in users) ──

_MCP_SERVER_URL = os.getenv("MCP_BASE_URL", "https://mcp.neodemos.nl") + "/mcp"


@router.post("/api/mcp/generate-token")
async def generate_mcp_token(request: Request):
    """Auto-generate an API token for MCP use. Returns JSON with token and config snippets."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Niet ingelogd"}, status_code=401)
    if not user.get("is_active"):
        return JSONResponse({"error": "Account is gedeactiveerd"}, status_code=403)

    # Grant mcp_access if not already set
    if not user.get("mcp_access"):
        auth_service.update_user(user["id"], mcp_access=True)

    # Generate token
    token_data = auth_service.create_api_token(
        user_id=user["id"],
        name="MCP Auto-Install",
        scopes="search,mcp",
    )

    return JSONResponse({
        "token": token_data["token"],
        "mcp_url": _MCP_SERVER_URL,
        "claude_desktop_config": {
            "mcpServers": {
                "neodemos": {
                    "url": _MCP_SERVER_URL,
                    "headers": {
                        "Authorization": f"Bearer {token_data['token']}"
                    }
                }
            }
        },
        "claude_code_command": (
            f"claude mcp add neodemos"
            f" --transport streamable-http"
            f" --url {_MCP_SERVER_URL}"
            f' --header "Authorization: Bearer {token_data["token"]}"'
        ),
    })


# ── /api/search ──

@router.get("/api/search")
async def api_search(request: Request, q: str, deep: bool = False, mode: str = None, party: str = "GroenLinks-PvdA", date_from: str = None, date_to: str = None, user: dict = Depends(get_api_user)):
    """
    Search for agenda items and documents.
    If deep=True, also performs AI Deep Research.
    """
    if not q or len(q) < 3:
        return {"results": [], "ai_answer": None}

    # Temporal extraction: detect date references in natural language
    # Only runs if no explicit date filters were provided by the frontend
    if not date_from and not date_to:
        try:
            temporal = await ai_service.extract_temporal_filters(q)
            if temporal.get("date_from") or temporal.get("date_to"):
                q = temporal["query"]
                date_from = temporal.get("date_from")
                date_to = temporal.get("date_to")
                logger.info(f"Temporal extraction: query='{q}' date_from={date_from} date_to={date_to}")
        except Exception:
            pass  # Non-critical — proceed with original query

    # 1. Traditional Keyword Search
    from psycopg2.extras import RealDictCursor
    def get_keyword_results():
        with storage._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                try:
                    # Optimized search using CTE and deferred headline generation
                    score_threshold=0.15  # Extremely relaxed recall for long queries
                    search_query = """
                    WITH matches AS (
                        -- 1. Trigram match on agenda item name (Highest priority/rank)
                        SELECT ai.id as agenda_item_id, 1.0 as rank, NULL::int as chunk_id
                        FROM agenda_items ai
                        WHERE ai.name ILIKE %s

                        UNION ALL

                        -- 2. FTS match on document chunks
                        SELECT d.agenda_item_id, ts_rank_cd(dc.text_search, websearch_to_tsquery('dutch', %s), 32) as rank, dc.id as chunk_id
                        FROM document_chunks dc
                        JOIN documents d ON dc.document_id = d.id
                        WHERE dc.text_search @@ websearch_to_tsquery('dutch', %s)
                    ),
                    ranked_items AS (
                        -- Pick the best match (highest rank) for each unique agenda item
                        SELECT DISTINCT ON (agenda_item_id)
                            agenda_item_id,
                            rank,
                            chunk_id
                        FROM matches
                        ORDER BY agenda_item_id, rank DESC
                    ),
                    top_items AS (
                        -- Take the top 50 results overall
                        SELECT * FROM ranked_items
                        ORDER BY rank DESC
                        LIMIT 50
                    )
                    SELECT
                        t.agenda_item_id,
                        ai.meeting_id,
                        ai.name,
                        m.start_date as meeting_date,
                        m.committee,
                        CASE
                            WHEN t.chunk_id IS NOT NULL THEN
                                ts_headline('dutch', dc.content, websearch_to_tsquery('dutch', %s),
                                            'StartSel=<b>, StopSel=</b>, MaxWords=35, MinWords=15, ShortWord=3, HighlightAll=FALSE, MaxFragments=1, FragmentDelimiter=" ... "')
                            ELSE (
                                SELECT LEFT(content, 200)
                                FROM documents
                                WHERE agenda_item_id = t.agenda_item_id
                                LIMIT 1
                            )
                        END as snippet,
                        t.rank
                    FROM top_items t
                    JOIN agenda_items ai ON t.agenda_item_id = ai.id
                    JOIN meetings m ON ai.meeting_id = m.id
                    LEFT JOIN document_chunks dc ON t.chunk_id = dc.id
                    ORDER BY t.rank DESC, m.start_date DESC;
                    """
                    # Fallback to a broader search if literal pattern fails
                    search_pattern = f"%{q.split()[0]}%" # Match at least the first word
                    cur.execute(search_query, (search_pattern, q, q, q))
                    results = cur.fetchall()

                    # If still empty, try even broader
                    if not results and len(q.split()) > 1:
                        broad_q = " & ".join(q.split()[:3]) # First 3 words ANDed
                        cur.execute(search_query, (f"%{q.split()[0]}%", broad_q, broad_q, broad_q))
                        results = cur.fetchall()

                    return results
                except Exception as e:
                    logger.error(f"Keyword search failed for query '{q}': {e}")
                    return []

    # Determine if we need to run AI research
    run_ai = deep or mode == 'debate'

    if run_ai:
        # Run both in parallel if AI is requested
        if mode == 'debate':
            ai_task = ai_service.perform_agentic_debate_prep(q, storage, party=party, date_from=date_from, date_to=date_to)
        else:
            ai_task = ai_service.perform_deep_search(q, storage, date_from=date_from, date_to=date_to)

        loop = asyncio.get_running_loop()
        keyword_rows = await loop.run_in_executor(None, get_keyword_results)
        ai_result = await ai_task
    else:
        # Standard fast search
        loop = asyncio.get_running_loop()
        keyword_rows = await loop.run_in_executor(None, get_keyword_results)
        ai_result = {"answer": None, "sources": []}

    results = []
    for r in keyword_rows:
        results.append({
            "agenda_item_id": r['agenda_item_id'],
            "meeting_id": r['meeting_id'],
            "name": r['name'],
            "meeting_date": str(r['meeting_date']).split('T')[0].split(' ')[0] if r['meeting_date'] else 'Onbekend',
            "committee": r['committee'],
            "snippet": r['snippet'] + "..." if r['snippet'] else ""
        })

    return {
        "results": results,
        "ai_answer": ai_result.get("answer"),
        "sources": ai_result.get("sources", [])
    }


# ---------------------------------------------------------------------------
# WS9 — AI Search via Sonnet + MCP tool_use (SSE streaming)
# ---------------------------------------------------------------------------

_AI_SEARCH_MONTHLY_LIMIT_ANON = 3  # anonymous users: 3 AI searches per month


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For from kamal-proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_ai_rate_limit(ip: str) -> dict:
    """
    Check IP-based rate limit for anonymous AI searches.
    Uses PostgreSQL for persistence across restarts.
    Returns {"allowed": bool, "remaining": int, "total": int}.
    """
    from services.db_pool import get_connection
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            # Ensure table exists (idempotent)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ai_search_rate_limits (
                    ip TEXT NOT NULL,
                    month TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (ip, month)
                )
            """)
            month_key = date.today().strftime("%Y-%m")
            cur.execute(
                "SELECT count FROM ai_search_rate_limits WHERE ip = %s AND month = %s",
                (ip, month_key),
            )
            row = cur.fetchone()
            current = row[0] if row else 0
            conn.commit()
            cur.close()
            remaining = max(0, _AI_SEARCH_MONTHLY_LIMIT_ANON - current)
            return {
                "allowed": current < _AI_SEARCH_MONTHLY_LIMIT_ANON,
                "remaining": remaining,
                "total": _AI_SEARCH_MONTHLY_LIMIT_ANON,
            }
    except Exception as e:
        logger.error(f"Rate limit check failed: {e}")
        return {"allowed": True, "remaining": 1, "total": _AI_SEARCH_MONTHLY_LIMIT_ANON}


def _increment_ai_rate_limit(ip: str) -> None:
    """Increment the AI search counter for an IP."""
    from services.db_pool import get_connection
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            month_key = date.today().strftime("%Y-%m")
            cur.execute("""
                INSERT INTO ai_search_rate_limits (ip, month, count)
                VALUES (%s, %s, 1)
                ON CONFLICT (ip, month) DO UPDATE SET count = ai_search_rate_limits.count + 1
            """, (ip, month_key))
            conn.commit()
            cur.close()
    except Exception as e:
        logger.error(f"Rate limit increment failed: {e}")


@router.get("/api/search/limit")
async def api_search_limit(request: Request):
    """Check remaining AI search quota. Logged-in users have unlimited."""
    user = await get_current_user(request)
    if user and user.get("is_active"):
        return {"remaining": -1, "total": -1, "unlimited": True}
    ip = _get_client_ip(request)
    limit = _check_ai_rate_limit(ip)
    return {"remaining": limit["remaining"], "total": limit["total"], "unlimited": False}


@router.get("/api/search/stream")
async def api_search_stream(request: Request, q: str):
    """
    SSE endpoint for AI-powered search with Sonnet + MCP tool_use.
    Anonymous: rate-limited to 3/month. Logged-in: unlimited.
    """
    if not q or len(q) < 3:
        return JSONResponse({"error": "Zoekvraag te kort (minimaal 3 tekens)"}, status_code=400)

    if not web_intel or not web_intel.available:
        return JSONResponse({"error": "AI-zoekservice niet beschikbaar"}, status_code=503)

    # Auth + rate limiting
    user = await get_current_user(request)
    is_authenticated = user and user.get("is_active")
    partij = None

    if not is_authenticated:
        ip = _get_client_ip(request)
        limit = _check_ai_rate_limit(ip)
        if not limit["allowed"]:
            return JSONResponse({
                "error": "Maandelijkse AI-zoeklimiet bereikt",
                "remaining": 0,
                "total": _AI_SEARCH_MONTHLY_LIMIT_ANON,
                "action": "create_account",
            }, status_code=429)
    else:
        ip = None
        # If user has a party preference, pass it to Sonnet
        partij = user.get("party")

    async def event_generator():
        # Increment rate limit for anonymous users at start
        if not is_authenticated and ip:
            _increment_ai_rate_limit(ip)

        async for event in web_intel.stream(q, partij=partij):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── /api/summarize ──

@router.get("/api/summarize/{doc_id}")
async def api_summarize(
    request: Request,
    doc_id: str,
    mode: str = "short",
    user: dict = Depends(get_api_user),
):
    """WS6 — Source-spans-verified per-document summary."""
    from types import SimpleNamespace
    from services.summarizer import Summarizer
    from services.storage_ws6 import (
        get_all_chunks_for_document,
        get_document_summary_cache,
        update_document_summary_columns,
    )

    if mode not in ("short", "long"):
        return JSONResponse({"error": f"Ongeldige mode '{mode}'."}, status_code=400)

    # Cache fast-path for mode='short'.
    if mode == "short":
        cached = get_document_summary_cache(doc_id)
        if cached and cached.get("summary_short"):
            return {
                "document_id": doc_id, "mode": "short",
                "text": cached["summary_short"],
                "verified": bool(cached.get("summary_verified")),
                "cached": True, "computed_at": cached.get("summary_computed_at"),
            }

    # Compute on demand via WS6 Summarizer.
    chunk_rows = get_all_chunks_for_document(doc_id)
    if not chunk_rows:
        return JSONResponse({"error": f"Geen fragmenten voor '{doc_id}'."}, status_code=404)

    chunks = [
        SimpleNamespace(chunk_id=r["chunk_id"], document_id=r["document_id"],
                        title=r.get("title") or "", content=r.get("content") or "")
        for r in chunk_rows
    ]

    summarizer = Summarizer()
    try:
        result = await summarizer.summarize_async(chunks, mode=mode)
    except Exception as e:
        logger.exception(f"Summarizer failed for {doc_id}: {e}")
        return JSONResponse({"error": f"Samenvatten mislukt: {e}"}, status_code=500)

    if not result.text:
        return JSONResponse({"error": "Lege samenvatting."}, status_code=502)

    # Write-through for mode='short'.
    if mode == "short":
        try:
            update_document_summary_columns(
                doc_id, summary_short=result.text, summary_verified=result.verified)
        except Exception:
            pass

    return {
        "document_id": doc_id, "mode": mode, "text": result.text,
        "verified": result.verified, "stripped_count": result.stripped_count,
        "total_sentences": result.total_sentences,
        "citations": [c.chunk_id for c in result.sources],
        "cached": False, "latency_ms": result.latency_ms,
    }


# ── /api/analyse/* ──

@router.get("/api/analyse/agenda/{agenda_item_id}")
async def api_analyse_agenda_item(request: Request, agenda_item_id: str, user: dict = Depends(get_api_user)):
    """
    Perform a general AI analysis of the agenda item.
    """
    logger.info(f"GENERAL AI ANALYSIS ENDPOINT CALLED for agenda item {agenda_item_id}")
    # Fetch agenda item and recursively collect documents from its sub-items
    item_data = storage.get_agenda_item_with_sub_documents(agenda_item_id)
    if not item_data:
        return {"error": f"Agendapunt {agenda_item_id} niet gevonden"}

    item_name = item_data['name']
    doc_rows = item_data['documents']

    if not doc_rows:
        return {
            "summary": f"Geen documenten beschikbaar voor agendapunt: {item_name}",
            "key_points": [],
            "conflicts": [],
            "decision_points": [],
            "controversial_topics": [],
            "questions": [],
            "party_alignment": None
        }

    # Prepare documents for analysis (FULL CONTENT, not truncated)
    documents = [
        {
            "name": row['name'],
            "content": row['content']
        }
        for row in doc_rows
    ]

    analysis = await ai_service.analyze_agenda_item(item_name, documents)
    return analysis


def _get_party_lens_service(party_name: str = "GroenLinks-PvdA"):
    """
    Get or create a PolicyLensEvaluationService for the specified party.
    Caches services to avoid reloading party profiles repeatedly.
    """
    cache_key = party_name.lower()

    if cache_key not in _party_lens_cache:
        service = PolicyLensEvaluationService(party_name=party_name)

        # Load party profile - searches in data/profiles/ directory
        profile_path = f"data/profiles/party_profile_{cache_key.replace(' ', '_').replace('-', '_')}.json"

        # Also try the standard naming convention
        if not os.path.exists(profile_path):
            profile_path = f"data/profiles/party_profile_{cache_key.replace('-', '_')}.json"

        if os.path.exists(profile_path):
            service.load_party_profile(profile_path)
        else:
            logger.warning(f"Party profile not found: {profile_path}")

        _party_lens_cache[cache_key] = service

    return _party_lens_cache[cache_key]


@router.get("/api/analyse/party-lens/{agenda_item_id}")
async def api_analyse_party_lens(request: Request, agenda_item_id: str, party: str = "GroenLinks-PvdA", user: dict = Depends(get_api_user)):
    """
    Analyze an agenda item through a party's perspective.

    Parameters:
    - party: The party to analyze through (default: GroenLinks-PvdA)
    """
    logger.info(f"PARTY LENS ENDPOINT CALLED: party={party}")
    from psycopg2.extras import RealDictCursor
    with storage._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get agenda item and meeting
            cur.execute(
                "SELECT name, meeting_id FROM agenda_items WHERE id = %s",
                (agenda_item_id,)
            )
            item_row = cur.fetchone()
            if not item_row:
                return {
                    "error": f"Agendapunt {agenda_item_id} niet gevonden",
                    "alignment_score": None
                }

            item_name = item_row['name']
            meeting_id = item_row['meeting_id']

            # Get meeting name
            cur.execute("SELECT name FROM meetings WHERE id = %s", (meeting_id,))
            meeting_row = cur.fetchone()
            meeting_name = meeting_row['name'] if meeting_row else "Onbekende vergadering"

            # Get all documents for this agenda item
            cur.execute(
                "SELECT name, content FROM documents WHERE agenda_item_id = %s AND content IS NOT NULL ORDER BY id",
                (agenda_item_id,)
            )
            doc_rows = cur.fetchall()

    if not doc_rows:
        return {
            "error": f"Geen documenten beschikbaar voor agendapunt: {item_name}",
            "alignment_score": None
        }

    seen_doc_ids = set()
    documents = []
    for row in doc_rows:
        unique_key = (row.get('name'), row.get('url'))
        if unique_key not in seen_doc_ids:
            documents.append({
                "name": row['name'],
                "content": row['content'],
                "url": row.get('url', '#')
            })
            seen_doc_ids.add(unique_key)

    # Use party lens service for through-party-lens analysis
    lens_service = _get_party_lens_service(party)

    # Combine documents into agenda item text
    agenda_text = f"{meeting_name} - {item_name}\n\n"
    for doc in documents:
        agenda_text += f"Document: {doc['name']}\n{doc['content']}\n\n"

    # Evaluate through the party's lens
    result = lens_service.evaluate_agenda_item(agenda_text)

    if result.get('analyse'):
        analysis = result['analyse']
        return {
            "agenda_item_id": agenda_item_id,
            "agenda_item_name": item_name,
            "meeting_name": meeting_name,
            "party": party,
            "alignment_score": analysis.get('afstemming_score', 0.5),
            "analysis": analysis.get('gedetailleerde_analyse', ''),
            "positieve_punten": analysis.get('positieve_punten', []),
            "kritische_punten": analysis.get('kritische_punten', []),
            "vraag_suggesties": analysis.get('vraag_suggesties', []),
            "tegenvoorstel_suggesties": analysis.get('tegenvoorstel_suggesties', []),
            "recommendations": result.get('aanbevelingen', []),
            "source": "party_lens_analysis"
        }

    return {
        "error": f"Partijlensanalyse leverde geen resultaat op voor agendapunt: {item_name}",
        "alignment_score": None
    }


@router.get("/api/analyse/unified/{agenda_item_id}")
async def api_analyse_unified(request: Request, agenda_item_id: str, party: str = "GroenLinks-PvdA", user: dict = Depends(get_api_user)):
    """
    Streams the agentic meeting analysis as Server-Sent Events (SSE).
    Events: {type: "status"|"chunk"|"done"|"error", ...}
    """
    logger.info(f"UNIFIED ANALYSIS ENDPOINT CALLED for agenda item {agenda_item_id}, party {party}")
    item_data = storage.get_agenda_item_with_sub_documents(agenda_item_id)
    if not item_data:
        async def _not_found():
            yield f"data: {json.dumps({'type': 'error', 'message': f'Agendapunt {agenda_item_id} niet gevonden'})}\n\n"
        return StreamingResponse(_not_found(), media_type="text/event-stream")

    item_name = item_data['name']
    doc_rows = item_data['documents']

    seen_doc_ids = set()
    documents = []
    for row in doc_rows:
        unique_key = (row.get('name'), row.get('url'))
        if unique_key not in seen_doc_ids:
            documents.append({
                "name": row['name'],
                "content": row['content'],
                "url": row.get('url', '#')
            })
            seen_doc_ids.add(unique_key)

    async def event_stream():
        try:
            async for event in ai_service.stream_agentic_meeting_analysis(item_name, documents, party=party):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"Streaming unified analysis error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/analyse/speech/{agenda_item_id}")
async def api_analyse_speech(request: Request, agenda_item_id: str, party: str = "GroenLinks-PvdA", user: dict = Depends(get_api_user)):
    """
    Generate a draft speech (bijdrage) for a councillor based on the agenda item analysis.
    """
    logger.info(f"SPEECH GENERATION ENDPOINT CALLED for agenda item {agenda_item_id}, party {party}")
    from psycopg2.extras import RealDictCursor
    with storage._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT name, meeting_id FROM agenda_items WHERE id = %s", (agenda_item_id,))
            item_row = cur.fetchone()
            if not item_row: return {"error": "Agendapunt niet gevonden"}
            item_name = item_row['name']

            cur.execute("SELECT name, content FROM documents WHERE agenda_item_id = %s AND content IS NOT NULL", (agenda_item_id,))
            doc_rows = cur.fetchall()

    if not doc_rows: return {"error": "Geen documenten beschikbaar"}

    documents = [{"name": r['name'], "content": r['content']} for r in doc_rows]
    lens_service = _get_party_lens_service(party)

    # We use the existing analysis but request a speech format
    speech = await ai_service.generate_speech_draft(item_name, documents, lens_service.party_vision)
    return {"speech": speech}


# ── Token management (for logged-in users) ──

@router.get("/api/tokens")
async def list_tokens(request: Request, user: dict = Depends(require_login)):
    tokens = auth_service.list_user_tokens(user["id"])
    return JSONResponse(tokens)


@router.post("/api/tokens")
async def create_token(request: Request, user: dict = Depends(require_login)):
    body = await request.json()
    name = body.get("name", "Default")[:50]
    scopes = body.get("scopes", "search,mcp")
    token_data = auth_service.create_api_token(user["id"], name=name, scopes=scopes)
    return JSONResponse(token_data)


@router.delete("/api/tokens/{token_id}")
async def revoke_token(request: Request, token_id: int, user: dict = Depends(require_login)):
    # Verify the token belongs to this user
    tokens = auth_service.list_user_tokens(user["id"])
    if not any(t["id"] == token_id for t in tokens):
        return JSONResponse({"error": "Token niet gevonden"}, status_code=404)
    auth_service.revoke_api_token(token_id)
    return JSONResponse({"ok": True})
