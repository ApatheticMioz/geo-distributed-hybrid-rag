import tantivy
idx = tantivy.Index.open('D:/Work/Semester6/NLP/Project_Laptop/systems/node_b/implementation/data/tantivy_index')
print('Node B Tantivy Docs:', idx.searcher().num_docs)
