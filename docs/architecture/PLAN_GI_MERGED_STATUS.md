# Plan G+I Merged: Execution Status & Next Stages

**Last updated:** 2026-04-08
**Overall status:** Layer 1 COMPLETE, Layers 2-3 pending

---

## What Was Delivered (Layer 1 — 2026-04-08)

### Metadata Enrichment (all 1,627,899 chunks)

| Field | Type | Chunks populated | Source |
|-------|------|-----------------|--------|
| `section_topic` | TEXT | 1,627,899 (100%) | Heuristic: committee + doc title |
| `key_entities` | TEXT[] | 449,436 (28%) | Gazetteer match on parent doc title |
| `vote_outcome` | TEXT | 18,378 | Regex from notulen chunks |
| `vote_counts` | JSONB | 4,731 | Regex: `{"voor": N, "tegen": M}` |
| `indieners` | TEXT[] | 40,278 | Regex from motie/amendement docs |
| `motion_number` | TEXT | 424 | Regex: "M2023-042" pattern |

All fields stored in both PostgreSQL `document_chunks` and Qdrant `notulen_chunks` payloads.

### Knowledge Graph

| Component | Before | After |
|-----------|--------|-------|
| `kg_entities` | 1,196,824 (noisy) | 881,169 (cleaned, normalized) |
| `kg_relationships` | 0 | 57,633 edges |
| `kg_mentions` | 11,935,758 | 3,342,821 (pruned with noise entities) |
| `politician_registry` | n/a | 228 records with aliases |
| `domain_gazetteer` | n/a | 2,217 entities |

### Relationship breakdown

| Type | Count | Source |
|------|-------|--------|
| DIENT_IN | 55,535 | person/party → motie (from indieners regex) |
| AANGENOMEN | 1,208 | motie → vergadering |
| VERWORPEN | 547 | motie → vergadering |
| LID_VAN | 205 | politician → party (from politician_registry) |
| IS_WETHOUDER_VAN | 67 | wethouder → beleidsgebied (from portfolio notes) |
| STEMT_VOOR | 35 | party → motie (explicit per-party vote regex) |
| STEMT_TEGEN | 36 | party → motie |

### Infrastructure changes

| Change | File | Lines |
|--------|------|-------|
| BM25 switched to dual-dictionary tsvector | `services/rag_service.py` | 485, 498, 502 |
| zoek_moties returns structured vote data | `mcp_server_v3.py` | 911-960 |
| Enriched tsvector index | PostgreSQL | `idx_chunks_text_search_enriched` (GIN) |
| Qdrant payload indexes | Qdrant | `key_entities`, `vote_outcome`, `indieners` |

### Scripts created

| Script | Purpose | LOC |
|--------|---------|-----|
| `scripts/build_domain_gazetteer.py` | Unified entity list from kg_entities + lexicon + registry | ~200 |
| `scripts/seed_politician_registry.py` | Canonical politician table with aliases | ~150 |
| `scripts/clean_kg_entities.py` | Noise removal + normalization + party merge | ~250 |
| `scripts/enrich_and_extract.py` | Tier 2 rule extraction on all chunks (--tier2-only) | ~600 |
| `scripts/sync_enrichment_to_qdrant.py` | Push metadata from PostgreSQL to Qdrant payloads | ~200 |
| `scripts/populate_kg_relationships.py` | Create deterministic KG edges | ~350 |
| `scripts/cost_calculator.py` | LLM cost calculator with actual DB data | ~100 |

### Data files created

| File | Purpose |
|------|---------|
| `data/knowledge_graph/domain_gazetteer.json` | Unified domain entity list (2,217 entries) |
| `data/knowledge_graph/entity_resolution_map.json` | 81K canonical→alias mappings (from Google Drive) |
| `data/knowledge_graph/gliner_entities.jsonl` | 1.1GB raw GLiNER extraction (from Google Drive) |

---

## Next Stages

### Layer 2: Flair NER + Gazetteer (Cost: $0, Time: 3-4 hours)

**Status:** Not started
**Prerequisite:** `pip install flair` + test on 50 samples

**What it does:**
- Run `flair/ner-dutch-large` (F1 95.25 on Dutch NER) on all 1.6M chunks
- Augment with domain gazetteer for project/programme/organisation names
- Produces richer `key_entities` (person names, orgs, locations beyond doc titles)
- Merge with existing Tier 2 results

