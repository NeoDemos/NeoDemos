# WS1 Pre-Enrichment Baseline — 2026-04-12

> **Purpose:** concrete "before" measurements for the 6 MCP chat replay sessions
> defined in [WS1_GRAPHRAG.md Eval gate Layer 2](../../docs/handoffs/WS1_GRAPHRAG.md).
> Run AFTER Phase 1 enrichment and compare against these results.
>
> **System state at baseline:** 57,633 kg edges (96% DIENT_IN) · 25.2% key_entities
> coverage · no graph_walk stream · no traceer_motie/vergelijk_partijen tools deployed ·
> no DISCUSSED_IN/VOTED_IN edges · no entity_ids in Qdrant payloads.
>
> **MCP server version:** v0.1.0 (13 tools, 4-stream retrieval).

---

## Scoring categories

| Category | What it measures | Scale |
|---|---|---|
| **Relevance** | Are results about the actual query topic? | 1-5 (5 = all results on-topic) |
| **Completeness** | Does the full picture emerge? (all parties, all moties, timeline) | 1-5 (5 = nothing important missing) |
| **Slot efficiency** | What % of result slots contain unique, useful content? | % (unique_useful / total_slots) |
| **Metadata quality** | Are indieners, vote_counts, coalition data structured + available? | 1-5 (5 = fully structured) |
| **OCR quality** | Is text readable and BM25-searchable? | 1-5 (5 = clean text) |
| **Cross-doc linking** | Can you trace motie → notulen discussion → vote? | 1-5 (5 = full chain) |
| **Party differentiation** | Do party-filtered results reflect that party's actual position? | 1-5 (5 = clearly differentiated) |
| **Political reliability** | Could this output support correct political interpretation? | 1-5 (5 = safe to publish) |

---

## R1 — Tweebosbuurt 2018 stemming

**Tool:** `zoek_moties(onderwerp="Tweebosbuurt sloop", datum_van="2018-01-01", datum_tot="2019-12-31")`
**Results returned:** 10

### Key observations

- Found the right moties: "Stop de sloop" (verworpen), "Behoud sociale huurwoningen" (verworpen), "Bouwen aan een nieuwe buurt met karakter" (aangenomen), "Zeg nee tegen een yuppendorp" (verworpen), "Sociaal plan slopen" (verworpen).
- Vote outcomes are populated (from doc name parsing) but **no vote_counts** (voor/tegen numbers) on any result.
- **Indieners garbled or absent.** Best case: "I313)" on the aangenomen motie. Worst case: completely unparseable OCR noise ("Nab/0337", "ROTI'ERDAM").
- **No coalition-at-time data anywhere.** No way to determine whether GL/PvdA were coalitie or oppositie in Nov 2018.
- Content previews are heavily OCR-damaged — "ROTI'ERDAM", "Gïnîur'.z", "ﬁwopfﬁﬁﬁ". BM25 on these chunks would miss standard Dutch queries.

### Scores

| Category | Score | Notes |
|---|---|---|
| Relevance | 4/5 | All 10 results are about the Tweebosbuurt; one Oost-Sidelinge motie is borderline |
| Completeness | 3/5 | Key moties found, but no vote counts, no notulen debate context |
| Slot efficiency | 90% | 9/10 unique and useful |
| Metadata quality | 1/5 | vote_outcome from name parsing only; indieners garbled; no vote_counts; no coalition data |
| OCR quality | 2/5 | Heavily damaged across most moties; many chunks unsearchable via BM25 |
| Cross-doc linking | 1/5 | Zero notulen linkage; cannot trace debate or discussion context |
| Party differentiation | 1/5 | Cannot determine which party submitted which motie from structured data |
| Political reliability | 2/5 | Outcome (aangenomen/verworpen) is correct from name. But missing coalition context makes Tweebosbuurt-class framing errors inevitable |

**Composite: 1.9/5**

---

## R2 — Warmtebedrijf motie trace

**Tool:** `zoek_moties(onderwerp="Warmtebedrijf", datum_van="2019-01-01", datum_tot="2022-12-31")`
**Results returned:** 10

### Key observations

