"""
Regression tests for ``scripts/audit_chunk_attribution``.

Why no real DB?
---------------
The audit script is READ-ONLY against production corpora (1.7M chunks) and
must coexist with WS6 Phase 3 + WS11 Phase 6 background writers. The
project's house rule is to NEVER run unrelated writes while those are
active. These tests therefore:

* validate the classifier on synthetic text (pure-function tests), and
* simulate three known documents + a deliberately-misattributed chunk by
  monkeypatching the script's two DB helpers. This exercises the full
  ``run_audit`` loop — iteration, bulk-doc fetch, Qdrant cross-check,
  summary accounting — without opening a psycopg2 connection.

Run:
    pytest tests/pipeline/test_chunk_attribution.py -v
"""

from __future__ import annotations

import pytest

from scripts import audit_chunk_attribution as aca


# ---------------------------------------------------------------------------
# 1. Pure-function classifier tests
# ---------------------------------------------------------------------------

class TestClassifier:
    def test_exact_substring(self):
        doc = "De begroting 2024 bevat posten voor parkeertarieven in de binnenstad."
        chunk = "posten voor parkeertarieven in de binnenstad"
        mt, overlap = aca.classify_attribution(chunk, doc)
        assert mt == aca.MATCH_EXACT
        assert overlap == 1.0

    def test_whitespace_normalized_substring(self):
        doc = "Centrum €3.50\n\n/ Buiten centrum €2.00 per uur"
        # Chunker collapsed the newline pair into a single space
        chunk = "Centrum €3.50 / Buiten centrum €2.00"
        mt, overlap = aca.classify_attribution(chunk, doc)
        assert mt == aca.MATCH_SUBSTRING
        assert overlap == 1.0

    def test_fuzzy_ocr_artifact(self):
        # >=80% token overlap but not contiguous — OCR rewrap style
        doc = (
            "Van de gemeenteraadsfractie GroenLinks: Alternatieve kaderbrief. "
            "Meer banen, meer bomen, bruisende binnenstad."
        )
        chunk = "GroenLinks kaderbrief alternatieve bomen banen bruisende binnenstad"
        mt, overlap = aca.classify_attribution(chunk, doc)
        assert mt == aca.MATCH_FUZZY
        assert overlap >= aca.FUZZY_THRESHOLD

    def test_mismatch_is_bug(self):
        # The actual parkeertarieven bug shape — chunk is from a parking doc,
        # doc_content is the GroenLinks kaderbrief. Token overlap tiny.
        doc = (
            "Van de gemeenteraadsfractie GroenLinks: Alternatieve kaderbrief "
            "2011, Meer banen, meer bomen en een bruisende binnenstad."
        )
        chunk = (
            "Parkeertarieven Rotterdam 2024: Centrum €3.50 per uur, "
            "Buiten centrum €2.00 per uur, vergunninghouders korting."
        )
        mt, overlap = aca.classify_attribution(chunk, doc)
        assert mt == aca.MATCH_MISMATCH
        assert overlap < aca.FUZZY_THRESHOLD

    def test_missing_doc(self):
        mt, overlap = aca.classify_attribution("wat dan ook", None)
        assert mt == aca.MATCH_MISSING_DOC
        assert overlap == 0.0

    def test_empty_doc(self):
        mt, overlap = aca.classify_attribution("iets", "")
        assert mt == aca.MATCH_EMPTY_DOC

    def test_empty_chunk_is_ok(self):
        mt, overlap = aca.classify_attribution("", "any content")
        assert mt == aca.MATCH_EXACT


# ---------------------------------------------------------------------------
# 2. End-to-end audit with monkeypatched DB layer
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_corpus(monkeypatch):
    """
    Simulate three documents and four chunks (one of which is a deliberate
    attribution bug) by monkeypatching the script's Postgres helpers and
    Qdrant client. No real network / DB calls happen.

    Documents
    ---------
    D1 — "Parkeerbeleid Rotterdam 2024" — tariff text
    D2 — "GroenLinks kaderbrief 2011"  — climate / urban text
    D3 — "Jaarstukken 2023"            — financial text

    Chunks
    ------
    C1 (doc=D1) verbatim slice from D1              -> exact
    C2 (doc=D2) whitespace-normalised slice from D2 -> substring
    C3 (doc=D3) token-overlap-only of D3            -> fuzzy
    C4 (doc=D2) the parking text from D1            -> MISMATCH
    """

    docs = {
        "D1": (
            "Parkeerbeleid Rotterdam 2024",
            "Parkeertarieven Rotterdam 2024. Centrum €3.50 per uur. "
            "Buiten centrum €2.00 per uur. Vergunninghouders ontvangen korting.",
        ),
        "D2": (
            "GroenLinks kaderbrief 2011",
            "Van de gemeenteraadsfractie GroenLinks: Alternatieve kaderbrief "
            "2011.\n\nMeer banen, meer bomen en een bruisende binnenstad.",
        ),
        "D3": (
            "Jaarstukken 2023",
            "Jaarrekening 2023 van de gemeente Rotterdam. Het resultaat "
            "bedraagt 45 miljoen euro. Het eigen vermogen is gestegen.",
        ),
    }

    chunks = [
        # (chunk_id, document_id, chunk_content)
        (101, "D1", "Centrum €3.50 per uur. Buiten centrum €2.00 per uur."),
        (102, "D2", "Alternatieve kaderbrief 2011. Meer banen, meer bomen en een bruisende binnenstad."),
        (103, "D3", "Jaarrekening Rotterdam resultaat bedraagt miljoen euro eigen vermogen gestegen"),
        # THE BUG: D1's parking content filed under D2.
        (999, "D2", "Parkeertarieven Rotterdam Centrum €3.50 per uur, buiten centrum €2.00."),
    ]

    # --- Patch _iter_chunks: yield one batch with everything ---
    def fake_iter(limit=None, doc_id=None, batch_size=500):
        rows = chunks
        if doc_id is not None:
            rows = [r for r in chunks if r[1] == doc_id]
        if limit is not None:
            rows = rows[:limit]
        yield rows

    monkeypatch.setattr(aca, "_iter_chunks", fake_iter)

    # --- Patch _fetch_doc_bulk: serve from our fake corpus ---
    def fake_fetch(doc_ids):
        return {d: docs[d] for d in doc_ids if d in docs}

    monkeypatch.setattr(aca, "_fetch_doc_bulk", fake_fetch)

    # --- Patch _corpus_stats so main() doesn't hit the DB ---
    monkeypatch.setattr(
        aca, "_corpus_stats",
        lambda: {"chunks": len(chunks), "documents": len(docs)},
    )

    # --- Replace the Qdrant lookup entirely ---
    class FakeQdrant:
        def __init__(self):
            # For the mismatch chunk we also simulate that Qdrant itself
            # disagrees with Postgres (payload.document_id == "D1") — this
            # is the classic case where Postgres was corrected but Qdrant
            # still points at the wrong doc.
            self._payloads = {}
            for chunk_id, doc_id, content in chunks:
                pid = aca.compute_point_id(str(doc_id), chunk_id)
                payload_doc = doc_id
                if chunk_id == 999:
                    # Qdrant says D1 even though PG says D2.
                    payload_doc = "D1"
                self._payloads[pid] = {
                    "document_id": payload_doc,
                    "content": content,
                }

        def fetch_payloads(self, point_ids):
            return {pid: self._payloads[pid] for pid in point_ids if pid in self._payloads}

    monkeypatch.setattr(aca, "_QdrantLookup", lambda *a, **kw: FakeQdrant())

    return docs, chunks


