# Plan I: LightRAG — Entity Extraction & Graph Retrieval

**Status:** Not started
**Estimated cost:** ~$30-50 in API calls
**Estimated time:** 2-3 days coding + 12-16 hours batch processing
**Dependencies:** Benefits from Plan G (better context → better extraction), but can run independently
**Risk level:** Medium

---

## 1. Problem Statement

Our multi_hop questions (mh-01: Warmtebedrijf votes, mh-02: culture vs sport budget) require connecting facts across multiple documents. Vector similarity and keyword search find chunks that **mention** a topic but cannot **traverse relationships** between entities.

Example: "Welke partijen stemden tegen het Warmtebedrijf voorstel?"

To answer this, you need to connect:
```
Warmtebedrijf voorstel (raadsvoorstel, dec 2016)
  ← GESTEMD_TEGEN: SP (motie: "geen overbruggingskrediet")  
  ← GESTEMD_TEGEN: Leefbaar Rotterdam
  ← GESTEMD_VOOR: PvdA, D66, GroenLinks, VVD, CDA (meerderheid)
  ← STEMUITSLAG: 28 voor, 17 tegen
```

This chain spans 5+ chunks from 3 different documents. No single vector search will retrieve it. Sub-query decomposition helps but still relies on text similarity, not structured relationships.

---

## 2. Current state of the knowledge graph

The NeoDemos project already has partial KG infrastructure:

| Table | Rows | Status |
|-------|------|--------|
| `kg_entities` | 1,196,824 | Populated by GLiNER NER extraction |
| `kg_mentions` | 11,935,758 | Entity-to-chunk links |
| `kg_relationships` | **0** | Empty — this is what we build |
| Entity resolution map | 81,472 entries | `data/knowledge_graph/entity_resolution_map.json` |

### Problems with existing kg_entities

The GLiNER extraction produced noisy entities:
- `"Wijzijndaaromverheugddatop8decembereeneerstestapwordtgezet"` (concatenated text, not an entity)
- `"{\"de heer Pans\",\"de heer Paans\"}"` (JSON-formatted alternatives)
- `"{Regiomanager}"` (role, not entity)
- Only 26 generic types (Organization, Topic, Person, Location...)

LightRAG replaces this with cleaner, domain-specific LLM-based extraction.

### Existing schema (usable as-is)

```sql
-- kg_entities (will receive new, cleaner rows)
CREATE TABLE kg_entities (
    id SERIAL PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(type, name)
);

-- kg_relationships (currently 0 rows — will be populated)
CREATE TABLE kg_relationships (
    id SERIAL PRIMARY KEY,
    source_entity_id INTEGER REFERENCES kg_entities(id),
    target_entity_id INTEGER REFERENCES kg_entities(id),
    relation_type TEXT NOT NULL,
    document_id TEXT,
    chunk_id INTEGER,
    confidence DOUBLE PRECISION,
    quote TEXT,                    -- supporting evidence from source text
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes already exist:
-- idx_kg_rels_source, idx_kg_rels_target, idx_kg_rels_type, idx_kg_rels_doc
```

---

## 3. What we extract

### Entity types (domain-specific for Rotterdam council)

| Type | Description | Examples |
|------|-------------|----------|
| `partij` | Political party | PvdA, VVD, Leefbaar Rotterdam, D66 |
| `raadslid` | Council member | Dennis Tak, Pastors, Vlaardingerbroek |
| `wethouder` | Alderman (member of B&W) | Schneider, Kurvers, Kasmi |
| `organisatie` | External organization | Warmtebedrijf, Evides, GGD |
| `motie` | Motion submitted | "Motie geen overbruggingskrediet" |
| `amendement` | Amendment | "Amendement woningbouw Feijenoord" |
| `raadsvoorstel` | Council proposal | "Raadsvoorstel Warmtebedrijf 2016" |
| `begrotingspost` | Budget line item | "Cultuurbudget 2022", "Sport subsidie" |
| `programma` | Policy programme | "Nationaal Programma Rotterdam Zuid" |
| `wijk` | Neighbourhood/area | Feijenoord, M4H, Tweebosbuurt |
| `commissie` | Committee | Commissie ZWCS, Commissie BWB |
| `vergadering` | Meeting session | "Raadsvergadering 12 december 2016" |