- Good variety: Enquete (aangenomen), Publiek kader (aangenomen), Grip op besluitvorming (aangenomen), Definitief stekker eruit (verworpen), Emissievrij warmtebedrijf (aangenomen), Maak gemeenten warm (aangenomen).
- **One clean indiener**: "Ruud van der Velden PvdD Rotterdam" on motie 3. Rest are OCR garbage ("ClHetwarmtebedrijfzogoedalsfaillietis", "Erzijnbin, Blad:7/9 '%").
- **Duplicate doc_id:** 6080322 and 6080367 are different chunks of the same "Tussenbericht moties inzake warmtebedrijf" — 2 of 10 slots wasted.
- **No vote_counts** on any result (voor/tegen numbers absent).
- **No cross-document links** — cannot see which raadsvergadering notulen discussed/voted each motie.
- `traceer_motie` does not exist yet — this is the best the current system can do.

### Scores

| Category | Score | Notes |
|---|---|---|
| Relevance | 5/5 | All 10 results are Warmtebedrijf moties/afdoeningsvoorstellen |
| Completeness | 3/5 | Key moties found, but missing vote_counts and debate context |
| Slot efficiency | 80% | 8/10 unique and useful; 2 are duplicate tussenbericht |
| Metadata quality | 1/5 | vote_outcome from name parsing; indieners garbled; no vote_counts |
| OCR quality | 2/5 | Many content previews are OCR-damaged beyond readability |
| Cross-doc linking | 1/5 | Zero — no DISCUSSED_IN / VOTED_IN edges exist |
| Party differentiation | 1/5 | Cannot reliably attribute moties to parties from current data |
| Political reliability | 2/5 | Uitkomst correct, but no path to reconstruct who-voted-what-and-why |

**Composite: 1.9/5**

---

## R3 — Heemraadssingel parkeren

**Tool:** `zoek_raadshistorie(vraag="Heemraadssingel parkeren")`
**Results returned:** 9

### Key observations

- **Zero results specifically about parking on the Heemraadssingel.** The system found parking-related docs (generic city-wide) and Heemraadssingel-related docs (cultuurhistorische verkenning about trees and bridges) separately, but not the intersection.
- Result [9] mentions Heemraadssingel in the context of "groenblauwe verbindingen" in a voorjaarsnota — tangential, not about parking complaints.
- Result [4] is a cultuurhistorische verkenning of the Heemraadssingel — relevant to the street but not to parking.
- Results [1], [3], [5], [7] are about parking in general (parkeerterreinen, fietsparkeren, bestemmingsplannen) with no Heemraadssingel connection.
- **Root cause:** key_entities only matched against doc titles (28% coverage). A chunk about "bewoners van de Heemraadssingel klagen over parkeerdruk" in a document titled "Voortgangsrapportage parkeren 2024" would get zero key_entities tags and be invisible to the payload filter. This is exactly the failure mode the chunk-text gazetteer quick-win (Phase A) targets.

### Scores

| Category | Score | Notes |
|---|---|---|
| Relevance | 2/5 | Some results touch parking or Heemraadssingel, but none address the actual topic (parking complaints on Heemraadssingel) |
| Completeness | 1/5 | The specific user question is unanswered |
| Slot efficiency | 30% | ~3/9 are marginally related; rest are noise |
| Metadata quality | N/A | Not a metadata question |
| OCR quality | 4/5 | These results happen to have decent OCR (not the motie-class docs) |
| Cross-doc linking | N/A | Not applicable for this query type |
| Party differentiation | N/A | Not a party question |
| Political reliability | 2/5 | System would present generic parking results as if they answer the Heemraadssingel question — misleading |

**Composite: 1.8/5** (applicable categories only)

---

## R4 — Partijvergelijking warmtenetten (baseline for vergelijk_partijen)

**Tools:** 3x `zoek_uitspraken(onderwerp="warmtenetten energietransitie", partij_of_raadslid=<party>)`
**Results returned:** LR: 4, GL-PvdA: 4, VVD: 7

### Key observations

**Leefbaar Rotterdam — 0/4 on-topic:**
- [1] Ombudsman discussion (2007) — zero relation to warmtenetten
- [2] Thorium centrales debate (2016) — energy-adjacent but not warmtenetten
- [3] Procedural: "welke moties zijn ingetrokken" (2005) — no content
- [4] General Voorjaarsnota remarks (2003) — no warmtenetten mention

