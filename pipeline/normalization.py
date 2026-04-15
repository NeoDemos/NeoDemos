import os
import logging
import re
from collections import OrderedDict
from contextlib import contextmanager
from typing import Dict, List, Optional, Any, Iterator

from psycopg2.extras import RealDictCursor

from services.db_pool import get_connection

logger = logging.getLogger(__name__)

class EntityNormalizer:
    """
    Normalizes speaker names and party affiliations by matching them against
    existing entities in the Knowledge Graph or historic statements.

    Connection lifecycle:
        All DB access goes through ``services.db_pool.get_connection`` so
        this class honors the ``pg_advisory_lock(42)`` writer discipline.
        For batch workloads, use ``normalize_segments`` or the ``batch()``
        context manager to borrow a single pool connection for the lifetime
        of the batch; individual ``normalize_speaker`` calls fall back to a
        short-lived pool checkout.
    """

    def __init__(self, db_url: Optional[str] = None):
        # ``db_url`` is retained for backward-compat with existing callers,
        # but connection management is now delegated to ``services.db_pool``
        # which reads ``DATABASE_URL`` / DB_* env vars at pool init time.
        self.db_url = db_url or os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/neodemos",
        )
        # When inside a ``batch()`` scope this holds the borrowed pool
        # connection; otherwise ``None`` and callers do per-call checkouts.
        self._batch_conn = None
        self._entity_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._entity_cache_max = 10_000

    def _cache_set(self, key: str, value: Dict[str, Any]) -> None:
        self._entity_cache[key] = value
        if len(self._entity_cache) > self._entity_cache_max:
            self._entity_cache.popitem(last=False)

    @contextmanager
    def batch(self) -> Iterator[None]:
        """
        Borrow a single pool connection for the duration of a batch of
        ``normalize_speaker`` calls. Nested ``batch()`` scopes reuse the
        outer connection (re-entrant, no double checkout).

        On failure to obtain a connection, logs a warning and yields
        without a borrowed connection — individual ``normalize_speaker``
        calls will then also fail soft (return ``mapped=False``) the same
        way the pre-refactor code did.
        """
        if self._batch_conn is not None:
            # Already inside a batch — reuse the outer connection.
            yield
            return

        try:
            with get_connection() as conn:
                self._batch_conn = conn
                try:
                    yield
                finally:
                    self._batch_conn = None
        except Exception as e:
            logger.warning(f"Could not connect to database for normalization: {e}")
            # Preserve soft-fail behavior: yield so the batch can still run
            # its non-DB work (e.g. ``correct_terms``) without raising.
            self._batch_conn = None
            yield

    def _strip_role(self, name: str) -> str:
        """
        Removes common roles and suffixes from speaker names.
        Example: "L.S. (Larissa) Vlieger (Commissievoorzitter)" -> "L.S. (Larissa) Vlieger"
        """
        if not name: return name
        # Remove anything in parentheses at the end that looks like a role
        roles = [
            r"\(Commissievoorzitter\)", r"\(Lid\)", r"\(Plaatsvervangend voorzitter\)",
            r"\(Wethouder\)", r"\(Burgemeester\)", r"\(Secretaris\)", r"\(Raadslid\)"
        ]
        clean_name = name
        for role in roles:
            clean_name = re.sub(role, "", clean_name, flags=re.IGNORECASE)

        # Also handle common prefixes
        prefixes = [r"^Wethouder\s+", r"^Voorzitter\s+", r"^Burgemeester\s+", r"^De heer\s+", r"^Mevrouw\s+"]
        for pref in prefixes:
            clean_name = re.sub(pref, "", clean_name, flags=re.IGNORECASE)

        return clean_name.strip()

    def correct_terms(self, text: str) -> str:
        """
        Fixes common mis-transcriptions of municipal terms.
        """
        if not text: return text
        corrections = {
            r"\bkoor\b": "COR",
            r"\bkor\b": "COR",
            r"\bibab\b": "iBabs",
            r"\bibabs\b": "iBabs",
            r"\broyal cast\b": "Royalcast",
            r"\broyalcast\b": "Royalcast",
            r"\braads voorstel\b": "raadsvoorstel",
            r"\bver vallen\b": "vervallen",
        }
        corrected = text
        for pattern, replacement in corrections.items():
            corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)
        return corrected

    def _lookup(self, conn, search_name: str, name: str, party: Optional[str], cache_key: str) -> Dict[str, Any]:
        """Run the kg_entities + party_statements lookups against ``conn``.

        Returns the normalized dict and populates the LRU cache. Raised
        exceptions propagate to the caller so they can be logged once at
        the top-level ``normalize_speaker`` boundary.
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Direct match in kg_entities
            cur.execute(
                "SELECT id, name, metadata FROM kg_entities WHERE (name = %s OR name = %s) AND type ILIKE '%%person%%'",
                (search_name, name)
            )
            entity = cur.fetchone()
            if entity:
                result = {
                    "name": entity['name'],
                    "party": entity['metadata'].get('fractie') or party,
                    "entity_id": entity['id'],
                    "mapped": True,
                    "source": "kg_entities"
                }
                self._cache_set(cache_key, result)
                return result

            # 2. Match in party_statements (historical data)
            cur.execute(
                "SELECT speaker_name, party_name FROM party_statements WHERE (speaker_name = %s OR speaker_name = %s) LIMIT 1",
                (search_name, name)
            )
            stmt = cur.fetchone()
            if stmt:
                result = {
                    "name": stmt['speaker_name'],
                    "party": stmt['party_name'] or party,
                    "mapped": True,
                    "source": "party_statements"
                }
                self._cache_set(cache_key, result)
                return result

            # 3. Fuzzy match fallback (Placeholder for implementation with fuzzywuzzy or pg_trgm)
            # For now, we return the raw data but marked as unmapped
            unmapped = {"name": name, "party": party, "mapped": False}
            self._cache_set(cache_key, unmapped)
            return unmapped

    def normalize_speaker(self, name: str, party: Optional[str] = None) -> Dict[str, Any]:
        """
        Attempts to find a canonical entity for a speaker.
        Returns a dict with normalized fields and a 'mapped' flag.
        """
        if not name:
            return {"name": name, "party": party, "mapped": False}

        # 0. Strip roles for better matching
        search_name = self._strip_role(name)

        # Check cache first
        cache_key = f"{name}|{party or ''}"
        if cache_key in self._entity_cache:
            return self._entity_cache[cache_key]

        # Prefer the batch-scoped connection when one is borrowed; otherwise
        # do a short-lived per-call pool checkout.
        if self._batch_conn is not None:
            try:
                return self._lookup(self._batch_conn, search_name, name, party, cache_key)
            except Exception as e:
                # A failed query leaves the shared batch connection in an
                # aborted state; rollback so subsequent speakers in the same
                # batch don't all fail with "current transaction is aborted".
                try:
                    self._batch_conn.rollback()
                except Exception:
                    pass
                logger.error(f"Error during speaker normalization: {e}")
                return {"name": name, "party": party, "mapped": False}

        # No batch: short-lived pool checkout. The pool's context manager
        # rolls back automatically on exception and returns the connection.
        try:
            with get_connection() as conn:
                return self._lookup(conn, search_name, name, party, cache_key)
        except Exception as e:
            # The pool's ``get_connection`` can raise either because of a
            # connect/checkout failure or a query failure that bubbled out
            # after auto-rollback. Distinguish by message to preserve the
            # pre-refactor log levels (warning vs error).
            msg = str(e).lower()
            if "connect" in msg or "pool" in msg or "refused" in msg:
                logger.warning(f"Could not connect to database for normalization: {e}")
            else:
                logger.error(f"Error during speaker normalization: {e}")
            return {"name": name, "party": party, "mapped": False}

    def normalize_segments(self, segments: List[Any]) -> List[Any]:
        """
        Batch normalizes a list of objects (SpeakerSegment, DetectedSpeaker, or TranscriptSegment).
        Note: Modifies segments in place.
        """
        # Borrow one pool connection for the full batch to avoid per-chunk
        # pool churn. ``batch()`` is re-entrant, so nested callers are fine.
        with self.batch():
            for seg in segments:
                # Check for 'speaker' or 'name' attribute
                name = getattr(seg, 'speaker', getattr(seg, 'name', None))
                party = getattr(seg, 'party', None)

                if name:
                    norm = self.normalize_speaker(name, party)
                    if norm.get("mapped"):
                        # Update whatever attribute we found
                        if hasattr(seg, 'speaker'):
                            seg.speaker = norm['name']
                        elif hasattr(seg, 'name'):
                            seg.name = norm['name']

                        seg.party = norm['party']

                        # Store mapping info in metadata if it exists
                        if hasattr(seg, 'metadata') and seg.metadata is not None:
                            if isinstance(seg.metadata, dict):
                                seg.metadata['normalized'] = True
                                seg.metadata['raw_name'] = name # Preserve original for provenance
                                seg.metadata['entity_id'] = norm.get('entity_id')
                                seg.metadata['norm_source'] = norm.get('source')

                # Also apply term correction to the text if it's a transcript segment
                if hasattr(seg, 'text') and seg.text:
                    seg.text = self.correct_terms(seg.text)
        return segments

    def close(self) -> None:
        """No-op retained for backward compatibility.

        Pre-refactor this closed a cached raw ``psycopg2.connect`` handle.
        Connection lifecycle is now owned by ``services.db_pool``; any
        borrowed connection is returned to the pool when the enclosing
        ``batch()`` (or ``normalize_segments``) scope exits.
        """
        # Defensive: if someone calls close() from outside a batch scope
        # while a batch is somehow still active (shouldn't happen — the
        # context manager owns the lifecycle), clear the reference.
        self._batch_conn = None

    def __enter__(self) -> "EntityNormalizer":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
