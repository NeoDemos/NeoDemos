#!/usr/bin/env python3
"""Simple test of the chunking script"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from services.ai_service import AIService
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
import psycopg2

load_dotenv(dotenv_path='.env')

print("=" * 70)
print("CHUNKING TEST - Single Document")
print("=" * 70)

# Initialize
print("\n1. Initializing...")
ai_service = AIService()
qdrant_client = QdrantClient(path="./data/qdrant_storage")
print("✓ Services initialized")

# Fetch one document
print("\n2. Fetching first notulen document...")
conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/neodemos")
cursor = conn.cursor()
cursor.execute("""
    SELECT id, name, content, LENGTH(content) as size
    FROM documents
    WHERE name ILIKE '%notule%' AND content IS NOT NULL AND LENGTH(content) > 100
    ORDER BY LENGTH(content) DESC
    LIMIT 1
""")
doc_id, doc_name, content, size = cursor.fetchone()
cursor.close()
conn.close()
print(f"✓ Fetched: {doc_name[:50]}... ({size/1024:.1f} KB)")

# Test chunking
print("\n3. Testing semantic chunking with pre-chunking for large doc...")
max_content_size = 800000

if len(content) > max_content_size:
    print(f"  - Document size: {len(content)/1024:.0f} KB (>800KB threshold)")
    print(f"  - Pre-chunking into sections...")
    
    paras = content.split('\n\n')
    pre_chunks = []
    current_chunk = ""
    
    for para in paras:
        if len(current_chunk) + len(para) > max_content_size // 5:
            if current_chunk:
                pre_chunks.append(current_chunk)
            current_chunk = para
        else:
            current_chunk += "\n\n" + para if current_chunk else para
    
    if current_chunk:
        pre_chunks.append(current_chunk)
    
    print(f"  - Created {len(pre_chunks)} pre-chunks")
    
    # Test chunking the first section
    print(f"  - Testing section 1...")
    section = pre_chunks[0]
    
else:
    print(f"  - Document size: {len(content)/1024:.0f} KB (<800KB, processing directly)")
    section = content

# Test chunking
prompt = f"""Your goal is to analyze documents for RAG by splitting into meaningful pieces.

Each piece should be a separate unit of text that can stand alone.

The piece of text should be the exact text from the document.
DON'T change a single word. DON'T summarize.

Return ONLY valid JSON array with:
- title: string
- text: string (exact text)
- questions: string[] (3-5 questions)

DOCUMENT TO CHUNK:

{section[:3000]}

Return only JSON array."""

print(f"\n4. Sending {len(prompt)} char prompt to Gemini...")
start_time = time.time()

response = ai_service.client.models.generate_content(
    model='gemini-3-flash-preview',
    contents=prompt
)

elapsed = time.time() - start_time
print(f"✓ Got response in {elapsed:.1f}s")

response_text = response.text
print(f"  - Response length: {len(response_text)} chars")

if response_text:
    try:
        json_start = response_text.find('[')
        json_end = response_text.rfind(']') + 1
        json_str = response_text[json_start:json_end]
        chunks = json.loads(json_str)
        print(f"✓ Parsed {len(chunks)} chunks")
        
        if chunks:
            print(f"\n5. First chunk:")
            first = chunks[0]
            print(f"  - Title: {first.get('title', 'N/A')[:60]}")
            print(f"  - Text: {first.get('text', '')[:100]}...")
            print(f"  - Questions: {len(first.get('questions', []))} questions")
    except Exception as e:
        print(f"✗ Error parsing: {e}")
        print(f"  Response: {response_text[:200]}...")
else:
    print("✗ Empty response from Gemini")

print("\n" + "=" * 70)
print("✓ Test complete!")
print("=" * 70)
