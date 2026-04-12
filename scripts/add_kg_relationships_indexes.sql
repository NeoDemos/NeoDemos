-- Add missing indexes on kg_relationships
-- Context: graph_retrieval.py walk() CTE does WHERE (source_entity_id = X OR target_entity_id = X)
-- on every hop. Without indexes, each hop is a full table scan on 500K+ rows.
-- Expected impact: 2-hop walk latency from ~7s → ~150ms at 500K edges.
--
-- Uses CONCURRENTLY to avoid locking the table during creation.
-- Run on production via: psql $DATABASE_URL -f scripts/add_kg_relationships_indexes.sql
--
-- Reference: MASTER_PLAN.md §10, PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md (planned but never created)

-- Forward traversal: "what did entity X do?"
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kg_rel_source
  ON kg_relationships(source_entity_id);

-- Reverse traversal: "who voted against entity Y?"
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kg_rel_target
  ON kg_relationships(target_entity_id);

-- Compound: covers the walk() CTE's bidirectional OR pattern
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kg_rel_source_type
  ON kg_relationships(source_entity_id, relation_type);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kg_rel_target_type
  ON kg_relationships(target_entity_id, relation_type);

-- Edge type filtering (used by walk() edge_types parameter)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kg_rel_type
  ON kg_relationships(relation_type);

-- Temporal queries for future use
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kg_rel_source_date
  ON kg_relationships(source_entity_id, created_at);