### Relationship types

| Type | Description | Example |
|------|-------------|---------|
| `LID_VAN` | Person is member of party | Pastors → LID_VAN → Leefbaar Rotterdam |
| `DIENT_IN` | Party/person submits motion/amendment | SP → DIENT_IN → Motie Warmtebedrijf |
| `STEMT_VOOR` | Party votes in favour | PvdA → STEMT_VOOR → Raadsvoorstel Warmtebedrijf |
| `STEMT_TEGEN` | Party votes against | SP → STEMT_TEGEN → Raadsvoorstel Warmtebedrijf |
| `HEEFT_BUDGET` | Programme has budget | Cultuurbeleid → HEEFT_BUDGET → €45 mln (2022) |
| `BETREFT_WIJK` | Policy concerns area | NPRZ → BETREFT_WIJK → Rotterdam Zuid |
| `VERWIJST_NAAR` | Document references another | Motie X → VERWIJST_NAAR → Raadsvoorstel Y |
| `ONDERDEEL_VAN` | Entity is part of larger entity | ZWCS → ONDERDEEL_VAN → Gemeenteraad |
| `SPREEKT_OVER` | Person speaks about topic | Pastors → SPREEKT_OVER → Warmtebedrijf |
| `IS_WETHOUDER_VAN` | Alderman responsible for domain | Schneider → IS_WETHOUDER_VAN → Wonen |
| `AANGENOMEN` | Motion/amendment was adopted | Motie X → AANGENOMEN → Raadsvergadering Y |
| `VERWORPEN` | Motion/amendment was rejected | Motie Z → VERWORPEN → Raadsvergadering Y |

---

## 4. Which chunks to process

Not all 1.63M chunks need entity extraction. Target high-value document types:

| Doc type | Chunks | Extract? | Why |
|----------|--------|----------|-----|
| notulen | 199,218 | **YES** | Speaker-party links, voting records, debate positions |
| motie | 130,914 | **YES** | Signatories, policy positions, vote outcomes |
| amendement | 3,871 | **YES** | Same as motie |
| raadsvoorstel | 41,859 | **YES** | Policy proposals, budget implications |
| financieel | 68,301 | **YES** | Budget line items, amounts, year-over-year |
| overig | 1,185,605 | **NO** | Too broad, low entity density |
| **Total to process** | **444,163** | | ~27% of all chunks |

---

## 5. Extraction pipeline

### Step 1: Entity extraction

**File:** `scripts/extract_entities_lightrag.py`

**Architecture:**
- Same pattern as `enrich_qdrant_metadata.py`: batch scroll, checkpoint-resumable, RAM guard
- Process chunks from PostgreSQL (not Qdrant — we need the full content + doc metadata)
- 10 concurrent Haiku calls via asyncio
- Store extracted entities in a staging table before dedup

**Prompt template:**
```
Je analyseert een fragment uit een Rotterdams gemeenteraadsdocument.

Document: {doc_name}
Type: {doc_type}
Datum: {meeting_date}
Commissie: {committee}

Fragment:
{chunk_content}

Extraheer alle genoemde entiteiten. Geef antwoord als JSON:
{{
  "entities": [
    {{"name": "exacte naam", "type": "type", "description": "korte beschrijving (max 15 woorden)"}}
  ]
}}

Gebruik alleen deze types: partij, raadslid, wethouder, organisatie, motie, 
amendement, raadsvoorstel, begrotingspost, programma, wijk, commissie, vergadering.

Regels:
- Gebruik de volledige officiële naam (niet afkortingen tenzij die standaard zijn: VVD, PvdA, D66)
- Voor raadsleden: gebruik "de heer/mevrouw" NIET, alleen de achternaam
- Voor moties: gebruik de titel als die er is, anders beschrijf het onderwerp
- Als er geen entiteiten zijn, geef een lege array
```

