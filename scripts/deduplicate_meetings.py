import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def deduplicate():
    db_name = os.getenv("DB_NAME", "neodemos")
    db_user = os.getenv("DB_USER", "dennistak")
    db_password = os.getenv("DB_PASSWORD", "")
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")

    try:
        conn = psycopg2.connect(
            dbname=db_name,
            user=db_user,
            password=db_password,
            host=db_host,
            port=db_port
        )
        cur = conn.cursor()

        print("--- IDENTIFYING DUPLICATE MEETINGS ---")
        cur.execute("""
            SELECT start_date, name, array_agg(id) 
            FROM meetings 
            GROUP BY start_date, name 
            HAVING COUNT(*) > 1
        """)
        dupes = cur.fetchall()
        print(f"Total duplicate groups: {len(dupes)}")

        processed_count = 0
        for start_date, name, ids in dupes:
            # Find the "master" (the one with the most documents attached)
            counts = []
            for mid in ids:
                cur.execute("SELECT COUNT(*) FROM documents WHERE meeting_id = %s", (mid,))
                counts.append((cur.fetchone()[0], mid))
            
            # Sort by count desc
            counts.sort(reverse=True)
            master_id = counts[0][1]
            duplicates_to_remove = [c[1] for c in counts[1:]]

            for dupe_id in duplicates_to_remove:
                # 1. Update documents to point to master
                cur.execute("UPDATE documents SET meeting_id = %s WHERE meeting_id = %s", (master_id, dupe_id))
                # 2. Update agenda_items to point to master
                cur.execute("UPDATE agenda_items SET meeting_id = %s WHERE meeting_id = %s", (master_id, dupe_id))
                # 3. Delete the duplicate meeting
                cur.execute("DELETE FROM meetings WHERE id = %s", (dupe_id,))
            
            processed_count += 1
            if processed_count % 10 == 0:
                print(f"Processed {processed_count} groups...")

        conn.commit()
        print(f"SUCCESS: Deduplicated {len(dupes)} meeting groups.")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error during deduplication: {e}")

if __name__ == "__main__":
    deduplicate()