def test_run_audit_flags_only_the_planted_mismatch(fake_corpus):
    rows, summary = aca.run_audit(limit=None, doc_id=None, check_qdrant=True)

    assert summary["total"] == 4

    by_match = summary["by_match"]
    # Exactly one mismatch, and it's chunk 999.
    assert by_match.get(aca.MATCH_MISMATCH, 0) == 1
    mismatches = [r for r in rows if r.match_type == aca.MATCH_MISMATCH]
    assert len(mismatches) == 1
    assert mismatches[0].chunk_id == 999
    # Qdrant disagreement detected on the same row.
    assert mismatches[0].qdrant_document_id == "D1"
    assert "qdrant_doc_id_drift" in mismatches[0].notes

    # Zero missing_doc / empty_doc
    assert by_match.get(aca.MATCH_MISSING_DOC, 0) == 0
    assert by_match.get(aca.MATCH_EMPTY_DOC, 0) == 0

    # The three good chunks classify as exact / substring / fuzzy.
    good_types = {r.match_type for r in rows if r.chunk_id in (101, 102, 103)}
    assert aca.MATCH_EXACT in good_types or aca.MATCH_SUBSTRING in good_types
    assert aca.MATCH_FUZZY in good_types


def test_run_audit_clean_corpus_has_zero_mismatches(monkeypatch, fake_corpus):
    """With the planted bug removed, the audit must report zero mismatches."""
    docs, chunks = fake_corpus
    # Drop the buggy row and re-patch _iter_chunks.
    clean_chunks = [c for c in chunks if c[0] != 999]

    def clean_iter(limit=None, doc_id=None, batch_size=500):
        rows = clean_chunks
        if doc_id is not None:
            rows = [r for r in clean_chunks if r[1] == doc_id]
        if limit is not None:
            rows = rows[:limit]
        yield rows

    monkeypatch.setattr(aca, "_iter_chunks", clean_iter)

    rows, summary = aca.run_audit(limit=None, doc_id=None, check_qdrant=True)

    assert summary["by_match"].get(aca.MATCH_MISMATCH, 0) == 0
    assert all(r.match_type != aca.MATCH_MISMATCH for r in rows)


def test_doc_id_mode_scopes_to_one_document(fake_corpus):
    rows, summary = aca.run_audit(limit=None, doc_id="D2", check_qdrant=True)
    # Chunks 102 and 999 both belong to D2.
    ids = {r.chunk_id for r in rows}
    assert ids == {102, 999}
    # Audit still flags the buggy one.
    assert any(
        r.match_type == aca.MATCH_MISMATCH and r.chunk_id == 999
        for r in rows
    )


def test_exit_code_nonzero_on_mismatch(monkeypatch, tmp_path, fake_corpus):
    """main() must exit non-zero when any mismatch is present — CI gate."""
    out = tmp_path / "audit.csv"
    rc = aca.main([
        "--limit", "100",
        "--output", str(out),
    ])
    assert rc == 1  # planted bug => exit 1
    assert out.exists()
    # CSV has a header + 4 rows
    text = out.read_text(encoding="utf-8")
    assert "chunk_id,document_id" in text
    assert "999" in text


def test_exit_code_zero_on_clean_corpus(monkeypatch, tmp_path, fake_corpus):
    docs, chunks = fake_corpus
    clean_chunks = [c for c in chunks if c[0] != 999]

    def clean_iter(limit=None, doc_id=None, batch_size=500):
        rows = clean_chunks
        if doc_id is not None:
            rows = [r for r in clean_chunks if r[1] == doc_id]
        if limit is not None:
            rows = rows[:limit]
        yield rows

    monkeypatch.setattr(aca, "_iter_chunks", clean_iter)

    out = tmp_path / "audit_clean.csv"
    rc = aca.main([
        "--limit", "100",
        "--output", str(out),
    ])
    assert rc == 0
    assert out.exists()
