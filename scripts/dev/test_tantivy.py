import tantivy
idx = tantivy.Index.open('D:/geo-distributed-hybrid-rag/node_b/node_B/implementation/data/tantivy_index')
print('Node B Tantivy Docs:', idx.searcher().num_docs)
