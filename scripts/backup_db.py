import os
import psycopg2
import csv
import datetime

# Database connection details
DB_NAME = "neodemos"
DB_USER = "postgres"
DB_PASSWORD = "postgres"
DB_HOST = "localhost"
DB_PORT = "5432"

BACKUP_DIR = "data/db_backup_csv"

def backup_database():
    print(f"Starting CSV-based backup of {DB_NAME} to {BACKUP_DIR}...")
    
    try:
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR)
            
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        conn.autocommit = True
        
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            tables = [row[0] for row in cur.fetchall()]

            for table in tables:
                output_file = os.path.join(BACKUP_DIR, f"{table}.csv")
                print(f"  Exporting table: {table} to {output_file}...")
                
                # Using PostgreSQL's built-in COPY command is the fastest way to export
                # Since we are connecting via TCP, we use the cursor's copy_expert
                with open(output_file, 'w', encoding='utf-8') as f:
                    copy_sql = f"COPY {table} TO STDOUT WITH CSV HEADER"
                    cur.copy_expert(copy_sql, f)

        print("CSV backup completed successfully!")
        
        # Create a restore script helper
        restore_script = os.path.join(BACKUP_DIR, "restore_instructions.txt")
        with open(restore_script, "w") as f:
            f.write("To restore these CSVs on the Shadow PC:\n")
            for table in tables:
                f.write(f"psql -U postgres -d neodemos -c \"\\copy {table} FROM '{table}.csv' WITH CSV HEADER\"\n")
        
        conn.close()

    except Exception as e:
        print(f"Error during backup: {e}")

if __name__ == "__main__":
    backup_database()
