import sqlite3

conn = sqlite3.connect('corpus.sqlite')
cursor = conn.cursor()

# Get table names
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()
print(f"Tables: {tables}")

# Get schema for each table
for table in tables:
    table_name = table[0]
    cursor.execute(f"PRAGMA table_info({table_name});")
    columns = cursor.fetchall()
    print(f"\nTable: {table_name}")
    for col in columns:
        print(f"  {col[1]}: {col[2]}")

# Get sample row
if tables:
    first_table = tables[0][0]
    cursor.execute(f"SELECT * FROM {first_table} LIMIT 1;")
    sample = cursor.fetchone()
    print(f"\nSample from {first_table}: {sample}")

conn.close()
