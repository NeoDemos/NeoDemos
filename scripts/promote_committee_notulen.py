#!/usr/bin/env python3
"""
Review & Promotion Tool for Committee Meeting Virtual Notulen
==============================================================

CLI tool to review staging data and promote approved transcripts
to the production PostgreSQL schema and Qdrant collection.

Audit-first architecture: staging only has chunks in PostgreSQL (no vectors).
Embedding happens at promotion time using LocalAIService.

Usage:
    python scripts/promote_committee_notulen.py --list
    python scripts/promote_committee_notulen.py --preview <meeting_id>
    python scripts/promote_committee_notulen.py --approve <meeting_id>
    python scripts/promote_committee_notulen.py --approve-batch --min-score 0.7
    python scripts/promote_committee_notulen.py --reject <meeting_id> --reason "poor audio"
    python scripts/promote_committee_notulen.py --stats
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

def _build_db_url():
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    user = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "neodemos")
    return f"postgresql://{user}:{pw}@{host}:{port}/{name}"

DB_URL = _build_db_url()
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
STAGING_COLLECTION = "committee_transcripts_staging"
PRODUCTION_COLLECTION = "notulen_chunks"

logger = logging.getLogger(__name__)


def _pg_row(row: dict) -> dict:
    """Serialize any dict/list values to JSON strings for psycopg2."""
    return {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()}


def get_staging_connection():
    """Get a connection with search_path set to staging."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SET search_path TO staging, public")
    cur.close()
    return conn


def get_production_connection():
    """Get a connection with default search_path (public)."""
    return psycopg2.connect(DB_URL)


# ── List ─────────────────────────────────────────────────────────────────

def cmd_list(args):
    """List all staging meetings with quality scores."""
    conn = get_staging_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    status_filter = ""
    params = []
    if args.status:
        status_filter = "WHERE m.review_status = %s"
        params.append(args.status)

    cur.execute(f"""
        SELECT m.id, m.name, m.committee, m.start_date,
               m.transcript_source, m.quality_score, m.review_status, m.promoted_at,
               (SELECT COUNT(*) FROM documents d WHERE d.meeting_id = m.id) as doc_count,
               (SELECT COUNT(*) FROM document_chunks dc
                JOIN documents d ON dc.document_id = d.id
                WHERE d.meeting_id = m.id) as chunk_count
        FROM meetings m
        {status_filter}
        ORDER BY m.start_date DESC
    """, params)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print("No meetings in staging.")
        return

    # Print table
    print(f"\n{'Meeting':<50} {'Date':<12} {'Source':<8} {'Score':<7} {'Status':<15} {'Chunks':<7}")
    print("-" * 100)
    for r in rows:
        name = (r["name"] or "")[:48]
        date = str(r["start_date"])[:10] if r["start_date"] else "N/A"
        source = r["transcript_source"] or "?"
        score = f"{r['quality_score']:.3f}" if r["quality_score"] is not None else "N/A"
        status = r["review_status"] or "?"
        chunks = r["chunk_count"] or 0

        # Color code status
        if status == "auto_approved":
            status_display = f"\033[92m{status}\033[0m"
        elif status == "auto_rejected":
            status_display = f"\033[91m{status}\033[0m"
        elif status == "pending":
            status_display = f"\033[93m{status}\033[0m"
        else:
            status_display = status

        print(f"{name:<50} {date:<12} {source:<8} {score:<7} {status_display:<24} {chunks:<7}")

    print(f"\nTotal: {len(rows)} meetings in staging")

    # Summary
    by_status = {}
    for r in rows:
        s = r["review_status"] or "unknown"
        by_status[s] = by_status.get(s, 0) + 1
    print(f"By status: {', '.join(f'{k}: {v}' for k, v in sorted(by_status.items()))}")


# ── Preview ──────────────────────────────────────────────────────────────

