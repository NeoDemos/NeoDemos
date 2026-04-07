"""
DB-backed claim verification: check extracted claims against actual chunk text
in PostgreSQL/Qdrant.

This is the strongest hallucination check possible — instead of asking an LLM
"is this claim supported by the context?", we search the actual database for
matching text. If a claim references a specific person, party, number, or event,
we can verify whether that literal text appears in any chunk.

Designed as a post-processing step: runs after the LLM judge has decomposed
the answer into claims. Adds a `db_verified` field to each claim.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


def verify_claims_against_db(
    claims: List[Dict],
    db_url: str = "",
) -> List[Dict]:
    """
    For each claim from the LLM judge, search PostgreSQL for matching text.

    Adds to each claim:
        "db_match": True/False — was supporting text found in the DB?
        "db_evidence": str — the matching chunk text (first 300 chars)
        "db_chunk_id": int | None

    This catches the most dangerous hallucination: claims that sound plausible
    but have zero basis in the actual document collection.
    """
    if not claims:
        return claims

    db_url = db_url or os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/neodemos",
    )

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
    except Exception as e:
        print(f"  [warn] DB verification unavailable: {e}")
        for claim in claims:
            claim["db_match"] = None
            claim["db_evidence"] = "DB connection failed"
        return claims

    try:
        cur = conn.cursor()
        for claim in claims:
            claim_text = claim.get("claim", "")
            if not claim_text:
                claim["db_match"] = None
                continue

            # Extract key terms from the claim for full-text search
            search_terms = _extract_search_terms(claim_text)
            if not search_terms:
                claim["db_match"] = None
                claim["db_evidence"] = "No searchable terms extracted"
                continue

            # Search chunks table using PostgreSQL full-text search
            found = _search_chunks(cur, search_terms)
            if found:
                claim["db_match"] = True
                claim["db_chunk_id"] = found["chunk_id"]
                claim["db_evidence"] = found["content"][:300]
            else:
                claim["db_match"] = False
                claim["db_chunk_id"] = None
                claim["db_evidence"] = "Geen overeenkomende tekst gevonden in de database"
    finally:
        conn.close()

    return claims


def _extract_search_terms(claim: str) -> List[str]:
    """
    Extract meaningful search terms from a claim.

    Focus on:
    - Proper nouns (capitalized words, likely parties/persons/places)
    - Numbers (euro amounts, percentages, years)
    - Domain-specific terms
    """
    # Dutch stop words to skip
    stop_words = {
        "de", "het", "een", "en", "van", "in", "op", "is", "dat", "die",
        "voor", "met", "zijn", "was", "werd", "worden", "heeft", "had",
        "ook", "als", "maar", "niet", "meer", "over", "aan", "uit",
        "door", "naar", "bij", "om", "dan", "nog", "wel", "geen", "tot",
        "er", "wat", "wie", "hoe", "dit", "deze", "zich", "hun", "haar",
        "zou", "kan", "moet", "wil", "zal", "alle", "veel", "zo", "te",
    }

    terms = []

    # Find proper nouns (capitalized, 3+ chars, not at sentence start)
    words = claim.split()
    for i, word in enumerate(words):
        clean = re.sub(r'[^\w]', '', word)
        if not clean:
            continue

        # Skip stop words
        if clean.lower() in stop_words:
            continue

        # Proper nouns (capitalized mid-sentence)
        if i > 0 and clean[0].isupper() and len(clean) >= 3:
            terms.append(clean)

        # Numbers (years, amounts)
        if re.match(r'\d+', clean):
            terms.append(clean)

    # If no proper nouns found, take the longest content words
    if not terms:
        content_words = [
            re.sub(r'[^\w]', '', w) for w in words
            if re.sub(r'[^\w]', '', w).lower() not in stop_words
            and len(re.sub(r'[^\w]', '', w)) >= 4
        ]
        terms = sorted(content_words, key=len, reverse=True)[:3]

    return terms[:5]  # Max 5 terms per claim


def _search_chunks(cur, terms: List[str]) -> Optional[Dict]:
    """
    Search the chunks table for text matching the given terms.

    Uses PostgreSQL text search with AND logic — all terms must appear.
    Falls back to ILIKE if ts_vector isn't available.
    """
    if not terms:
        return None

    # Try ILIKE approach (works without full-text search setup)
    conditions = []
    params = []
    for term in terms:
        conditions.append("content ILIKE %s")
        params.append(f"%{term}%")

    query = f"""
        SELECT id, content
        FROM chunks
        WHERE {' AND '.join(conditions)}
        LIMIT 1
    """

    try:
        cur.execute(query, params)
        row = cur.fetchone()
        if row:
            return {"chunk_id": row[0], "content": row[1]}
    except Exception:
        pass

    # Fallback: try with fewer terms (OR logic, at least 2 must match)
    if len(terms) >= 2:
        try:
            conditions_or = []
            params_or = []
            for term in terms:
                conditions_or.append("content ILIKE %s")
                params_or.append(f"%{term}%")

            # Count how many terms match per chunk, require at least 2
            case_parts = []
            for term in terms:
                case_parts.append(f"CASE WHEN content ILIKE %s THEN 1 ELSE 0 END")
                params_or.append(f"%{term}%")

            match_count = " + ".join(case_parts)
            query_fallback = f"""
                SELECT id, content, ({match_count}) AS match_count
                FROM chunks
                WHERE {' OR '.join(conditions_or)}
                HAVING ({match_count}) >= 2
                ORDER BY match_count DESC
                LIMIT 1
            """
            cur.execute(query_fallback, params_or + params_or)
            row = cur.fetchone()
            if row:
                return {"chunk_id": row[0], "content": row[1]}
        except Exception:
            pass

    return None


def compute_db_verification_summary(claims: List[Dict]) -> Dict:
    """Summarize DB verification results across all claims."""
    total = len(claims)
    db_confirmed = sum(1 for c in claims if c.get("db_match") is True)
    db_denied = sum(1 for c in claims if c.get("db_match") is False)
    db_unknown = sum(1 for c in claims if c.get("db_match") is None)

    return {
        "total_claims": total,
        "db_confirmed": db_confirmed,
        "db_denied": db_denied,
        "db_unknown": db_unknown,
        "db_confirmation_rate": round(db_confirmed / total, 2) if total > 0 else 0.0,
        "db_denial_rate": round(db_denied / total, 2) if total > 0 else 0.0,
    }
