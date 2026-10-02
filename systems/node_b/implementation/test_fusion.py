import sys, os
sys.path.insert(0, os.getcwd())
from src.fusion import reciprocal_rank_fusion
dense = ['1', '2']
sparse = ['2', '3']
fused = reciprocal_rank_fusion(sparse, dense)
print('Fusion OK, count:', len(fused), 'fused:', fused)

