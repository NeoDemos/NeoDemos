"""
DB Consistency Checks for Virtual Notulen

Verifies that the staging transcript is consistent with:
  1. Production public.meetings — correct committee, date, name
  2. Known council members — speakers and parties are recognizable
  3. Production chunks for the same committee — entity cross-reference
  4. Qdrant staging collection — correct metadata injected at ingestion

All functions return dicts safe for JSON serialization.
"""

from __future__ import annotations

import re
from typing import Dict, List


# ── Meeting metadata consistency ──────────────────────────────────────────────

def check_meeting_metadata_consistency(meeting_id: str, db_url: str) -> Dict:
    """
    Compare staging.meetings with public.meetings for the same meeting_id.

    Checks: name, start_date, committee.
    These should match (staging enriches, never contradicts).
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Staging row
        cur.execute("""
            SELECT name, start_date, committee, review_status, quality_score, transcript_source
            FROM staging.meetings
            WHERE id = %s
        """, (meeting_id,))
        staging_row = cur.fetchone()

        if not staging_row:
            cur.close()
            conn.close()
            return {"status": "not_found", "message": f"Meeting {meeting_id} not found in staging"}

        staging = {
            "name": staging_row["name"],
            "start_date": str(staging_row["start_date"]) if staging_row["start_date"] else None,
            "committee": str(staging_row["committee"]) if staging_row["committee"] else None,
            "review_status": staging_row["review_status"],
            "quality_score": staging_row["quality_score"],
            "transcript_source": staging_row["transcript_source"],
        }

        # Production row
        cur.execute("""
            SELECT name, start_date, committee
            FROM public.meetings
            WHERE id = %s
        """, (meeting_id,))
        prod_row = cur.fetchone()

        cur.close()
        conn.close()

        production = None
        issues = []

        if prod_row:
            production = {
                "name": prod_row["name"],
                "start_date": str(prod_row["start_date"]) if prod_row["start_date"] else None,
                "committee": str(prod_row["committee"]) if prod_row["committee"] else None,
            }
            # Name match (case-insensitive)
            if staging["name"] and production["name"]:
                if staging["name"].lower() != production["name"].lower():
                    issues.append(
                        f"Name mismatch: staging='{staging['name']}' prod='{production['name']}'"
                    )
            # Date match (compare first 10 chars = YYYY-MM-DD)
            if staging["start_date"] and production["start_date"]:
                if staging["start_date"][:10] != production["start_date"][:10]:
                    issues.append(
                        f"Date mismatch: staging={staging['start_date'][:10]} "
                        f"prod={production['start_date'][:10]}"
                    )
            # Committee match
            if staging["committee"] and production["committee"]:
                if staging["committee"] != production["committee"]:
                    issues.append(
                        f"Committee mismatch: staging={staging['committee']} "
                        f"prod={production['committee']}"
                    )
        else:
            issues.append("Meeting not found in public.meetings — will be inserted on promotion")

        return {
            "status": "ok" if not issues else "issues_found",
            "staging": staging,
            "production": production,
            "issues": issues,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Speaker presence check ────────────────────────────────────────────────────

_ROLE_KEYWORDS = {
    "voorzitter", "burgemeester", "wethouder", "griffier", "commissiegriffier",
    "secretaris", "directeur", "ambtenaar", "portefeuillehouder",
    # Committee-only members (burgercommissieleden) represent parties in committees
    # but are not formal city councillors — still valid recognized participants
    "burgercommissielid", "commissielid",
    # Other common non-councillor roles
    "adviseur", "inspreker",
}


def check_speaker_presence(segments: List[Dict], dictionary: Dict) -> Dict:
    """
    Check whether transcript speakers are recognizable as known council members,
    officials, or named roles.

    Unrecognized non-role speakers are flagged — they may be transcription errors
    or speakers misidentified by the diarization system.
    """
    if not segments or not dictionary:
        return {}

    known_surnames = {s.lower() for s in dictionary.get("council_members", {}).get("surnames", [])}
    known_parties = {p.lower() for p in dictionary.get("parties", [])}

    # Build per-speaker stats
    speaker_stats: Dict[str, Dict] = {}
    for seg in segments:
        speaker = (seg.get("speaker") or "").strip()
        party = (seg.get("party") or "").strip()
        if not speaker or speaker.lower() in {"", "spreker onbekend", "unknown", "onbekend"}:
            continue
        if speaker not in speaker_stats:
            speaker_stats[speaker] = {"count": 0, "parties": set()}
        speaker_stats[speaker]["count"] += 1
        if party:
            speaker_stats[speaker]["parties"].add(party)

    recognized = []
    unrecognized = []

    for name, info in speaker_stats.items():
        name_parts = name.lower().split()
        is_known_member = any(part in known_surnames for part in name_parts)
        is_role = any(kw in name.lower() for kw in _ROLE_KEYWORDS)

        entry = {
            "name": name,
            "segment_count": info["count"],
            "parties": sorted(info["parties"]),
        }

        if is_known_member:
            entry["recognized_as"] = "council_member"
            recognized.append(entry)
        elif is_role:
            entry["recognized_as"] = "role"
            recognized.append(entry)
        else:
            # Validate party field as a fallback
            parties_lower = {p.lower() for p in info["parties"]}
            if parties_lower & known_parties:
                entry["recognized_as"] = "party_member_unverified_name"
                recognized.append(entry)
            else:
                entry["recognized_as"] = "unknown"
                unrecognized.append(entry)

    total = len(speaker_stats)
    return {
        "total_unique_speakers": total,
        "recognized_speakers": len(recognized),
        "unrecognized_speakers": len(unrecognized),
        "recognition_rate": round(len(recognized) / total, 4) if total > 0 else 0.0,
        "recognized": recognized,
        "unrecognized": unrecognized,
    }


# ── Entity cross-reference with production ────────────────────────────────────

def check_entity_consistency_with_production(
    meeting_id: str,
    chunks_sample: List[Dict],
    db_url: str,
    top_n_production_chunks: int = 50,
) -> Dict:
    """
    Cross-reference capitalized named entities in staging chunks with recent
    production chunks for the same committee.

    High overlap = staging entities are consistent with production knowledge.
    Staging-only entities that look like proper nouns = worth reviewing.
    """
    if not chunks_sample:
        return {"status": "skipped", "message": "No staging chunks provided"}

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        # Get committee_id from staging
        cur.execute("SELECT committee FROM staging.meetings WHERE id = %s", (meeting_id,))
        row = cur.fetchone()
        committee_id = row[0] if row else None

        if not committee_id:
            cur.close()
            conn.close()
            return {"status": "skipped", "message": "No committee found in staging meeting"}

        # Fetch recent production chunks for the same committee
        cur.execute("""
            SELECT dc.content
            FROM public.document_chunks dc
            JOIN public.documents d ON dc.document_id = d.id
            JOIN public.meetings m ON d.meeting_id = m.id
            WHERE m.committee = %s
              AND dc.content IS NOT NULL
              AND length(dc.content) > 100
            ORDER BY m.start_date DESC NULLS LAST
            LIMIT %s
        """, (str(committee_id), top_n_production_chunks))
        prod_chunks = [r[0] for r in cur.fetchall()]

        cur.close()
        conn.close()

        if not prod_chunks:
            return {"status": "skipped", "message": "No production chunks found for this committee"}

        # Dutch function words to exclude from entity comparison
        dutch_function_words = {
            "het", "een", "van", "voor", "maar", "ook", "met", "door", "bij",
            "zijn", "haar", "hun", "hen", "dat", "die", "dit", "deze", "naar",
            "dan", "toch", "nog", "wel", "niet", "meer", "zeer", "veel", "alle",
            "over", "aan", "uit", "tot", "als", "bij", "per", "dan", "zo",
        }

        # Extract capitalized words (proxy for named entities) from each side
        def extract_entities(text: str) -> set:
            return {
                w for w in re.findall(r'\b[A-Z][a-z]{2,}\b', text)
                if w.lower() not in dutch_function_words
            }

        staging_text = " ".join(c.get("content", "") for c in chunks_sample[:30])
        prod_text = " ".join(prod_chunks[:30])

        staging_entities = extract_entities(staging_text)
        prod_entities = extract_entities(prod_text)

        overlap = staging_entities & prod_entities
        staging_only = staging_entities - prod_entities

        # Staging-only entities that look genuinely suspicious
        # (longer names, not obviously Dutch common words)
        suspicious = sorted(
            e for e in staging_only
            if len(e) >= 4
            and e not in {
                "Maar", "Voor", "Naar", "Door", "Over", "Alle", "Veel",
                "Heeft", "Wordt", "Waren", "Zijn", "Werd", "Zal",
            }
        )

        return {
            "status": "ok",
            "committee_id": str(committee_id),
            "staging_entities_count": len(staging_entities),
            "production_entities_count": len(prod_entities),
            "shared_entities_count": len(overlap),
            "overlap_rate": round(len(overlap) / len(staging_entities), 4) if staging_entities else 0.0,
            "staging_only_entities_count": len(staging_only),
            "suspicious_staging_only": suspicious[:15],
            "note": "Low overlap may be normal for new meetings; suspicious_staging_only warrants review",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Qdrant indexing quality ───────────────────────────────────────────────────

def check_qdrant_indexing_quality(
    meeting_id: str,
    staging_collection: str,
    qdrant_url: str,
    scroll_limit: int = 200,
) -> Dict:
    """
    Scroll the staging Qdrant collection for this meeting_id and verify:
      - Points exist
      - doc_type = "virtual_notulen"
      - is_virtual_notulen = True
      - start_date is populated
      - committee is populated

    Returns metadata completeness stats and a list of issues.
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        qdrant = QdrantClient(url=qdrant_url, timeout=30)

        filter_obj = Filter(
            must=[FieldCondition(key="meeting_id", match=MatchValue(value=meeting_id))]
        )

        result, _ = qdrant.scroll(
            collection_name=staging_collection,
            scroll_filter=filter_obj,
            limit=scroll_limit,
            with_payload=True,
            with_vectors=False,
        )
        points = result

        if not points:
            return {
                "status": "not_found",
                "message": f"No points found in '{staging_collection}' for meeting_id={meeting_id}",
            }

        total = len(points)
        stats = {
            "total_points": total,
            "with_doc_type_virtual": 0,
            "with_is_virtual_flag": 0,
            "with_start_date": 0,
            "with_committee": 0,
            "with_content": 0,
            "with_meeting_id": 0,
        }

        for p in points:
            payload = p.payload or {}
            if payload.get("doc_type") == "virtual_notulen":
                stats["with_doc_type_virtual"] += 1
            if payload.get("is_virtual_notulen") is True:
                stats["with_is_virtual_flag"] += 1
            if payload.get("start_date"):
                stats["with_start_date"] += 1
            if payload.get("committee"):
                stats["with_committee"] += 1
            if payload.get("content"):
                stats["with_content"] += 1
            if payload.get("meeting_id"):
                stats["with_meeting_id"] += 1

        issues = []
        if stats["with_doc_type_virtual"] < total:
            issues.append(
                f"{total - stats['with_doc_type_virtual']} points missing doc_type='virtual_notulen'"
            )
        if stats["with_is_virtual_flag"] < total:
            issues.append(
                f"{total - stats['with_is_virtual_flag']} points missing is_virtual_notulen=True"
            )
        if stats["with_start_date"] < int(total * 0.9):
            issues.append(
                f"Only {stats['with_start_date']}/{total} points have start_date (expected ≥90%)"
            )
        if stats["with_committee"] < int(total * 0.9):
            issues.append(
                f"Only {stats['with_committee']}/{total} points have committee (expected ≥90%)"
            )
        if stats["with_content"] < total:
            issues.append(f"{total - stats['with_content']} points missing content")

        return {
            "status": "ok" if not issues else "issues_found",
            "metadata_stats": stats,
            "issues": issues,
            "note": f"Scrolled up to {scroll_limit} points; collection may have more",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Chunk count consistency ───────────────────────────────────────────────────

def check_chunk_count_consistency(meeting_id: str, db_url: str, qdrant_url: str,
                                   staging_collection: str) -> Dict:
    """
    Compare chunk count in staging.document_chunks vs. Qdrant staging collection.
    A large discrepancy indicates ingestion errors (embedding failures or DB-only writes).
    """
    pg_count = None
    qdrant_count = None

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) AS cnt FROM staging.document_chunks dc
            JOIN staging.documents d ON dc.document_id = d.id
            WHERE d.meeting_id = %s
        """, (meeting_id,))
        pg_count = cur.fetchone()["cnt"]
        cur.close()
        conn.close()
    except Exception as e:
        return {"status": "error", "message": f"PostgreSQL error: {e}"}

    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qdrant = QdrantClient(url=qdrant_url, timeout=30)
        count_result = qdrant.count(
            collection_name=staging_collection,
            count_filter=Filter(
                must=[FieldCondition(key="meeting_id", match=MatchValue(value=meeting_id))]
            ),
        )
        qdrant_count = count_result.count
    except Exception as e:
        qdrant_count = None

    if pg_count is None or qdrant_count is None:
        return {
            "status": "partial",
            "pg_chunks": pg_count,
            "qdrant_points": qdrant_count,
        }

    discrepancy = abs(pg_count - qdrant_count)
    discrepancy_rate = round(discrepancy / pg_count, 4) if pg_count > 0 else 0.0

    issues = []
    if discrepancy_rate > 0.05:
        issues.append(
            f"Large discrepancy: {pg_count} PG chunks vs {qdrant_count} Qdrant points "
            f"({discrepancy_rate:.1%} gap)"
        )

    return {
        "status": "ok" if not issues else "issues_found",
        "pg_chunks": pg_count,
        "qdrant_points": qdrant_count,
        "discrepancy": discrepancy,
        "discrepancy_rate": discrepancy_rate,
        "issues": issues,
    }
