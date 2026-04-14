# NeoDemos: Strategic Progress & Tooling Fixes
*Walkthrough of updates and validations performed on 2026-03-05*

## 🚀 Key Accomplishments

### 1. Zero-Dependency RAG Testing
I fixed the `scripts/quick_rag_test.py` script. It now uses the Gemini Embedding and Flash-Lite APIs directly, eliminating the need for local heavy libraries like `sentence-transformers`.
*   **Result:** You can now query the Qdrant database mid-flight to verify the intelligence of the chunks being created by the swarm.

# Knowledge Graph & Transcript Audit Walkthrough

## 1. Knowledge Graph Ingestion [SUCCESS]

The ingestion of the Knowledge Graph (GLiNER entities and mentions) is complete, covering 1.19M chunks and 11.47M entity mentions.

---

## 2. Transcript Audit: The "Aboutaleb in 2025" Mystery [SOLVED]

The user reported seeing Ahmed Aboutaleb as Mayor in 2025 meetings, which is chronologically impossible (his term ended in 2024).

### The Root Cause: UUID Mapping Collisions
We discovered that the file `ibabs_uuid_mapping.json` contains multiple meetings pointing to the same video/transcript UUID. When the bulk pipeline runs, it attaches the **same 2024 transcript content** to multiple 2025 and 2026 meeting records.

### Concrete Evidence (Audit Log)
The following 2025 and 2026 meetings were found to be sharing transcripts with older sessions:
- **6125068** (2025-05-19): Sharing transcript with 2 other meetings.
- **7683550** (2026-02-10): Sharing transcript with session 7683551.
- **Result**: RAG queries for these 2025/2026 dates retrieve 2024 content (Aboutaleb).

## 3. Recovery Strategy

Instead of a simple date repair, we must perform a **Transcript Realignment**:
1. **Purge**: Delete the contaminated `document_chunks` for any meeting affected by a mapping collision.
2. **Deduplicate**: Fix `ibabs_uuid_mapping.json` to ensure one-to-one integrity.
3. **Re-Ingest**: Re-run the transcription pipeline for the purged IDs to fetch their unique content.

---

## 4. Date Inconsistency Audit [DIAGNOSED]
The "Midnight Shift" originally investigated was actually a side-effect of this content contamination (wrong video = wrong metadata). Timezone offsets remain a minor display issue (1hr) that will be fixed in the next code update.
De foutmelding bij de standaard zoekfunctie is verholpen:
- 🛠️ **Dependency Fix**: `marked.js` is weer toegevoegd aan `base.html`. Hierdoor kan de AI-tekst weer correct worden omgezet en getoond.
- 🔄 **UI State Logic**: De "Deep Research" laadbox wordt nu alleen getoond als de toggle daadwerkelijk aan staat. Bij een standaard zoekopdracht blijft de interface nu rustig totdat het resultaat er is.

![Succesvol Zoekresultaat](/Users/dennistak/.gemini/antigravity/brain/cf8e9c98-ac99-45e0-91b0-62efbd4d247c/search_results_success_marked_v2_1772913118599.png)

### 2. Manual Safeguard for Phase C
Per your request, I have updated the automation logic. Phase C (Knowledge Graph) will **not** start automatically.
*   **Safety:** The swarm will complete the Phase B processing and mop-up, and then the system will pause for your explicit approval before scaling into Graph extraction.

### 3. Phase D Roadmap (Productization)
I've formalized the vision for Phase D in the `implementation_plan.md`:
*   **Citizen Chatbot:** Accessible RAG for the public.
*   **Councillor Workflow:** Speech drafting, editing tools, and motion-synthesis.
*   **Data Completeness:** Verified that 2018–2026 contains robust meeting counts (~130–270 per year).
*   **UI/UX:** Tighter search and mobile-readiness.

### 4. Hardware Resource Audit (M1 8GB)
I performed a real-time RAM audit to address your concerns about hitting 8GB limits:
*   **Qdrant:** ~172MB (2.1%)
*   **Postgres:** ~0.2% per process
*   **Conclusion:** The setup is highly optimized for Apple Silicon and has significant headroom for Phase C and D.

---

## 📈 Current Status
*   **Active Swarm:** 20 workers are processing Phase B1.
*   **Completion:** **10,223** documents (~14.4%) fully chunked and vectorized.
*   **Next Steps:** Continue swarm monitoring until Phase B1 completes.

---

## 💻 Running the Test
To test the baseline intelligence of the system right now, use:
```bash
.venv/bin/python3 scripts/quick_rag_test.py "Uw vraag hier"
```
