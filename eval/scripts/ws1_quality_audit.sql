-- WS1 Phase 1 Quality Audit — run AFTER all enrichment scripts complete
-- Checks acceptance criteria from docs/handoffs/WS1_GRAPHRAG.md
-- Usage: psql $DATABASE_URL -f eval/scripts/ws1_quality_audit.sql

\echo '============================================================'
\echo '  WS1 QUALITY AUDIT — Phase 1 Acceptance Criteria'
\echo '============================================================'
\echo ''

-- 1. KG edge count (target: >= 500K, was 57,633)
\echo '--- 1. kg_relationships total (target >= 500,000) ---'
SELECT COUNT(*) AS total_edges FROM kg_relationships;

\echo ''
\echo '--- 1b. Edges by relation_type ---'
SELECT relation_type, COUNT(*) AS edge_count
FROM kg_relationships
GROUP BY relation_type
ORDER BY edge_count DESC
LIMIT 20;

-- 2. key_entities coverage (target: >= 60%, was 25.2%)
\echo ''
\echo '--- 2. key_entities coverage (target >= 60%) ---'
SELECT
  COUNT(*) AS total_chunks,
  SUM(CASE WHEN key_entities IS NOT NULL AND array_length(key_entities, 1) > 0 THEN 1 ELSE 0 END) AS with_entities,
  ROUND(100.0 * SUM(CASE WHEN key_entities IS NOT NULL AND array_length(key_entities, 1) > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS coverage_pct
FROM document_chunks;

-- 3. BAG location skeleton (target: ~5K street nodes + ~5K LOCATED_IN edges)
\echo ''
\echo '--- 3. BAG location entities ---'
SELECT
  metadata->>'level' AS level,
  COUNT(*) AS entity_count
FROM kg_entities
WHERE type = 'Location'
  AND metadata->>'gemeente' = 'rotterdam'
  AND metadata->>'level' IS NOT NULL
GROUP BY metadata->>'level'
ORDER BY entity_count DESC;

\echo ''
\echo '--- 3b. LOCATED_IN edges ---'
SELECT COUNT(*) AS located_in_edges
FROM kg_relationships
WHERE relation_type = 'LOCATED_IN';

-- 4. BAG canonical key check (all Locations must have gemeente populated)
\echo ''
\echo '--- 4. Location entities missing gemeente (should be 0) ---'
SELECT COUNT(*) AS locations_without_gemeente
FROM kg_entities
WHERE type = 'Location'
  AND (metadata->>'gemeente' IS NULL OR metadata->>'gemeente' = '');

-- 5. Motie-notulen cross-document links
\echo ''
\echo '--- 5. Cross-document motie<->notulen edges ---'
SELECT relation_type, COUNT(*) AS edge_count
FROM kg_relationships
WHERE relation_type IN ('DISCUSSED_IN', 'VOTED_IN')
GROUP BY relation_type;

-- 6. kg_mentions (chunk-entity link table) growth
\echo ''
\echo '--- 6. kg_mentions total (was 3,247,244) ---'
SELECT COUNT(*) AS total_mentions FROM kg_mentions;

-- 7. kg_entities type distribution (check for mistyped entities)
\echo ''
\echo '--- 7. Entity type distribution ---'
SELECT type, COUNT(*) AS entity_count
FROM kg_entities
GROUP BY type
ORDER BY entity_count DESC
LIMIT 20;

-- 8. Qdrant entity_ids backfill check (via chunk sample)
\echo ''
\echo '--- 8. Chunks with entity mentions (proxy for Qdrant backfill readiness) ---'
SELECT
  COUNT(DISTINCT chunk_id) AS chunks_with_mentions,
  (SELECT COUNT(*) FROM document_chunks) AS total_chunks,
  ROUND(100.0 * COUNT(DISTINCT chunk_id) / (SELECT COUNT(*) FROM document_chunks), 1) AS mention_coverage_pct
FROM kg_mentions;

-- 9. Heemraadssingel test (the specific 0-hit failure from baseline)
\echo ''
\echo '--- 9. Heemraadssingel in key_entities (should be >= 1 after gazetteer pass) ---'
SELECT COUNT(*) AS chunks_with_heemraadssingel
FROM document_chunks
WHERE 'Heemraadssingel' = ANY(key_entities)
   OR 'heemraadssingel' = ANY(key_entities);

-- 10. BM25 hit rate on moties for "gemeenteraad" (OCR quality proxy, WS7 target)
\echo ''
\echo '--- 10. BM25 hit rate moties (WS7 OCR target >= 95%) ---'
SELECT
  COUNT(*) AS total_moties,
  SUM(CASE WHEN text_search @@ to_tsquery('dutch', 'gemeenteraad') THEN 1 ELSE 0 END) AS bm25_hits,
  ROUND(100.0 * SUM(CASE WHEN text_search @@ to_tsquery('dutch', 'gemeenteraad') THEN 1 ELSE 0 END) / COUNT(*), 1) AS hit_pct
FROM documents
WHERE LOWER(name) LIKE '%motie%'
  AND content IS NOT NULL
  AND LENGTH(content) > 100;

-- 11. Gemini-created edges (if Phase A Gemini ran)
\echo ''
\echo '--- 11. Gemini-sourced edges ---'
SELECT relation_type, COUNT(*) AS edge_count
FROM kg_relationships
WHERE metadata->>'source' = 'gemini_semantic_enrichment'
GROUP BY relation_type
ORDER BY edge_count DESC;

-- 12. NULL/orphan check
\echo ''
\echo '--- 12. Orphan edges (entity_id not in kg_entities) ---'
SELECT COUNT(*) AS orphan_source
FROM kg_relationships r
WHERE r.source_entity_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM kg_entities e WHERE e.id = r.source_entity_id);

SELECT COUNT(*) AS orphan_target
FROM kg_relationships r
WHERE r.target_entity_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM kg_entities e WHERE e.id = r.target_entity_id);

\echo ''
\echo '============================================================'
\echo '  AUDIT COMPLETE — compare against WS1 acceptance criteria'
\echo '============================================================'