def cmd_preview(args):
    """Preview a staging meeting's transcript."""
    conn = get_staging_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Get meeting info
    cur.execute("SELECT * FROM meetings WHERE id = %s", (args.preview,))
    meeting = cur.fetchone()
    if not meeting:
        print(f"Meeting {args.preview} not found in staging.")
        return

    print(f"\n{'='*60}")
    print(f"Meeting: {meeting['name']}")
    print(f"Date:    {meeting['start_date']}")
    print(f"Source:  {meeting['transcript_source']}")
    print(f"Score:   {meeting['quality_score']}")
    print(f"Status:  {meeting['review_status']}")
    print(f"{'='*60}")

    # Get quality metrics from pipeline log
    cur.execute("""
        SELECT quality_metrics FROM pipeline_meeting_log
        WHERE meeting_id = %s ORDER BY completed_at DESC LIMIT 1
    """, (args.preview,))
    log = cur.fetchone()
    if log and log["quality_metrics"]:
        metrics = log["quality_metrics"] if isinstance(log["quality_metrics"], dict) else json.loads(log["quality_metrics"])
        print(f"\nQuality Breakdown:")
        for k, v in metrics.get("metrics", metrics).items():
            if k != "score":
                print(f"  {k}: {v}")

    # Get first few chunks as preview
    cur.execute("""
        SELECT dc.title, dc.content, dc.chunk_type
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE d.meeting_id = %s
        ORDER BY dc.chunk_index
        LIMIT 5
    """, (args.preview,))
    chunks = cur.fetchall()

    if chunks:
        print(f"\nFirst {len(chunks)} chunks:")
        for i, c in enumerate(chunks, 1):
            print(f"\n  [{i}] {c['title'] or 'Untitled'} ({c['chunk_type']})")
            text = c["content"][:300]
            print(f"      {text}{'...' if len(c['content']) > 300 else ''}")

    cur.close()
    conn.close()


# ── Approve / Promote ────────────────────────────────────────────────────