**Expected improvement:**
- `key_entities` coverage: 28% → ~60-70% of chunks
- Person entity recognition: currently only from doc titles, Flair catches inline mentions
- Organisation mentions: "Warmtebedrijf" in chunk text (not just doc title) gets tagged

**Scripts needed:** Extend `scripts/enrich_and_extract.py` with `--tier1` flag.

### Layer 3: Gemini Flash Lite (Cost: ~$90-130, Time: 3-5 hours)

**Status:** Not started
**Prerequisite:** Layer 2 completed (Flair entities feed into LLM prompt)

**What it does:**
- 562K API calls to Gemini 2.5 Flash Lite ($0.10/1M input, $0.40/1M output)
- Generates `answerable_questions` per chunk (3-5 Dutch questions, no dates)
- Refines `section_topic` where heuristic is too generic
- Extracts semantic relationships: HEEFT_BUDGET, BETREFT_WIJK, SPREEKT_OVER, VERWIJST_NAAR
- Parent-entity injection for chunks where gazetteer + doc title didn't cover the gap

**Prompt includes:**
- Gazetteer context (politicians, orgs, projects, committees for the period)
- Flair NER results as grounding
- Rule extraction results as verification

**Expected improvement:**
- `answerable_questions` populated (currently 0% → ~35% of chunks)
- kg_relationships: 57K → ~1M+ edges (semantic relationships added)
- Cross-document linking: motie docs ↔ notulen vote records

**Scripts needed:** Extend `scripts/enrich_and_extract.py` with `--tier3` flag + `scripts/enrichment_coordinator.py` for parallel execution.

### Layer 4: Graph Retrieval Service (Cost: $0, Time: 4-6 hours coding)

**Status:** Not started
**Prerequisite:** Layers 2-3 completed

**What it does:**
- `services/graph_retrieval.py`: Extract entities from query, traverse kg_relationships via SQL CTEs, format structured facts for LLM context
- Integrate into `services/rag_service.py`: prepend graph context for multi_hop and balanced_view queries
- Integrate into `services/query_router.py`: trigger graph traversal for entity-rich queries

**Expected improvement:**
- Multi-hop queries ("which parties voted against Warmtebedrijf?") answered with structured facts
- Completeness score: 2.75 → >=3.25 on v3 eval benchmark

### Layer 5: Audit + Evaluation (Cost: ~$10, Time: 1 day)

**Status:** Not started
**Prerequisite:** All layers completed

**What it does:**
- SQL consistency audit (NULL rates, distribution checks, entity cardinality)
- Deterministic spot-check (100 stratified samples, rule vs LLM comparison)
- LLM judge (50 samples via Gemini Flash Lite)
- v3 eval benchmark (20 questions): target completeness >=3.25, precision >=0.95

**Scripts needed:** `scripts/audit_enrichment_quality.py`

---

## Cost Summary

| Layer | Method | Cost | Status |
|-------|--------|------|--------|
| 1: Rules + Gazetteer | CPU-only | $0 | **COMPLETE** |
| 2: Flair NER | Local inference | $0 | Pending |
| 3: Gemini Flash Lite | 562K API calls | $90-130 | Pending |
| 4: Graph retrieval | Coding | $0 | Pending |
| 5: Audit + Eval | Gemini Flash Lite | ~$10 | Pending |
| **Total remaining** | | **$100-140** | |

---

## Future-Proofing

The schema supports scaling to other bestuurslagen:
- `politician_registry.organisatie`: "rotterdam" (default), extensible to "zuid-holland", "tweede_kamer"
- `kg_entities` can get `bestuurslaag` column: "gemeente", "provincie", "nationaal"
- Domain gazetteer is per-organisatie: `domain_gazetteer_rotterdam.json`, etc.
- All regex patterns and prompt templates are parameterizable

---

## MCP Server Configuration

Claude Desktop config at `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "neodemos_v3": {
      "command": "/Users/dennistak/Documents/Final Frontier/NeoDemos/.venv/bin/python",
      "args": ["/Users/dennistak/Documents/Final Frontier/NeoDemos/mcp_server_v3.py"],
      "env": {"PYTHONPATH": "/Users/dennistak/Documents/Final Frontier/NeoDemos"}
    }
  }
}
```

The `neodemos_v3` server includes all enrichment improvements. Restart Claude Desktop to pick up code changes.
