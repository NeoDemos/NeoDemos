# Phase 2B: AI-Powered Party Position Analysis
## Revised Plan - High-Confidence Profiling (70-80%+ Accuracy)

### Overview

This phase implements a scientifically-grounded political stance analysis system combining:
- **DGAP multi-signal weighted scoring** (semantic 50%, topic 25%, stance 25%)
- **Benoit ensemble validation** (confidence reporting + ambiguity flagging)
- **Contextual actor profiling** (Approach 3, enhanced for 70-80%+ accuracy)
- **Politheon lessons** (source citation, numeric scoring, exportable briefs)

**Target accuracy**: 70-80%+ (vs. 74% single-pass baseline)

---

## Architecture

```
PHASE 2B PIPELINE

Input: Programme PDF + Notulen + Moties/Amendementen
  ↓
1. ATOMIC CHUNKING
   - Programme: split into policy bullets (1-3 sentences each)
   - Notulen: extract speaker statements (150-300 tokens)
   - Moties: extract proposals (structured)
  ↓
2. MULTI-SOURCE PROFILE EXTRACTION
   - Extract programme positions
   - Extract notulen statements (GroenLinks + PvdA combined)
   - Extract moties/amendementen
   - SYNTHESIZE into unified party profile
   ↓
3. PROFILE GROUNDING CHECK
   - Validate profile internal consistency
   - Identify contradictions between sources
   - Flag evolution over time
   - Present for user review/correction
  ↓
4. EMBEDDING LAYER (pgvector)
   - Generate Gemini embeddings for all chunks
   - Store in PostgreSQL vector columns
   - Enable semantic similarity scoring
  ↓
5. STATEMENT CLASSIFICATION (WITH VALIDATION)
   - Initial stance classification (SUPPORT|OPPOSE|MIXED|NEUTRAL|UNCLEAR)
   - Validation pass: check against party profile
   - Confidence scoring (0.0-1.0)
   - Alternative interpretation reporting
   - Ambiguity flagging for review
  ↓
6. THREE-DIMENSIONAL ALIGNMENT SCORING
   - Semantic similarity (cosine of embeddings): 50%
   - Topic match (deterministic): 25%
   - Stance direction match: 25%
   - Final weighted score: 0.0-1.0
  ↓
7. RESULTS & COMPARISON
   - Programme positions vs. notulen statements
   - Topic-based grouping
   - Contradiction detection
   - Exportable brief
  ↓
Output: Party analysis with 70-80%+ confidence
```

---

## Detailed Steps

### Step 1: Fix Notulen Truncation & Fetch Missing Content
**Status**: Pending
**Priority**: CRITICAL (blocks everything)
**Effort**: Small

**Problem**: All documents truncated to 15,000 chars by `compress_text()`. Real notulen are 50K-200K chars.

**Actions**:
```python
# services/scraper.py modification
def compress_text(self, text: str, max_length: int = 15000) -> str:
    # Add optional max_length parameter
    # ... existing logic ...
    return result[:max_length]

# New method for notulen
def preserve_notulen_text(self, text: str) -> str:
    # No truncation, or very high limit (500K)
    return text.replace('\x00', '')
```

**Deliverable**: 6+ Rotterdam gemeenteraad notulen with full content

---

### Step 2: Migrate to `google.genai` SDK + Add pgvector
**Status**: Pending
**Priority**: HIGH (prereq for new AI code)
**Effort**: Small-Medium

**Actions**:
1. Update `requirements.txt`:
   ```
   google-genai>=0.1.0
   psycopg2-binary
   pgvector
   ```