def promote_meeting(meeting_id: str) -> bool:
    """Promote a single meeting from staging to production."""
    staging_conn = get_staging_connection()
    prod_conn = get_production_connection()

    try:
        s_cur = staging_conn.cursor(cursor_factory=RealDictCursor)
        p_cur = prod_conn.cursor()

        # 1. Check meeting exists in staging
        s_cur.execute("SELECT * FROM meetings WHERE id = %s", (meeting_id,))
        meeting = s_cur.fetchone()
        if not meeting:
            print(f"Meeting {meeting_id} not found in staging.")
            return False

        # 2. Upsert meeting into production (preserve existing data)
        p_cur.execute("""
            INSERT INTO meetings (id, name, start_date, committee, location, organization_id, category)
            VALUES (%(id)s, %(name)s, %(start_date)s, %(committee)s, %(location)s, %(organization_id)s, %(category)s)
            ON CONFLICT (id) DO UPDATE SET
                category = COALESCE(EXCLUDED.category, meetings.category)
        """, _pg_row(dict(meeting)))
        print(f"  + Meeting record upserted")

        # 3. Copy documents
        s_cur.execute("SELECT * FROM documents WHERE meeting_id = %s", (meeting_id,))
        docs = s_cur.fetchall()
        for doc in docs:
            p_cur.execute("""
                INSERT INTO documents (id, name, meeting_id, content, category)
                VALUES (%(id)s, %(name)s, %(meeting_id)s, %(content)s, %(category)s)
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    category = EXCLUDED.category
            """, dict(doc))
        print(f"  + {len(docs)} documents copied")

        # 4. Copy document_children
        doc_ids = [d["id"] for d in docs]
        if doc_ids:
            s_cur.execute("""
                SELECT * FROM document_children WHERE document_id = ANY(%s)
            """, (doc_ids,))
            children = s_cur.fetchall()

            # Delete existing production children first to prevent duplicates on re-promotion.
            # (document_children has no unique constraint — chunks reference children by serial id.)
            p_cur.execute("DELETE FROM document_children WHERE document_id = ANY(%s)", (doc_ids,))

            # Map old staging child IDs to new production IDs
            child_id_map = {}
            for child in children:
                p_cur.execute("""
                    INSERT INTO document_children (document_id, content, chunk_index, metadata)
                    VALUES (%(document_id)s, %(content)s, %(chunk_index)s, %(metadata)s)
                    RETURNING id
                """, _pg_row(dict(child)))
                new_id = p_cur.fetchone()[0]
                child_id_map[child["id"]] = new_id
            print(f"  + {len(children)} document_children copied")

            # 5. Copy document_chunks (with remapped child_ids)
            s_cur.execute("""
                SELECT * FROM document_chunks WHERE document_id = ANY(%s)
            """, (doc_ids,))
            chunks = s_cur.fetchall()
            for chunk in chunks:
                chunk_dict = dict(chunk)
                # Remap child_id
                old_child_id = chunk_dict.get("child_id")
                chunk_dict["child_id"] = child_id_map.get(old_child_id, old_child_id)
                p_cur.execute("""
                    INSERT INTO document_chunks (document_id, chunk_index, title, content, chunk_type,
                                                  table_json, tokens_estimated, child_id)
                    VALUES (%(document_id)s, %(chunk_index)s, %(title)s, %(content)s, %(chunk_type)s,
                            %(table_json)s, %(tokens_estimated)s, %(child_id)s)
                    ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                        content = EXCLUDED.content, title = EXCLUDED.title, child_id = EXCLUDED.child_id
                """, _pg_row(chunk_dict))
            print(f"  + {len(chunks)} document_chunks copied")

            # 6. Copy document_assignments
            s_cur.execute("""
                SELECT * FROM document_assignments WHERE document_id = ANY(%s)
            """, (doc_ids,))
            assignments = s_cur.fetchall()
            for a in assignments:
                p_cur.execute("""
                    INSERT INTO document_assignments (document_id, meeting_id, agenda_item_id)
                    VALUES (%(document_id)s, %(meeting_id)s, %(agenda_item_id)s)
                    ON CONFLICT (document_id, meeting_id, agenda_item_id) DO NOTHING
                """, dict(a))
            print(f"  + {len(assignments)} assignments copied")

        # 7. Generate embeddings and upsert to production Qdrant
        #    (Audit-first architecture: no vectors exist in staging,
        #     embedding happens here at promotion time.)
        try:
            import hashlib
            from qdrant_client import QdrantClient
            from qdrant_client.models import PointStruct

            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from services.ai_service import AIService

            qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY if QDRANT_API_KEY else None)
            local_ai = AIService()

            # Fetch meeting metadata for payload enrichment
            s_cur.execute(
                "SELECT start_date, committee FROM meetings WHERE id = %s",
                (meeting_id,)
            )
            meta_row = s_cur.fetchone()
            start_date_iso = meta_row["start_date"].isoformat() if meta_row and meta_row["start_date"] else None
            committee_val = (meta_row["committee"] if meta_row else None) or None

            # Fetch all chunks for this meeting
            s_cur.execute("""
                SELECT dc.id, dc.document_id, dc.chunk_index, dc.title,
                       dc.content, dc.chunk_type, dc.child_id,
                       d.name AS doc_name
                FROM document_chunks dc
                JOIN documents d ON dc.document_id = d.id
                WHERE d.meeting_id = %s
                ORDER BY dc.chunk_index
            """, (meeting_id,))
            all_chunks = s_cur.fetchall()

            points = []
            for chunk in all_chunks:
                text = (chunk["content"] or "").strip()
                if len(text) < 20:
                    continue
                title = chunk["title"] or "Untitled"
                doc_name_val = chunk["doc_name"] or ""

                context_str = f"[Document: {doc_name_val} | Section: {title}]\n"
                embedding = local_ai.generate_embedding(context_str + text)
                if embedding is None:
                    continue

                hash_str = hashlib.md5(
                    f"{chunk['document_id']}_{chunk['child_id']}_{chunk['chunk_index']}".encode()
                ).hexdigest()
                point_id = int(hash_str[:15], 16)

                payload = {
                    "document_id": chunk["document_id"],
                    "doc_name": doc_name_val,
                    "doc_type": "virtual_notulen",
                    "is_virtual_notulen": True,
                    "meeting_id": meeting_id,
                    "start_date": start_date_iso,
                    "committee": committee_val,
                    "child_id": chunk["child_id"],
                    "chunk_index": chunk["chunk_index"],
                    "chunk_type": chunk["chunk_type"],
                    "title": title,
                    "content": text,
                }
                points.append(PointStruct(id=point_id, vector=embedding, payload=payload))

            # Batch upsert (100 at a time)
            for i in range(0, len(points), 100):
                batch = points[i:i + 100]
                qdrant.upsert(collection_name=PRODUCTION_COLLECTION, points=batch)

            print(f"  + {len(points)} chunks embedded and upserted to {PRODUCTION_COLLECTION}")
        except ImportError as ie:
            print(f"  ! Missing dependency: {ie}")
        except Exception as e:
            print(f"  ! Embedding/Qdrant promotion failed: {e}")

        # 8. Mark as promoted in staging
        s_cur.execute("""
            UPDATE meetings SET review_status = 'approved', promoted_at = NOW()
            WHERE id = %s
        """, (meeting_id,))

        prod_conn.commit()
        staging_conn.commit()
        s_cur.close()
        p_cur.close()

        print(f"  Promoted: {meeting_id}")
        return True

    except Exception as e:
        prod_conn.rollback()
        staging_conn.rollback()
        print(f"  ERROR promoting {meeting_id}: {e}")
        return False
    finally:
        staging_conn.close()
        prod_conn.close()


def cmd_approve(args):
    """Approve and promote a single meeting."""
    print(f"\nPromoting meeting: {args.approve}")
    promote_meeting(args.approve)


