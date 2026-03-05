#!/usr/bin/env python3
"""
Quick unlink of non-target-city notulen (Part B2)

This is the synchronous, fast part of the data fix that just removes
incorrect links from the database. Full refetch is in fix_notulen_data.py.

Run from project root:
  python scripts/unlink_non_target_notulen.py rotterdam
"""

import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from scripts.fix_notulen_data import CityConfig


def unlink_non_target_notulen(city_name='rotterdam'):
    """Quickly unlink non-target-city notulen"""
    
    city_config = CityConfig.get(city_name)
    non_target_ids = city_config.get('known_wrong_docs', [])
    
    if not non_target_ids:
        print(f"No known non-{city_name} documents to unlink")
        return {'unlinked': 0, 'errors': []}
    
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', '5432')),
        'dbname': os.getenv('DB_NAME', 'neodemos'),
        'user': os.getenv('DB_USER', 'postgres'),
        'password': os.getenv('DB_PASSWORD', 'postgres')
    }
    
    stats = {'unlinked': 0, 'errors': []}
    
    try:
        conn = psycopg2.connect(**db_config)
        cur = conn.cursor()
        
        print(f"\nUnlinking {len(non_target_ids)} non-{city_name} notulen...")
        print("-" * 70)
        
        for doc_id in non_target_ids:
            try:
                cur.execute(
                    "UPDATE documents SET meeting_id = NULL WHERE id = %s AND meeting_id IS NOT NULL",
                    (doc_id,)
                )
                if cur.rowcount > 0:
                    stats['unlinked'] += 1
                    print(f"  ✓ Unlinked doc {doc_id}")
                else:
                    print(f"  - Doc {doc_id} not found or already unlinked")
            except Exception as e:
                error = f"Error unlinking {doc_id}: {e}"
                stats['errors'].append(error)
                print(f"  ✗ {error}")
        
        conn.commit()
        cur.close()
        conn.close()
        
        print("-" * 70)
        print(f"✓ Total unlinked: {stats['unlinked']}")
        
    except Exception as e:
        error = f"Database error: {e}"
        stats['errors'].append(error)
        print(f"✗ {error}")
    
    return stats


if __name__ == "__main__":
    load_dotenv()
    
    city = sys.argv[1] if len(sys.argv) > 1 else 'rotterdam'
    
    try:
        stats = unlink_non_target_notulen(city)
        if stats['errors']:
            print(f"\n⚠ {len(stats['errors'])} error(s) occurred")
            for err in stats['errors']:
                print(f"  - {err}")
            sys.exit(1)
        else:
            print(f"\n✓ Successfully unlinked {stats['unlinked']} documents")
            sys.exit(0)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
