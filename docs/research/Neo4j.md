This report evaluates the transition from a split-stack architecture (**Qdrant + PostgreSQL**) to a unified knowledge graph using **Neo4j** or **Apache AGE**, specifically tailored for 90,000 city council documents with **4096-dimensional int8 embeddings**.

---

# **Technical Evaluation: Knowledge Graph Migration Report**

## **1. Executive Summary**
For a dataset of 90,000 documents requiring both semantic search and relationship mapping, **Neo4j (v2026.02)** is the recommended target. While Apache AGE offers familiar relational benefits, Neo4j’s native support for **4096-dimensional vectors** and **int8 quantization** allows for a complete consolidation of your Qdrant Vector DB and metadata into a single, high-performance engine.

---

## **2. Database Comparison Matrix**

| Feature | **Neo4j (v2026.02)** | **Apache AGE + Postgres** |
| :--- | :--- | :--- |
| **Vector Dimensions** | Native support up to **4096** | Limited (pgvector usually caps at 2000 for HNSW) |
| **Quantization** | Native `INTEGER8` support | Requires custom bit-handling or `halfvec` |
| **Query Language** | **Cypher 25** (with native `SEARCH`) | SQL + Cypher (Hybrid) |
| **Graph Maturity** | Index-free adjacency (Deep hops) | Join-based (Shallow/Mid-depth hops) |
| **Complexity** | Low (Single DB for Graph + Vector) | Moderate (Multiple extensions required) |

---

## **3. The "Vector" Factor: 4096-Dim & Int8**
Your current Qdrant setup uses high-dimensional embeddings ($d=4096$) with `int8` quantization. This poses a challenge for traditional relational systems but is a core strength of the 2026 Neo4j release.

### **Memory Efficiency**
By retaining the `int8` quantization during migration, the memory footprint for your 90,000 documents remains remarkably lean:

$$90,000 \text{ docs} \times 4,096 \text{ dims} \times 1 \text{ byte (int8)} \approx 368.64 \text{ MB}$$

In contrast, converting these to `float32` would require approximately **1.47 GB** of RAM just for the vector storage, excluding index overhead.

### **In-Index Filtering**
Neo4j’s **Cypher 25** allows you to filter metadata *during* the vector search. This is critical for city council data where you may want to limit searches to specific years, council members, or committees without sacrificing search speed or recall.

---

## **4. Proposed Unified Architecture**

**Current State:**
* **Qdrant:** Stores 4096-dim `int8` vectors.
* **Postgres:** Stores document metadata.
* **Application:** Orchestrates joins between the two.

**Future State (Neo4j Consolidated):**
* **Neo4j Node:** `(:Document {text: string, embedding: VECTOR(INT8, 4096), date: date, ...})`
* **Benefits:** Zero-latency joins between semantic similarity and structural relationships (e.g., "Find documents similar to *Housing Reform* that were signed by *Councilor Smith*").

---

## **5. Migration Roadmap (Qdrant $\rightarrow$ Neo4j)**

### **Step 1: Index Definition**
Create an index optimized for your specific high-dimensional `int8` vectors:
```cypher
CREATE VECTOR INDEX council_doc_index IF NOT EXISTS
FOR (d:Document) ON d.embedding
WITH [d.council_member, d.year]
OPTIONS {
  indexConfig: {
    `vector.dimensions`: 4096,
    `vector.similarity_function`: 'cosine'
  }
}
```

### **Step 2: Data Ingestion**
Export from Qdrant via Python and load using the `CAST` function to preserve quantization:
```cypher
UNWIND $batch AS row
MERGE (d:Document {id: row.id})
SET d.embedding = CAST(row.vector, "VECTOR(INTEGER8, 4096)"),
    d.metadata = row.payload
```

### **Step 3: Unified Querying**
Execute semantic and graph discovery in a single block:
```cypher
MATCH (d:Document)
SEARCH d IN (
  VECTOR INDEX council_doc_index 
  FOR $queryVector 
  WHERE d.year > 2024
  LIMIT 5
)
MATCH (d)-[:PART_OF]->(m:Meeting)
RETURN d.text, m.transcript_url, score
```

---

## **6. Conclusion**
Given that you already have a Vector DB with 4096-dim `int8` embeddings, **Neo4j** provides the most seamless migration path. It eliminates the need for Apache AGE's complex SQL-to-Graph mapping and overcomes the dimensionality limits found in standard PostgreSQL vector extensions. Consolidating into Neo4j will reduce infrastructure overhead while significantly enhancing your ability to perform "GraphRAG" on city council data.