2. Install pgvector extension:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   
   ALTER TABLE statement_chunks ADD COLUMN embedding vector(768);
   CREATE INDEX ON statement_chunks USING ivfflat (embedding vector_cosine_ops);
   ```

3. Migrate `services/ai_service.py`:
   ```python
   # Old: import google.generativeai as genai
   # New:
   import google.genai as genai
   ```

**Deliverable**: pgvector installed, embeddings table ready

---

### Step 3: Multi-Source Party Profile Extraction
**Status**: Code written (`services/party_profile_service.py`)
**Priority**: HIGH
**Effort**: Medium

**Key Innovation**: Extract party profile from THREE sources to ground accuracy in actual behaviour:

#### 3a. Programme Positions
- Send full programme text to Gemini
- Extract policy positions per topic
- Get rhetorical tone + priority topics
- **Cost**: 1 API call (~55K input, ~20K output)

#### 3b. Notulen Statements (GroenLinks + PvdA combined)
- Query all 6 Rotterdam gemeenteraad notulen
- For each notulen: send to Gemini asking for GroenLinks-PvdA statements
- Extract: speaker, statement, topic, stance, context
- **Cost**: ~6 API calls (~30K input per call, ~10K output)

#### 3c. Moties/Amendementen
- Query ~46 documents mentioning GroenLinks or PvdA
- Extract: what's being proposed, party stance, outcome
- **Cost**: ~3-5 API calls (moties are short, batch them)

#### 3d. Synthesis
- Combine all three sources into unified profile
- Compare programme vs. actual behaviour
- Flag contradictions
- Assess internal consistency (0.0-1.0)
- **Cost**: 1 API call (~30K input)

**Total cost**: ~10-12 Gemini API calls (fits comfortably in free tier)

**Output**:
```json
{
  "party_name": "GroenLinks-PvdA",
  "overall_ideology": "center-left, progressive, social-democratic",
  "priority_topics": ["Klimaat & Milieu", "Bouwen & Wonen", ...],
  "rhetorical_tone": "idealistic, community-focused, activist",
  "topic_profiles": {
    "Klimaat & Milieu": {
      "position": "Synthesis from programme + notulen + moties",
      "direction": "left",
      "strength": "STRONG",
      "evidence": "mentioned in [programme, notulen, moties]",
      "consistency": 0.92
    },
    ...
  },
  "programme_vs_behaviour": {
    "overall_match": 0.87,
    "contradictions": ["contradiction1", ...]
  },
  "internal_consistency": 0.89,
  "data_sources_used": ["programme", "notulen", "moties"]
}
```

**Deliverable**: High-confidence party profile, presented for your review

---

### Step 4: Profile Review & Approval
**Status**: Pending
**Priority**: HIGH (gates downstream analysis)
**Effort**: Your decision (15 mins - 1 hour)

**What you do**:
1. Review the generated profile for factual accuracy
2. Identify any misinterpretations or gaps
3. Provide corrections (inline)
4. Approve for use in downstream analysis

**Why this matters**: The profile is the ground truth for validating all subsequent statements. If the profile is wrong, confidence drops. This step ensures accuracy.

**Deliverable**: Approved, corrected party profile

---

### Step 5: Atomic Chunking of All Documents
**Status**: Pending
**Priority**: HIGH (enables embedding)
**Effort**: Medium

**Programme Chunking**:
1. Send programme to Gemini with prompt: "Identify each distinct policy position. Return as a list of atomic bullets (1-3 sentences each)"
2. Tag each chunk with: topic_id, policy area, clarity score
3. Store in `statement_chunks` table with `source_type='programme'`

**Notulen Chunking**:
1. For each notulen, send to Gemini: "Extract each distinct statement by a GroenLinks-PvdA speaker"
2. Tag with: speaker name, date, topic_id, stance (initial)
3. Store in `statement_chunks`

**Moties Chunking**:
1. For each motie, extract: proposing party, proposal text, outcome
2. Tag with: topic_id, voting result
3. Store in `statement_chunks`

**Deliverable**: `statement_chunks` table populated with ~500-800 atomic chunks

---

### Step 6: Embedding Generation (pgvector)
**Status**: Pending
**Priority**: HIGH (core scoring layer)
**Effort**: Small

**Actions**:
```python
import google.genai as genai

