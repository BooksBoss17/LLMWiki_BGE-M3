from pathlib import Path
import sys

from FlagEmbedding import BGEM3FlagModel


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "skills" / "_shared" / "scripts"))
from project_paths import resolve_path


model = BGEM3FlagModel(
    str(resolve_path("rag.models", start=PROJECT_ROOT) / "BAAI" / "bge-m3"),
    use_fp16=True,
)

texts = [
    "导体棒在匀强磁场中切割磁感线，会产生动生电动势 E=BLv。",
    "电容器充电过程中，电容器两端电压逐渐增大，当 U_C=BLv 时电流趋于零。",
    "牛顿第二定律说明物体所受合外力等于质量与加速度的乘积。",
    "为什么电容器最后不再继续充电？",
]

output = model.encode(
    texts,
    batch_size=4,
    max_length=1024,
    return_dense=True,
    return_sparse=True,
    return_colbert_vecs=False,
)

dense_vecs = output["dense_vecs"]

print("dense 向量形状：", dense_vecs.shape)
print("第一条向量前 10 维：")
print(dense_vecs[0][:10])

query_vec = dense_vecs[3]
passage_vecs = dense_vecs[:3]
scores = passage_vecs @ query_vec

print("\n问题：为什么电容器最后不再继续充电？")
print("相似度分数：")

for i, score in enumerate(scores):
    print(f"材料 {i+1}: {score:.4f} - {texts[i]}")

print("\nsparse 权重示例：")
lexical_weights = output["lexical_weights"][0]
print(list(lexical_weights.items())[:20])
