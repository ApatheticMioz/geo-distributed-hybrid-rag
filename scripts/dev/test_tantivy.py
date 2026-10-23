import os
import tantivy

index_path = os.environ.get('TANTIVY_INDEX_PATH', 'systems/node_b/implementation/data/tantivy_index')
idx = tantivy.Index.open(index_path)
print('Node B Tantivy Docs:', idx.searcher().num_docs)