def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Generate Gemini embeddings for a batch of texts"""
    response = genai.embed_content(
        model='models/text-embedding-004',
        content=texts
    )
    return response['embedding']

# For each chunk in statement_chunks:
# 1. Generate embedding
# 2. Store in embedding column
# 3. Create pgvector index for cosine similarity search
```

**Cost**: Free (Gemini embedding API included in free tier)

**Deliverable**: All `statement_chunks` have embeddings in pgvector

---

### Step 7: Classification + Validation Pass (High Confidence)
**Status**: Code written (`services/stance_validation_service.py`)
**Priority**: HIGH (core accuracy boost)
**Effort**: Medium

**This is the key innovation for 70-80%+ accuracy**

For each statement chunk:

#### Pass 1: Initial Classification
- Send statement to Gemini
- Get: stance (SUPPORT|OPPOSE|MIXED|NEUTRAL|UNCLEAR)

#### Pass 2: Validation Against Profile
- Send statement + party profile to Gemini
- Ask: "Does this align with the party's known position on [topic]?"
- Get:
  - Validated stance (may differ from initial)
  - Confidence (HIGH|MEDIUM|LOW)
  - Confidence score (0.0-1.0)
  - Consistency with profile (0.0-1.0)
  - Alternative interpretation (what would change the stance?)
  - Ambiguity flag (needs human review?)

#### Pass 3: Contradiction Detection
- Group statements by topic
- Identify opposing stances on same topic
- Flag for investigation

**Result**: Each statement has:
- Initial stance + validated stance
- Confidence score (0.0-1.0)
- Consistency metric
- Alternative interpretation
- Ambiguity flag

**Accuracy gain**: The validation pass catches misclassifications. Expected improvement: +10-15% over single-pass (74% → 70-80%+)

**Deliverable**: `statement_chunks` with validated stances + confidence

---

### Step 8: Three-Dimensional Alignment Scoring
**Status**: Pending
**Priority**: HIGH
**Effort**: Small-Medium

**Implements DGAP weighted similarity**

For each (programme_chunk, notulen_chunk) pair on the same topic:

**Dimension 1: Semantic Similarity (weight 0.50)**
```python
# pgvector cosine similarity
SELECT 
  chunk1.id, chunk2.id,
  1 - (chunk1.embedding <=> chunk2.embedding) as cosine_similarity
FROM statement_chunks chunk1
JOIN statement_chunks chunk2 
  ON chunk1.topic_id = chunk2.topic_id
  AND chunk1.source_type = 'programme'
  AND chunk2.source_type = 'notulen'
```

**Dimension 2: Topic Match (weight 0.25)**
```
Same topic_id: 1.0
Related topics: 0.5
Unrelated: 0.0
```

**Dimension 3: Stance Direction Match (weight 0.25)**
```
Both SUPPORT: 1.0
Both OPPOSE: 1.0
One SUPPORT, one OPPOSE: 0.0
MIXED or NEUTRAL: 0.5
UNCLEAR: 0.25
```

**Final Score**:
```
alignment_score = 
  (semantic_sim * 0.50) + 
  (topic_match * 0.25) + 
  (stance_match * 0.25)
```

Result: 0.0 (fully contradicting) to 1.0 (fully aligned)

**Deliverable**: `alignment_results` table with scored pairs

---

### Step 9: Results Storage & Retrieval
**Status**: Pending
**Priority**: MEDIUM
**Effort**: Small

**New API endpoints**:
```python
GET /api/party/GroenLinks-PvdA/profile
  → Returns party profile

GET /api/party/GroenLinks-PvdA/positions
  → Returns programme positions (from statement_chunks)

GET /api/party/GroenLinks-PvdA/statements
  → Returns notulen/motie statements (from statement_chunks)

GET /api/party/GroenLinks-PvdA/alignment
  → Returns alignment scores between programme and behaviour

GET /api/party/GroenLinks-PvdA/alignment?topic=Klimaat
  → Topic-specific alignment

GET /api/topics
  → List all 21 topic categories
```

**Deliverable**: Queryable API for analysis results

---

### Step 10: Party Analysis UI
**Status**: Pending
**Priority**: MEDIUM
**Effort**: Medium

**Template**: `templates/party_analysis.html`

**Layout**:
```
[Party: GroenLinks-PvdA] [Overall Consistency: 87%]

[Topic Filter: Klimaat & Milieu ▼]

┌─────────────────────────────────────────────────────────────┐
│ PROGRAMME POSITION                                           │
├─────────────────────────────────────────────────────────────┤
│ "Zero-emission zone by 2030. Ban fossil fuel heating by..."│
│ [Confidence: HIGH] [Consistency: 92%]                       │
│ Source: Verkiezingsprogramma 2026-2030                      │
└─────────────────────────────────────────────────────────────┘

          ALIGNMENT SCORE: 0.78 ████████░

┌─────────────────────────────────────────────────────────────┐
│ ACTUAL STATEMENTS (Notulen)                                 │
├─────────────────────────────────────────────────────────────┤
│ • "We support the climate action plan" (Nov 2024)           │
│   Speaker: Member X | [Confidence: HIGH]                    │
│ • "Market solutions are insufficient" (Jan 2024)            │
│   Speaker: Member Y | [Confidence: MEDIUM]                  │
└─────────────────────────────────────────────────────────────┘

⚠️  Potential contradiction: Members expressed different views 
    on market-based vs regulatory approaches
```

**Deliverable**: Interactive analysis page

---

### Step 11: Testing & Validation
**Status**: Pending
**Priority**: MEDIUM
**Effort**: Medium

**Spot-checks**:
1. Verify 5-10 programme position extractions against actual PDF
2. Verify 5-10 notulen statement extractions against actual text
3. Verify alignment scores make intuitive sense
4. Test edge cases (no programme data, only moties, etc.)
5. Verify party profile matches your domain knowledge

**Deliverable**: Validated system ready for use

---

## Database Schema Changes

### New Table: `statement_chunks`
```sql
CREATE TABLE statement_chunks (
  id SERIAL PRIMARY KEY,
  source_type TEXT NOT NULL,           -- 'programme', 'notulen', 'motie'
  source_document_id TEXT,
  party_name TEXT NOT NULL,
  speaker_name TEXT,
  chunk_text TEXT NOT NULL,
  chunk_index INTEGER,
  topic_id INTEGER REFERENCES topics(id),
  meeting_date TIMESTAMP,
  embedding vector(768),               -- Gemini embeddings
  stance TEXT,                         -- SUPPORT|OPPOSE|MIXED|NEUTRAL|UNCLEAR
  initial_stance TEXT,                 -- Before validation
  validated_stance TEXT,               -- After validation
  confidence TEXT,                     -- HIGH|MEDIUM|LOW
  confidence_score FLOAT,              -- 0.0-1.0
  consistency_with_profile FLOAT,      -- 0.0-1.0
  alternative_interpretation TEXT,
  is_ambiguous BOOLEAN DEFAULT FALSE,
  metadata JSONB,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ON statement_chunks USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON statement_chunks (party_name, topic_id);
CREATE INDEX ON statement_chunks (source_type);
```

### Updated Table: `party_programmes`
```sql
ALTER TABLE party_programmes ADD COLUMN profile_json JSONB;
ALTER TABLE party_programmes ADD COLUMN profile_confidence FLOAT;
ALTER TABLE party_programmes ADD COLUMN profile_approved BOOLEAN DEFAULT FALSE;
```

### New Table: `alignment_results`
```sql
CREATE TABLE alignment_results (
  id SERIAL PRIMARY KEY,
  programme_chunk_id INTEGER REFERENCES statement_chunks(id),
  statement_chunk_id INTEGER REFERENCES statement_chunks(id),
  semantic_score FLOAT,               -- 0.0-1.0
  topical_score FLOAT,                -- 0.0-1.0
  stance_score FLOAT,                 -- 0.0-1.0
  final_weighted_score FLOAT,         -- 0.0-1.0
  is_contradiction BOOLEAN,
  computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ON alignment_results (final_weighted_score DESC);
```

---

## Accuracy Pathway: 74% → 70-80%+

| Approach | Baseline | Technique | Expected Improvement |
|----------|----------|-----------|----------------------|
| Single LLM call | - | Generate content once | 74% (Benoit baseline) |
| **+ Validation pass** | 74% | Check against party profile | +10-15% |
| **+ Multi-source profile** | 84-89% | Ground truth from actual behaviour | +5-10% |
| **+ Confidence reporting** | 84-89% | Flag ambiguous cases, allow human review | +1-5% (quality, not quantity) |
| **FINAL** | - | All three combined | **70-80%+** |

The key insight: accuracy isn't just about the LLM. It's about:
1. **Grounded context** (profile from actual behaviour, not just promises)
2. **Validation** (check against profile, flag mismatches)
3. **Confidence reporting** (admit what you don't know, flag for review)

---

## Timeline & Effort Estimate

| Step | Effort | Timeline |
|------|--------|----------|
| 1. Fix truncation | 1-2 hrs | Day 1 |
| 2. SDK migration + pgvector | 2-3 hrs | Day 1 |
| 3. Profile extraction | 2-3 hrs | Day 2-3 (your review: 30 mins) |
| 4. Profile review | 30 mins | Day 3 |
| 5. Atomic chunking | 2-3 hrs | Day 3 |
| 6. Embeddings | 1 hr | Day 3-4 |
| 7. Classification + validation | 3-4 hrs | Day 4-5 |
| 8. Alignment scoring | 2-3 hrs | Day 5 |
| 9. API endpoints | 2-3 hrs | Day 5 |
| 10. UI page | 3-4 hrs | Day 6 |
| 11. Testing | 2-3 hrs | Day 6-7 |

**Total**: ~25-35 hours of development
**Your involvement**: ~30 minutes (profile review in Step 4)

---

## What We're Deferring

1. **Multi-agent pipeline** - single agent for now
2. **Dedicated vector DB** - pgvector sufficient
3. **Real-time monitoring** - batch analysis only
4. **Multi-party expansion** - one party (GroenLinks-PvdA) for PoC
5. **Ensemble re-scoring** - ambiguous items flagged but not auto-rerun

All can be added later without rearchitecting.

---

## Success Criteria

- ✅ Party profile matches your domain knowledge
- ✅ Alignment scores between programme and statements make intuitive sense
- ✅ Confidence scores are reliable (HIGH items have 85%+ accuracy, LOW items are genuinely ambiguous)
- ✅ Alternative interpretations explain plausible disagreements
- ✅ System correctly identifies and flags contradictions
- ✅ API returns results in <2 seconds for any query
- ✅ UI displays results clearly

---

## References

- DGAP Mapping Democracy: https://dgap.org/en/mapping-democracy-towards-ai-strengthened-political-discourse
- Benoit et al. (2025): Ask-and-average ensemble validation pattern
- Actor profiling (2025): +24-38% accuracy improvement with contextual profiles
- Politheon: Numeric scoring, source citation, exportable briefs

---

**Status**: Ready for execution
**Next step**: Begin Step 1 (Fix truncation)