def cmd_approve_batch(args):
    """Approve and promote all meetings above the minimum score."""
    min_score = args.min_score or 0.7
    conn = get_staging_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, quality_score, transcript_source
        FROM meetings
        WHERE review_status IN ('auto_approved', 'pending')
          AND quality_score >= %s
          AND transcript_source = 'vtt'
          AND promoted_at IS NULL
        ORDER BY start_date
    """, (min_score,))

    meetings = cur.fetchall()
    cur.close()
    conn.close()

    if not meetings:
        print(f"No meetings to promote (min_score={min_score}, source=vtt)")
        return

    print(f"\nPromoting {len(meetings)} meetings (score >= {min_score}, VTT source):")
    for m in meetings:
        print(f"  {m[0][:20]}... | {m[1][:40]} | score={m[2]:.3f}")

    promoted = 0
    for m in meetings:
        if promote_meeting(m[0]):
            promoted += 1

    print(f"\nPromoted: {promoted}/{len(meetings)}")


def cmd_reject(args):
    """Reject a meeting."""
    conn = get_staging_connection()
    cur = conn.cursor()

    reason = args.reason or "Rejected by reviewer"
    cur.execute("""
        UPDATE meetings SET review_status = 'rejected'
        WHERE id = %s
    """, (args.reject,))

    if cur.rowcount == 0:
        print(f"Meeting {args.reject} not found in staging.")
    else:
        print(f"Rejected: {args.reject} (reason: {reason})")

    conn.commit()
    cur.close()
    conn.close()


def cmd_stats(args):
    """Show pipeline statistics."""
    conn = get_staging_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE review_status = 'auto_approved') as auto_approved,
            COUNT(*) FILTER (WHERE review_status = 'pending') as pending,
            COUNT(*) FILTER (WHERE review_status = 'auto_rejected') as auto_rejected,
            COUNT(*) FILTER (WHERE review_status = 'approved') as promoted,
            COUNT(*) FILTER (WHERE review_status = 'rejected') as rejected,
            AVG(quality_score) FILTER (WHERE quality_score IS NOT NULL) as avg_score,
            MIN(quality_score) FILTER (WHERE quality_score IS NOT NULL) as min_score,
            MAX(quality_score) FILTER (WHERE quality_score IS NOT NULL) as max_score,
            COUNT(*) FILTER (WHERE transcript_source = 'vtt') as vtt_count,
            COUNT(*) FILTER (WHERE transcript_source = 'whisper') as whisper_count
        FROM meetings
    """)
    stats = cur.fetchone()

    print(f"\nPipeline Statistics")
    print(f"{'='*40}")
    print(f"Total meetings:   {stats['total']}")
    print(f"  Auto-approved:  {stats['auto_approved']}")
    print(f"  Pending review: {stats['pending']}")
    print(f"  Auto-rejected:  {stats['auto_rejected']}")
    print(f"  Promoted:       {stats['promoted']}")
    print(f"  Rejected:       {stats['rejected']}")
    print(f"\nQuality Scores:")
    print(f"  Average: {stats['avg_score']:.3f}" if stats['avg_score'] else "  Average: N/A")
    print(f"  Min:     {stats['min_score']:.3f}" if stats['min_score'] else "  Min:     N/A")
    print(f"  Max:     {stats['max_score']:.3f}" if stats['max_score'] else "  Max:     N/A")
    print(f"\nTranscript Sources:")
    print(f"  VTT:     {stats['vtt_count']}")
    print(f"  Whisper: {stats['whisper_count']}")

    # Pipeline runs
    cur.execute("""
        SELECT id, started_at, completed_at, status, meetings_total, meetings_completed, meetings_failed
        FROM pipeline_runs ORDER BY started_at DESC LIMIT 5
    """)
    runs = cur.fetchall()
    if runs:
        print(f"\nRecent Pipeline Runs:")
        for r in runs:
            started = str(r["started_at"])[:19] if r["started_at"] else "?"
            print(f"  {r['id']} | {started} | {r['status']} | "
                  f"{r['meetings_completed']}/{r['meetings_total']} completed, "
                  f"{r['meetings_failed']} failed")

    cur.close()
    conn.close()


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Review & promote committee meeting virtual notulen"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List staging meetings")
    group.add_argument("--preview", type=str, help="Preview a meeting's transcript")
    group.add_argument("--approve", type=str, help="Promote a meeting to production")
    group.add_argument("--approve-batch", action="store_true", help="Promote all auto-approved meetings")
    group.add_argument("--reject", type=str, help="Reject a meeting")
    group.add_argument("--stats", action="store_true", help="Show pipeline statistics")

    parser.add_argument("--status", type=str, help="Filter --list by status (pending/auto_approved/auto_rejected)")
    parser.add_argument("--min-score", type=float, default=0.7, help="Min quality score for --approve-batch")
    parser.add_argument("--reason", type=str, help="Rejection reason for --reject")

    args = parser.parse_args()

    if args.list:
        cmd_list(args)
    elif args.preview:
        cmd_preview(args)
    elif args.approve:
        cmd_approve(args)
    elif args.approve_batch:
        cmd_approve_batch(args)
    elif args.reject:
        cmd_reject(args)
    elif args.stats:
        cmd_stats(args)


if __name__ == "__main__":
    main()
