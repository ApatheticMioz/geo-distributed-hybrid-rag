import tantivy
idx = tantivy.Index.open('D:/FAST/semester-project/NLP/project_node_b/node_B/implementation/data/tantivy_index')
print('Node B Tantivy Docs:', idx.searcher().num_docs)