**Output per chunk (example):**
```json
{
  "entities": [
    {"name": "Pastors", "type": "raadslid", "description": "raadslid Leefbaar Rotterdam"},
    {"name": "Leefbaar Rotterdam", "type": "partij", "description": "politieke partij"},
    {"name": "Warmtebedrijf", "type": "organisatie", "description": "stadsverwarmingsbedrijf Rotterdam"},
    {"name": "overbruggingskrediet €18 mln", "type": "begrotingspost", "description": "gevraagd krediet voor Warmtebedrijf"}
  ]
}
```

**Cost calculation:**
```
444,163 chunks
× ~600 tokens input (metadata + chunk text, capped at 500 chars)
× ~100 tokens output (entity list)
= ~266M input + ~44M output

Haiku: $0.80/1M input, $4.00/1M output
Input:  266M × $0.80/1M = $0.21
Output: 44M × $4.00/1M  = $0.18
Total Step 1: ~$0.40

With 10 concurrent calls at ~0.5s each:
444,163 ÷ (10 × 120/min) = ~370 min = ~6 hours
```

### Step 2: Relationship extraction

**File:** `scripts/extract_relationships_lightrag.py`

**Input:** Only chunks where Step 1 found ≥2 entities (relationships need at least 2 entities). Estimated: ~60% of processed chunks = ~266K chunks.

**Prompt template:**
```
Je analyseert een fragment uit een Rotterdams gemeenteraadsdocument.

Document: {doc_name}
Datum: {meeting_date}

Fragment:
{chunk_content}

Gevonden entiteiten in dit fragment:
{entities_json}

Extraheer relaties TUSSEN de bovenstaande entiteiten. Geef antwoord als JSON:
{{
  "relationships": [
    {{
      "source": "exacte naam van bron-entiteit",
      "target": "exacte naam van doel-entiteit", 
      "type": "relatietype",
      "quote": "exacte tekst uit het fragment die deze relatie bevestigt (max 100 tekens)"
    }}
  ]
}}

Gebruik alleen deze relatietypes: LID_VAN, DIENT_IN, STEMT_VOOR, STEMT_TEGEN,
HEEFT_BUDGET, BETREFT_WIJK, VERWIJST_NAAR, ONDERDEEL_VAN, SPREEKT_OVER,
IS_WETHOUDER_VAN, AANGENOMEN, VERWORPEN.

Regels:
- Alleen relaties die EXPLICIET in de tekst staan
- De "quote" moet letterlijk uit het fragment komen
- Als er geen relaties zijn, geef een lege array
- Maximaal 10 relaties per fragment
```

**Cost:**
```
266,498 chunks × ~800 tokens input × ~150 tokens output
= ~213M input + ~40M output

Haiku: $0.17 + $0.16 = ~$0.33
Time: ~4 hours (10 concurrent)
```

### Step 3: Entity resolution

**File:** `scripts/resolve_entities_lightrag.py`

**What it does:**
1. Load all extracted entities from staging table
2. Normalize names:
   - Strip articles: "de heer Pastors" → "Pastors"
   - Apply existing `entity_resolution_map.json` (81,472 entries)
   - Apply `party_utils.py` normalization for party names
   - Fuzzy match on remaining duplicates (Levenshtein distance ≤ 2)
3. Merge duplicates: pick canonical name, merge descriptions
4. Write resolved entities to `kg_entities` table
5. Update relationship source/target IDs to point to resolved entities

**No API calls needed** — pure Python processing.

**Time:** ~1-2 hours for 444K chunks worth of entities.

