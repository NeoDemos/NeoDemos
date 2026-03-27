-- Migration to support many-to-many relationships between meetings and documents

CREATE TABLE IF NOT EXISTS document_assignments (
    id SERIAL PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    meeting_id TEXT REFERENCES meetings(id) ON DELETE SET NULL,
    agenda_item_id TEXT REFERENCES agenda_items(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_id, meeting_id, agenda_item_id)
);

-- Backfill from existing data in documents table
-- Only insert if there's at least a meeting_id or agenda_item_id
INSERT INTO document_assignments (document_id, meeting_id, agenda_item_id)
SELECT id, meeting_id, agenda_item_id 
FROM documents 
WHERE meeting_id IS NOT NULL OR agenda_item_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- Indices for performance
CREATE INDEX IF NOT EXISTS idx_doc_assign_doc_id ON document_assignments(document_id);
CREATE INDEX IF NOT EXISTS idx_doc_assign_meeting_id ON document_assignments(meeting_id);
CREATE INDEX IF NOT EXISTS idx_doc_assign_agenda_id ON document_assignments(agenda_item_id);
