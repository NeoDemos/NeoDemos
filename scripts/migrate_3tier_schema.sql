-- Migration to support Parent-Child-Grandchild hierarchy
CREATE TABLE IF NOT EXISTS document_children (
    id SERIAL PRIMARY KEY,
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    chunk_index INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB
);

-- Add child_id to grandchildren (the existing document_chunks table)
DO \$\$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='document_chunks' AND column_name='child_id') THEN
        ALTER TABLE document_chunks ADD COLUMN child_id INTEGER REFERENCES document_children(id) ON DELETE CASCADE;
    END IF;
END \$\$;

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_doc_children_doc_id ON document_children(document_id);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_child_id ON document_chunks(child_id);