### Step 4: Load into PostgreSQL

**What it does:**
1. Bulk INSERT into `kg_relationships` (from step 2 output, with resolved entity IDs)
2. Create composite indexes for efficient traversal:

```sql
-- Forward traversal: "what did entity X do?"
CREATE INDEX idx_kg_rels_source_type ON kg_relationships(source_entity_id, relation_type);

-- Reverse traversal: "who voted against entity Y?"
CREATE INDEX idx_kg_rels_target_type ON kg_relationships(target_entity_id, relation_type);

-- Temporal queries: "relationships for entity X after date Y"
CREATE INDEX idx_kg_rels_source_date ON kg_relationships(source_entity_id, created_at);
```

**Expected data volume:**
```
444,163 chunks × ~3 relationships/chunk (average from sample runs)
= ~1.3M relationships

With entity resolution merging: ~1M unique relationships
Storage: ~200 bytes/row × 1M = ~200 MB
Indexes: ~150 MB
Total PostgreSQL growth: ~350 MB
```

Note: this is lower than the initial 8-16M estimate in the deployment doc because we're targeting 444K chunks (not all 1.63M) and actual relationship density is lower than the Mistral sample suggested.

---

## 6. Query-time integration

### New service: `services/graph_retrieval.py` (~200 LOC)

```python
class GraphRetriever:
    """Traverses kg_relationships for multi-hop queries."""
    
    def __init__(self, db_url: str): ...
    
    def extract_entities_from_query(self, query: str) -> List[str]:
        """Use entity name matching + Haiku extraction to find entities in query."""
    
    def traverse(
        self, entity_names: List[str], 
        relation_types: List[str] = None,
        max_hops: int = 2,
    ) -> List[GraphFact]:
        """
        BFS traversal from seed entities.
        Returns structured facts with provenance.
        """
    
    def format_graph_context(self, facts: List[GraphFact]) -> str:
        """Format graph facts as readable context for LLM."""
```

**GraphFact dataclass:**
```python
@dataclass
class GraphFact:
    source_entity: str
    relation_type: str
    target_entity: str
    quote: str           # Evidence from original text
    document_id: str     # Provenance
    meeting_date: str    # Temporal context
    hop_distance: int    # 1 = direct, 2 = two-hop
```

### Query-time flow

```
User: "Welke partijen stemden tegen het Warmtebedrijf voorstel?"

1. Router classifies as multi_hop
2. Extract entities from query: ["Warmtebedrijf"]
3. Graph traversal (2 hops, relation_types=["STEMT_TEGEN", "STEMT_VOOR"]):
   
   Hop 1: Warmtebedrijf ← STEMT_TEGEN ← SP (quote: "SP dient motie in tegen...")
   Hop 1: Warmtebedrijf ← STEMT_TEGEN ← Leefbaar Rotterdam (quote: "Leefbaar pleit voor...")
   Hop 1: Warmtebedrijf ← STEMT_VOOR ← PvdA (quote: "PvdA steunt het voorstel")
   Hop 1: Warmtebedrijf ← DIENT_IN ← SP → Motie "geen overbruggingskrediet"
   Hop 2: SP → LID_VAN → Pastors (who submitted the motion)

4. Format as context:
   "## Gestructureerde feiten over Warmtebedrijf
   - SP stemde TEGEN (bron: notulen 12-12-2016, 'SP dient motie in tegen...')
   - Leefbaar Rotterdam stemde TEGEN (bron: notulen 12-12-2016)
   - PvdA stemde VOOR (bron: notulen 12-12-2016)
   - SP diende motie in: geen overbruggingskrediet van €18 mln
   - Motie ingediend door raadslid Pastors (Leefbaar Rotterdam)"

5. Prepend to chunk context before LLM generation
6. LLM gets structured facts + supporting chunks → much better answer
```

### Integration into v3 wrapper

