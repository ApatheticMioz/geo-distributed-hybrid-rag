import tantivy
idx = tantivy.Index.open('D:/FAST/Semester6/NLP/Project_Laptop/node_B/implementation/data/tantivy_index')
print('Node B Tantivy Docs:', idx.searcher().num_docs)
