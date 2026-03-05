#!/usr/bin/env python3
"""
Phase C: Knowledge Graph Schema + Entity Extraction
====================================================
Creates the graph tables (entities + relationships) and runs Gemini-powered
entity extraction across all document chunks to build the knowledge graph.

This maps:  people → authored → moties/amendementen
            fracties → voted_for/against → raadsvoorstellen
            documents → reference_budget → budget_lines
            notulen quotes → speaker → raadslid

Run AFTER Phase B (compute_embeddings.py):
  nohup python3 -u scripts/build_knowledge_graph.py > knowledge_graph.log 2>&1 &
"""

import json
import os
import sys
import time
import psycopg2
from typing import List, Dict, Any, Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from services.ai_service import AIService

load_dotenv()

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
EXTRACT_MODEL = "gemini-2.5-flash-lite"
RATE_LIMIT_SLEEP = 2.0
MIN_CONFIDENCE = 0.6


EXTRACTION_PROMPT = """You are extracting entities and relationships from a Dutch municipal council document chunk for a knowledge graph.

POLITICAL & FINANCIAL INTELLIGENCE FOCUS:
1. AUTHORSHIP & STANCE: When someone authors or speaks about a proposal, detect their STANCE (Pro, Anti, Nuanced, Concerned).
2. SENITMENT: For notulen quotes, detect the political sentiment towards the topic.
3. BUDGET HIERARCHY: If you see budget lines, detect parent/child relationships (e.g. "Onderwijs" is parent of "Scholenbouw").
4. CALCULATIONS: Identify sums, variances, and ratios.

Extract:
1. ENTITIES — people, fracties, topics, budget_lines (with amount/year), document_references.
2. RELATIONSHIPS — between those entities and the document or each other.

Return ONLY valid JSON:
{
  "entities": [
    {"type": "person", "name": "...", "role": "raadslid|wethouder|member of public", "fractie": "... (very important)"},
    {"type": "fractie", "name": "..."},
    {"type": "topic", "name": "..."},
    {"type": "budget_line", "name": "...", "amount": "...", "year": "..."}
  ],
  "relationships": [
    {
      "source": "... (entity name)", 
      "relation": "authored|voted_for|voted_against|speaks_about|amends|references_budget|is_parent_of|has_variance|has_ratio", 
      "target": "... (entity name or doc title)", 
      "confidence": 0.0-1.0, 
      "quote": "exact quote as evidence",
      "metadata": {
         "stance": "Pro|Anti|Nuanced|Concerned",
         "sentiment": "Positive|Negative|Neutral|Critical",
         "calculation": "percentage|diff|total",
         "value": "..." // e.g. "+5.2%" or "Total"
      }
    }
  ]
}

Relation guidance:
- "is_parent_of": budget_line A contains budget_line B
- "has_variance": budget_line A changed by X from previous year/budget
- "has_ratio": budget_line A is X% of budget_line B
- "authored": stance is mandatory in metadata

Only include relationships with confidence >= 0.5. Escape Dutch characters correctly.
If a fractie name is mentioned alone, extract it. If a person is mentioned, find their fractie.

DOCUMENT TYPE: {doc_type}
DOCUMENT: {doc_name}
CHUNK TITLE: {chunk_title}

CHUNK TEXT:
{text}"""