In `eval/v3/instrumentation/rag_wrapper_v3.py`, the `_sub_query_retrieve` method gets an additional step:

```python
# After sub-query retrieval and CRAG filter:
if graph_retriever:
    entities = graph_retriever.extract_entities_from_query(query_text)
    if entities:
        graph_facts = graph_retriever.traverse(entities, max_hops=2)
        graph_context = graph_retriever.format_graph_context(graph_facts)
        # Prepend to synthesis context
```

---

## 7. SQL queries for traversal

### 1-hop: Direct relationships for an entity
```sql
SELECT e2.name, r.relation_type, r.quote, r.document_id
FROM kg_relationships r
JOIN kg_entities e1 ON r.target_entity_id = e1.id
JOIN kg_entities e2 ON r.source_entity_id = e2.id
WHERE e1.name = 'Warmtebedrijf'
AND r.relation_type IN ('STEMT_VOOR', 'STEMT_TEGEN', 'DIENT_IN');
```

### 2-hop: Follow relationships from 1-hop results
```sql
WITH hop1 AS (
    SELECT e2.id, e2.name, r.relation_type, r.quote
    FROM kg_relationships r
    JOIN kg_entities e1 ON r.target_entity_id = e1.id
    JOIN kg_entities e2 ON r.source_entity_id = e2.id
    WHERE e1.name = 'Warmtebedrijf'
)
SELECT h1.name as hop1_entity, h1.relation_type as hop1_rel,
       e3.name as hop2_entity, r2.relation_type as hop2_rel, r2.quote
FROM hop1 h1
JOIN kg_relationships r2 ON r2.source_entity_id = h1.id
JOIN kg_entities e3 ON r2.target_entity_id = e3.id
WHERE r2.relation_type IN ('LID_VAN', 'DIENT_IN', 'HEEFT_BUDGET');
```

### Budget comparison (financial multi_hop)
```sql
SELECT e.name, r.relation_type, r.quote, r.metadata->>'year' as year, r.metadata->>'amount' as amount
FROM kg_relationships r
JOIN kg_entities e ON r.source_entity_id = e.id
WHERE e.type = 'begrotingspost'
AND e.name ILIKE '%cultuur%' OR e.name ILIKE '%sport%'
ORDER BY r.metadata->>'year';
```

---

## 8. Files to create/modify

| File | Action | LOC est. | Phase |
|------|--------|----------|-------|
| `scripts/extract_entities_lightrag.py` | NEW | ~300 | Extraction |
| `scripts/extract_relationships_lightrag.py` | NEW | ~300 | Extraction |
| `scripts/resolve_entities_lightrag.py` | NEW | ~200 | Processing |
| `services/graph_retrieval.py` | NEW | ~200 | Integration |
| `eval/v3/instrumentation/rag_wrapper_v3.py` | MODIFY | ~30 | Integration |
| `eval/v3/config.py` | MODIFY (add enable_graph flag) | ~5 | Integration |

---

## 9. Cost breakdown

| Step | API calls | Cost | Time |
|------|-----------|------|------|
| Entity extraction (444K chunks) | 444K Haiku | ~$0.40 | ~6 hours |
| Relationship extraction (266K chunks) | 266K Haiku | ~$0.33 | ~4 hours |
| Entity resolution | 0 (Python only) | $0 | ~1-2 hours |
| PostgreSQL load + indexing | 0 | $0 | ~30 min |
| **Total extraction** | **710K Haiku calls** | **~$0.73** | **~12 hours** |
| Graph retrieval service (coding) | 0 | $0 | ~1-2 days |
| Integration + testing | 0 | $0 | ~1 day |
| **Total project** | | **~$1** | **2-3 days + 12 hours batch** |

Note: the cost is much lower than the initial $30-50 estimate because Haiku 4.5 pricing is very aggressive on short prompts. The main cost is engineering time, not API calls.

---

## 10. Execution checklist

