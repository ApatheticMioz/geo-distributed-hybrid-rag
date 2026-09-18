import sys, os
sys.path.insert(0, os.getcwd())
from src.bm25_retriever import BM25Retriever
retriever = BM25Retriever('data/tantivy_index')
res = retriever.query('distributed computing', top_k=2)
print('BM25 Query Success!')
for r in res: print(' -', r['doc_id'], r['score'])