**GroenLinks-PvdA — 1/4 on-topic:**
- [1] Coalition formation in Brielle (!!) — wrong municipality entirely
- [2] Warmtewet 2 langetermijn perspectief (2020) — ON TOPIC, directly about warmtenetten market regulation
- [3] DENK verkiezingsprogramma about warmtenet tarieven — this is a DENK document, not GroenLinks-PvdA. Party filter leaked.
- [4] Procedural commissie agenda — no content

**VVD — 1/7 on-topic:**
- [3] Van Groningen about energietransitie/windenergie (2020) — tangentially relevant
- All other results: 2003 budget debates, debate etiquette, Zakenfestival, international climate questions — zero warmtenetten content

**Total: 2 of 15 results are actually about warmtenetten for the specified party.** Slot efficiency is 13%. The system fundamentally cannot do party-differentiated topic retrieval — it filters on party name appearing in the text, not on the party actually speaking about the topic.

### Scores

| Category | Score | Notes |
|---|---|---|
| Relevance | 1/5 | 2/15 results on-topic across all three parties |
| Completeness | 1/5 | No picture emerges of any party's warmtenetten position |
| Slot efficiency | 13% | 2/15 useful — worst of all sessions |
| Metadata quality | N/A | Not a metadata question |
| OCR quality | 4/5 | Notulen text is generally clean |
| Cross-doc linking | N/A | |
| Party differentiation | 1/5 | Catastrophic: party filter returns noise, one result leaks wrong party (DENK in GL-PvdA bucket), wrong municipality (Brielle) |
| Political reliability | 1/5 | Any "vergelijking" based on this data would be meaningless at best, misleading at worst |

**Composite: 1.3/5** (applicable categories only)

---

## R5 — Woningbouw 10-jaar research

**Tool:** `scan_breed(vraag="woningbouwbeleid sociale huurwoningen Rotterdam")`
**Results returned:** 20

### Key observations