```
Day 1 — Extraction scripts:
  [ ] Write scripts/extract_entities_lightrag.py
  [ ] Smoke test on 100 chunks, verify entity quality
  [ ] Start full entity extraction batch (run overnight, ~6 hours)

Day 2 — Relationships + resolution:
  [ ] Write scripts/extract_relationships_lightrag.py  
  [ ] Start relationship extraction batch (entities from day 1 as input)
  [ ] Write scripts/resolve_entities_lightrag.py
  [ ] Run entity resolution
  [ ] Load into kg_relationships table
  [ ] Verify: SELECT COUNT(*) FROM kg_relationships (expect ~1M rows)
  [ ] Create composite indexes

Day 3 — Integration:
  [ ] Write services/graph_retrieval.py
  [ ] Integration test: query "Warmtebedrijf" → verify graph traversal returns correct parties
  [ ] Wire into rag_wrapper_v3.py sub_query strategy
  [ ] Run v3 eval benchmark on multi_hop questions (mh-01, mh-02)
  [ ] Run full 20-question v3 eval, compare against v3-full-v2

Day 4 — Polish:
  [ ] Run eval on balanced_view (bv-01) with graph retrieval
  [ ] Tune max_hops and relation_type filters per query type
  [ ] Update ARCHITECTURE.md and EVALUATION_REPORT
```

---

## 11. Risks and mitigations

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Haiku extracts noisy entities from Dutch text | Medium | Graph has garbage nodes | Smoke test on 100 chunks first; add confidence threshold |
| Relationship extraction hallucinates connections | Medium | False graph edges → wrong answers | Require `quote` field; verify quote exists in source chunk |
| Entity resolution merges distinct entities | Low | "Rotterdam" (city) merged with "Rotterdam" (football) | Type-aware dedup: only merge within same entity type |
| Graph too sparse for useful traversal | Medium | Multi-hop queries still fail | Check density: if <0.5 rels/chunk, expand to more doc types |
| Graph traversal too slow (2-hop on 1M+ edges) | Low | Query latency spike | PostgreSQL indexes + LIMIT clauses; cache hot entities |
| Extraction batch fails mid-run | Low | Wasted compute | Checkpoint-resumable (same pattern as enrichment script) |

---

## 12. How to validate success

### Before/after comparison on multi_hop

| Question | Current v3 score | Target with graph | How graph helps |
|----------|-----------------|-------------------|-----------------|
| mh-01 (Warmtebedrijf votes) | Relevance 2.0, Faithfulness 4.5 | **Relevance 4.0+** | Graph provides structured vote records |
| mh-02 (culture vs sport budget) | Relevance 1.0, Faithfulness 5.0 | **Relevance 3.5+** | Graph links begrotingspost entities across years |

### Graph density check

After extraction, verify:
```sql
-- Should see meaningful relationships
SELECT r.relation_type, COUNT(*) 
FROM kg_relationships r
GROUP BY r.relation_type 
ORDER BY COUNT(*) DESC;

-- Expected distribution:
-- SPREEKT_OVER: ~300K (most common — speaker discusses topic)
-- LID_VAN: ~50K (party membership)
-- DIENT_IN: ~30K (motion/amendment submissions)
-- STEMT_VOOR/STEMT_TEGEN: ~20K (if voting records are extractable)
-- HEEFT_BUDGET: ~10K (budget relationships)
```

### Traversal sanity check

```sql
-- "Who is connected to Warmtebedrijf?"
SELECT e.name, e.type, r.relation_type, r.quote
FROM kg_relationships r
JOIN kg_entities e ON r.source_entity_id = e.id
WHERE r.target_entity_id = (SELECT id FROM kg_entities WHERE name = 'Warmtebedrijf' LIMIT 1)
ORDER BY r.relation_type;

-- Should return: SP (STEMT_TEGEN), PvdA (STEMT_VOOR), etc.
```
