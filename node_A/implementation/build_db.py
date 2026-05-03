import sqlite3
import csv
import sys

tsv_file = 'collection.tsv'
db_file = 'corpus.sqlite'

print("Connecting to SQLite database...")
conn = sqlite3.connect(db_file)
cursor = conn.cursor()

# Create a table with doc_id as the Primary Key for instant O(1) lookups
cursor.execute('''
    CREATE TABLE IF NOT EXISTS passages (
        doc_id TEXT PRIMARY KEY,
        text TEXT
    )
''')

print(f"Reading {tsv_file} and inserting into {db_file}...")
print("This might take a few minutes for 8.8 million rows. Please wait...")

# Read the TSV and insert in chunks to save memory
with open(tsv_file, 'r', encoding='utf-8') as f:
    # MS MARCO collection is strictly "id \t text"
    reader = csv.reader(f, delimiter='\t')
    batch = []
    count = 0
    
    for row in reader:
        if len(row) >= 2:
            batch.append((row[0], row[1]))
            count += 1
            
        # Commit every 100,000 rows
        if len(batch) >= 100000:
            cursor.executemany('INSERT OR IGNORE INTO passages VALUES (?, ?)', batch)
            conn.commit()
            print(f"Processed {count} passages...")
            batch = []
            
    # Commit the remaining rows
    if batch:
        cursor.executemany('INSERT OR IGNORE INTO passages VALUES (?, ?)', batch)
        conn.commit()
        print(f"Processed {count} passages...")

conn.close()
print("Done! corpus.sqlite is ready.")