class KnowledgeGraphBuilder:

    def __init__(self):
        self.ai = AIService()
        if not self.ai.use_llm:
            print("❌ LLM not available.")
            sys.exit(1)

    def create_schema(self):
        """Create the knowledge graph tables."""
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        print("Creating knowledge graph schema (GraphRAG v2)...")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS kg_entities (
                id SERIAL PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                metadata JSONB DEFAULT '{}',
                summary TEXT,
                community_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(type, name)
            );
            CREATE INDEX IF NOT EXISTS idx_kg_entities_type ON kg_entities(type);
            CREATE INDEX IF NOT EXISTS idx_kg_entities_name ON kg_entities(name);
            CREATE INDEX IF NOT EXISTS idx_kg_entities_comm ON kg_entities(community_id);
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kg_relationships (
                id SERIAL PRIMARY KEY,
                source_entity_id INTEGER REFERENCES kg_entities(id) ON DELETE CASCADE,
                target_entity_id INTEGER REFERENCES kg_entities(id) ON DELETE CASCADE,
                relation_type TEXT NOT NULL,
                document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                chunk_id INTEGER REFERENCES document_chunks(id) ON DELETE SET NULL,
                confidence FLOAT DEFAULT 1.0,
                quote TEXT,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS kg_communities (
                id SERIAL PRIMARY KEY,
                name TEXT,
                summary TEXT,
                entity_count INTEGER,
                relationship_count INTEGER,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kg_extraction_log (
                chunk_id INTEGER PRIMARY KEY REFERENCES document_chunks(id) ON DELETE CASCADE,
                entities_found INTEGER DEFAULT 0,
                relationships_found INTEGER DEFAULT 0,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.commit()
        cur.close(); conn.close()
        print("✓ All tables synchronized.")

    def _extract_from_chunk(self, chunk_id, doc_id, doc_name, doc_type, chunk_title, text) -> Dict[str, Any]:
        """Call Gemini to extract entities and relationships."""
        prompt = EXTRACTION_PROMPT.format(
            doc_type=doc_type,
            doc_name=doc_name,
            chunk_title=chunk_title,
            text=text
        )
        try:
            response = self.ai.client.models.generate_content(
                model=EXTRACT_MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json"
                }
            )
            # Find JSON in the response
            text_resp = response.text or ""
            # Clean up markdown if present
            text_resp = re.sub(r"```json\s*", "", text_resp)
            text_resp = re.sub(r"\s*```", "", text_resp)
            
            return json.loads(text_resp)
        except Exception as e:
            print(f"  ❌ Extraction API error: {e}")
            return {"entities": [], "relationships": []}

    def _upsert_entity(self, cur, ent_type, name, metadata=None) -> Optional[int]:
        """Insert or update an entity and return its ID."""
        if not name or not ent_type: return None
        try:
            cur.execute("""
                INSERT INTO kg_entities (type, name, metadata)
                VALUES (%s, %s, %s)
                ON CONFLICT (type, name) DO UPDATE SET
                metadata = kg_entities.metadata || EXCLUDED.metadata
                RETURNING id
            """, (ent_type, name, json.dumps(metadata or {})))
            return cur.fetchone()[0]
        except Exception as e:
            print(f"  ❌ Upsert entity error ({name}): {e}")
            return None

    # ── Orchestration ─────────────────────────────────────────────────────────
    def detect_communities(self):
        """Use Louvain algorithm to cluster entities into communities."""
        import networkx as nx
        import community as community_louvain

        print("\n--- Starting Community Detection ---")
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        # Build graph from DB
        cur.execute("SELECT source_entity_id, target_entity_id FROM kg_relationships")
        edges = cur.fetchall()
        
        G = nx.Graph()
        G.add_edges_from(edges)
        
        if len(G.nodes) == 0:
            print("⚠ No nodes found in graph.")
            return

        print(f"Building communities for {len(G.nodes)} entities...")
        partition = community_louvain.best_partition(G)
        
        # Update entities with community IDs
        for node_id, comm_id in partition.items():
            cur.execute("UPDATE kg_entities SET community_id = %s WHERE id = %s", (comm_id, node_id))
        
        conn.commit()
        cur.close(); conn.close()
        print(f"✓ {len(set(partition.values()))} communities detected.")

    def summarize_communities(self):
        """Generate high-level summaries for each community (GraphRAG Global Context)."""
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        cur.execute("""
            SELECT community_id, count(*), string_agg(name, ', ' ORDER BY name)
            FROM kg_entities 
            WHERE community_id IS NOT NULL 
            GROUP BY community_id 
            HAVING count(*) >= 3
        """)
        communities = cur.fetchall()
        
        print(f"\n--- Summarizing {len(communities)} key communities ---")
        
        for comm_id, count, names in communities:
            # Get key relationships for this community
            cur.execute("""
                SELECT DISTINCT r.relation_type, se.name, te.name, r.metadata
                FROM kg_relationships r
                JOIN kg_entities se ON r.source_entity_id = se.id
                JOIN kg_entities te ON r.target_entity_id = te.id
                WHERE se.community_id = %s AND te.community_id = %s
                LIMIT 20
            """, (comm_id, comm_id))
            rels = cur.fetchall()
            rel_desc = "\n".join([f"- {s} {rt} {t} ({m})" for rt, s, t, m in rels])

            prompt = f"""Summarize this political/financial cluster for a Dutch municipal RAG graph.
COMMUNITY ID: {comm_id}
ENTITIES: {names}
KEY RELATIONSHIPS:
{rel_desc}

Describe:
1. The cohesive theme of this community.
2. The key political agents and their primary focus.
3. Financial significance (if any).
Return a concise 2-3 paragraph summary in Dutch."""

            try:
                print(f"Summarizing community {comm_id} ({count} entities)...")
                response = self.ai.client.models.generate_content(
                    model=EXTRACT_MODEL,
                    contents=prompt
                )
                summary = response.text or ""
                
                cur.execute("""
                    INSERT INTO kg_communities (id, name, summary, entity_count)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET summary = EXCLUDED.summary
                """, (comm_id, f"Community {comm_id}", summary, count))
                conn.commit()
            except Exception as e:
                print(f"  ❌ Community summary error: {e}")
                time.sleep(RATE_LIMIT_SLEEP)

        cur.close(); conn.close()
        print("✓ Community summarization complete.")

    # ── Orchestration ─────────────────────────────────────────────────────────

    def process_all_chunks(self):
        """Extract entities from all chunks and populate the knowledge graph."""
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        cur.execute("SELECT chunk_id FROM kg_extraction_log")
        done_ids = {row[0] for row in cur.fetchall()}
        
        cur.execute("""
            SELECT dc.id, dc.document_id, dc.title, dc.content, dc.chunk_type, d.name
            FROM document_chunks dc
            JOIN documents d ON dc.document_id = d.id
            WHERE dc.content IS NOT NULL AND length(dc.content) > 30
            ORDER BY dc.id
        """)
        chunks = cur.fetchall()
        cur.close(); conn.close()

        to_process = [c for c in chunks if c[0] not in done_ids]
        total = len(to_process)
        if total == 0:
            print("No new chunks to process.")
            return

        print(f"Chunks to process: {total}")

        for idx, (chunk_id, doc_id, chunk_title, text, chunk_type, doc_name) in enumerate(to_process, 1):
            # Same logic as before but wrapped for reliability
            try:
                result = self._extract_from_chunk(chunk_id, doc_id, doc_name, "auto", chunk_title, text)
                entities_raw = result.get("entities", [])
                relationships_raw = result.get("relationships", [])

                conn2 = psycopg2.connect(DB_URL)
                cur2 = conn2.cursor()
                entity_map = {}
                for ent in entities_raw:
                    eid = self._upsert_entity(cur2, ent.get("type", "overig"), ent.get("name", ""), ent)
                    if eid: entity_map[ent.get("name", "")] = eid
                
                doc_eid = self._upsert_entity(cur2, "document", doc_name)
                
                rel_count = 0
                for rel in relationships_raw:
                    src_id = entity_map.get(rel.get("source"))
                    tgt_id = entity_map.get(rel.get("target"))
                    if not src_id or not tgt_id: continue
                    
                    cur2.execute("""
                        INSERT INTO kg_relationships (source_entity_id, target_entity_id, relation_type, 
                        document_id, chunk_id, confidence, quote, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (src_id, tgt_id, rel.get("relation"), doc_id, chunk_id, 1.0, rel.get("quote"), json.dumps(rel.get("metadata", {}))))
                    rel_count += 1
                
                cur2.execute("INSERT INTO kg_extraction_log (chunk_id, entities_found, relationships_found) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", 
                            (chunk_id, len(entity_map), rel_count))
                conn2.commit(); cur2.close(); conn2.close()
                print(f"[{idx}/{total}] ✓ {len(entity_map)} ents, {rel_count} rels")
                time.sleep(RATE_LIMIT_SLEEP)
            except Exception as e:
                print(f"[{idx}/{total}] ❌ {e}")

def main():
    builder = KnowledgeGraphBuilder()
    builder.create_schema()
    builder.process_all_chunks()
    builder.detect_communities()
    builder.summarize_communities()

if __name__ == "__main__":
    main()
