"""RAG 检索验证测试"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag_pipeline import RAGRetriever

retriever = RAGRetriever()
retriever.load()

queries = [
    "电容器充电过程中电流为什么减小",
    "机械能守恒的条件是什么",
    "理想变压器原副线圈电压电流的关系",
    "带电粒子在匀强磁场中做圆周运动的半径",
    "交变电流有效值怎么计算",
    "牛顿第二定律的内容和公式",
]

for q in queries:
    print(f"\n{'='*60}")
    print(f"🔍 查询: {q}")
    print(f"{'='*60}")
    
    # Dense 检索
    results = retriever.search(q, k=3, mode="dense")
    for r in results:
        print(f"  #{r['rank']} score={r['score']:.4f} [{r['source_type']}] {r['title'][:50]}")
        print(f"       {r['text'][:80]}")
