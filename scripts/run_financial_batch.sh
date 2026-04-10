#!/bin/bash
# Financial Document Batch Processor
# Runs Docling extraction on all pending PDFs via nohup (avoids sandbox kills).
# Usage:
#   ./scripts/run_financial_batch.sh              # Extract all pending
#   ./scripts/run_financial_batch.sh --workers 4  # Control parallelism
#   ./scripts/run_financial_batch.sh --status      # Check progress

set -euo pipefail
cd "$(dirname "$0")/.."

WORKERS="${1:-4}"
LOG_DIR="/tmp/docling_batch"
mkdir -p "$LOG_DIR"

if [[ "${1:-}" == "--status" ]]; then
    echo "=== Docling Batch Status ==="
    echo ""
    # Check running workers
    running=$(ps aux | grep "financial_ingestor\|run_docling_worker" | grep python | grep -v grep | wc -l | tr -d ' ')
    echo "Running workers: $running"
    echo ""
    # Check completed logs
    for f in "$LOG_DIR"/worker_*.log; do
        [ -f "$f" ] || continue
        name=$(basename "$f" .log)
        if grep -q "^Result:" "$f" 2>/dev/null; then
            result=$(grep "^Result:" "$f")
            echo "  DONE: $name — $result"
        elif grep -q "Traceback" "$f" 2>/dev/null; then
            err=$(grep -A1 "Error\|Exception" "$f" | tail -1)
            echo "  FAIL: $name — $err"
        else
            size=$(wc -c < "$f" | tr -d ' ')
            echo "  RUNNING: $name (log: ${size} bytes)"
        fi
    done
    exit 0
fi

# Source environment
source .venv/bin/activate 2>/dev/null || true
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0

echo "=== Financial Document Batch Extraction ==="
echo "Workers: $WORKERS"
echo "Log dir: $LOG_DIR"
echo ""

# Query staging for pending PDFs
PENDING=$(python -c "
import os, sys, psycopg2, json
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
h = os.getenv('DB_HOST', 'localhost')
p = os.getenv('DB_PORT', '5432')
d = os.getenv('DB_NAME', 'neodemos')
u = os.getenv('DB_USER', 'postgres')
pw = os.getenv('DB_PASSWORD', 'postgres')
url = f'postgresql://{u}:{pw}@{h}:{p}/{d}'
conn = psycopg2.connect(url)
cur = conn.cursor()
cur.execute('''
    SELECT id, doc_type, fiscal_year, pdf_path, source_url
    FROM staging.financial_documents
    WHERE pdf_path IS NOT NULL
      AND docling_tables_found IS NULL
      AND review_status = 'pending'
    ORDER BY fiscal_year DESC
''')
rows = cur.fetchall()
for r in rows:
    print(json.dumps({'id': r[0], 'doc_type': r[1], 'fiscal_year': r[2], 'pdf_path': r[3], 'source_url': r[4] or ''}))
cur.close()
conn.close()
")

if [ -z "$PENDING" ]; then
    echo "No pending PDFs found in staging.financial_documents"
    exit 0
fi

TOTAL=$(echo "$PENDING" | wc -l | tr -d ' ')
echo "Pending PDFs: $TOTAL"
echo ""

# Process with GNU parallel or xargs fallback
process_one() {
    local json_line="$1"
    local doc_id=$(echo "$json_line" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
    local doc_type=$(echo "$json_line" | python -c "import sys,json; print(json.load(sys.stdin)['doc_type'])")
    local fiscal_year=$(echo "$json_line" | python -c "import sys,json; print(json.load(sys.stdin)['fiscal_year'])")
    local pdf_path=$(echo "$json_line" | python -c "import sys,json; print(json.load(sys.stdin)['pdf_path'])")
    local source_url=$(echo "$json_line" | python -c "import sys,json; print(json.load(sys.stdin)['source_url'])")
    local doc_name="${doc_type} ${fiscal_year}"
    local log_file="$LOG_DIR/worker_${doc_id}.log"

    echo "  Starting: $doc_id ($doc_name) → $log_file"

    python -u -c "
import sys, os, time
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from pipeline.financial_ingestor import FinancialDocumentIngestor
start = time.time()
ingestor = FinancialDocumentIngestor()
result = ingestor.process_pdf(
    pdf_path='${pdf_path}',
    doc_id='${doc_id}',
    doc_name='${doc_name}',
    doc_type='${doc_type}',
    fiscal_year=${fiscal_year},
    source_url='${source_url}'
)
print(f'Result: {result}')
print(f'Time: {time.time()-start:.0f}s')
" > "$log_file" 2>&1
}

export -f process_one
export LOG_DIR

# Use xargs for parallelism (works on macOS without GNU parallel)
echo "$PENDING" | xargs -P "$WORKERS" -I {} bash -c 'process_one "$@"' _ {}

echo ""
echo "=== Batch Complete ==="
echo "Check results: $0 --status"