- **Score distribution is bimodal:** 0.74-0.77 for results 1-19 (odd-numbered), 0.05 for even-numbered. The even/odd interleaving suggests vector and BM25 result batches are merged without proper score normalization.
- **Duplicate doc_ids:**
  - 239879 appears 2x (#1, #3)
  - 6098225 appears 2x (#6, #8)
  - 6084802 appears 2x (#5, #13)
- Of 20 slots, ~14 unique documents, ~11 substantively useful.
- Results are topically relevant (woningbouw, woonvisie, verordeningen), but the picture is incomplete — no graph connections between moties and their outcomes, no timeline structure.
- **graph_walk stream does not exist yet** — 0 graph-contributed chunks (definitional baseline).
- Scores < 0.10 are noise (10 of 20 results).

### Scores

| Category | Score | Notes |
|---|---|---|
| Relevance | 4/5 | Top-10 results are woningbouw-related; bottom-10 are noise |
| Completeness | 2/5 | Broad but shallow — no motie-outcome linkage, no party positions, no timeline |
| Slot efficiency | 55% | ~11/20 unique and useful; 3 duplicates + 6 noise results |
| Metadata quality | 2/5 | Dates present; no structured vote or party data in scan_breed output |
| OCR quality | 3/5 | Mixed: some docs clean, a few garbled (result #9) |
| Cross-doc linking | 1/5 | No links between policy documents, moties, and outcomes |
| Party differentiation | 1/5 | No party attribution in scan_breed results |
| Political reliability | 3/5 | Topically correct but too shallow for a 10-year research question |

**Composite: 2.1/5**

---

## R6 — Haven verduurzaming dossier

**Tool:** `scan_breed(vraag="verduurzaming haven Rotterdam scheepvaart waterstof")`
**Results returned:** 20

### Key observations

- **Score distribution bimodal:** 0.68-0.73 for odd results, 0.05-0.07 for even results. Same interleaving artifact as R5.
- **Catastrophic duplicate:** doc_id 3256584 appears **5 times** (#1, #3, #7, #11, #13) — 25% of all slots consumed by a single notulen document from 2016-03-17. The chunks are different fragments of the same meeting, but the user sees the same document dominating the results.
- **Low-score noise:** 10/20 results have scores 0.05-0.07, well below the 0.15 floor recommended in FEEDBACK_LOG.
- "Haven van de toekomst" appears 3 times under different doc_ids (6113377, 6115032, 6116071) — these appear to be different sections of the same report that were ingested as separate documents.
- Effective unique relevant results: ~6-7 out of 20 slots.

### Scores

| Category | Score | Notes |
|---|---|---|
| Relevance | 3/5 | Top results on-topic (cruisevaart, duurzaamheidsmonitor, verduurzaming mobiliteit); bottom half is noise |
| Completeness | 2/5 | Missing waterstof (explicitly queried), missing windenergie havengebied, missing recente motie |
| Slot efficiency | 35% | ~7/20 unique and useful; 5x duplicate doc + 10 noise results |
| Metadata quality | 2/5 | Dates and doc_ids present; no structured policy/vote data |
| OCR quality | 3/5 | Generally readable but some garbled notulen |
| Cross-doc linking | 1/5 | Cannot trace from policy to debate to vote |
| Party differentiation | 1/5 | No party attribution in scan_breed |
| Political reliability | 3/5 | Topically correct where relevant but 65% of output is waste |

**Composite: 1.9/5**

---

## Summary scorecard

| Session | Relevance | Completeness | Slot eff. | Metadata | OCR | Cross-doc | Party diff. | Political reliability | **Composite** |
|---|---|---|---|---|---|---|---|---|---|
| R1 Tweebosbuurt | 4 | 3 | 90% | 1 | 2 | 1 | 1 | 2 | **1.9** |
| R2 Warmtebedrijf | 5 | 3 | 80% | 1 | 2 | 1 | 1 | 2 | **1.9** |
| R3 Heemraadssingel | 2 | 1 | 30% | - | 4 | - | - | 2 | **1.8** |
| R4 Warmtenetten partij | 1 | 1 | 13% | - | 4 | - | 1 | 1 | **1.3** |
| R5 Woningbouw | 4 | 2 | 55% | 2 | 3 | 1 | 1 | 3 | **2.1** |
| R6 Haven | 3 | 2 | 35% | 2 | 3 | 1 | 1 | 3 | **1.9** |
| **Mean** | **3.2** | **2.0** | **50%** | **1.5** | **3.0** | **1.0** | **1.0** | **2.2** | **1.8** |

### Systemic weaknesses (pre-enrichment)

1. **Cross-document linking = 1.0/5 across the board.** No edges between moties and their notulen discussions. This is the #1 blocker for traceer_motie and the biggest single improvement WS1 Phase A can deliver.

2. **Party differentiation = 1.0/5 across the board.** Party-filtered retrieval returns noise, not signal. Root cause: the filter matches party name in text, not speaker attribution or party membership. WS1's graph_walk (LID_VAN + SPREEKT_OVER) is designed to fix this.

3. **Metadata quality = 1.5/5.** Indieners are garbled OCR, vote_counts absent, coalition-at-time unavailable. The rule-based enrichment from Layer 1 partially extracted these but OCR damage on source PDFs limits the ceiling.

4. **Slot efficiency = 50%.** Half of all result slots are wasted on duplicates (same doc_id appearing 2-5 times) or noise (scores < 0.10). The WS4 dedup fix at the retrieval layer (not formatter) and score floor are prerequisites, not WS1 scope — but the 5th graph_walk stream should add genuinely new chunks rather than duplicating existing ones.

5. **Score distribution is bimodal** (0.68-0.77 vs 0.05-0.07) in scan_breed results, suggesting the vector/BM25 fusion score normalization is broken. Results are interleaved by source rather than ranked by merged score.

### WS1 Phase 1 target

After enrichment, the specific improvements expected:

| Weakness | WS1 fix | Target score |
|---|---|---|
| Cross-doc linking 1.0 | DISCUSSED_IN / VOTED_IN edges from link_motie_to_notulen.py | ≥ 3.5 |
| Party differentiation 1.0 | graph_walk LID_VAN + SPREEKT_OVER paths | ≥ 3.0 |
| Metadata quality 1.5 | Flair NER + Gemini enrichment + BAG locations | ≥ 3.0 |
| Heemraadssingel 0-hit | chunk-text gazetteer pass | ≥ 1 hit (binary pass/fail) |
| Political reliability 2.2 | coalition_history in primer + structured vote data | ≥ 3.5 |

**Overall composite target: ≥ 3.0/5** (from current 1.8/5).

Categories NOT expected to improve from WS1 alone:
- Slot efficiency (WS4 dedup at retrieval layer)
- OCR quality (source PDF problem, not enrichment)
- Score bimodality (fusion algorithm, not WS1 scope)
