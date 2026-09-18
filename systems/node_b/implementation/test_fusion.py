import sys, os
sys.path.insert(0, os.getcwd())
from src.fusion import reciprocal_rank_fusion
dense = [{'doc_id': '1', 'score': 0.9}]
sparse = [{'doc_id': '1', 'score': 15.0}]
fused = reciprocal_rank_fusion(dense, sparse)
print('Fusion OK, count:', len(fused))
