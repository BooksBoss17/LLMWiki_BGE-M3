"""
RAG 入库脚手架 — LLMWiki_BGE-M3 知识库
功能: 切片 → 向量化 → FAISS索引 → 检索
raw 库更新时重新运行此脚本即可同步 RAG 库

🔒 锁定区 — 本脚本的核心逻辑（切片策略、向量化流程、FAISS索引构建、检索器）
受权限控制保护。标准权限下只能运行脚本，不得修改代码。
如需调整切片策略或检索参数，需管理员权限。详见 SCHEMA.md 权限控制规则。
"""

import json
import os
import re
import sys
from pathlib import Path
import numpy as np
import faiss


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHARED_SCRIPTS = PROJECT_ROOT / "skills" / "_shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))

from project_paths import resolve_path


KNOWLEDGE_POINTS_RE = re.compile(
    r'^knowledge_points:[^\S\r\n]*\r?\n((?:^[ \t]*-[ \t]+.+(?:\r?\n|$))+)',
    re.MULTILINE,
)

# ============================================================
# 配置
# ============================================================
BASE = str(resolve_path("project.root", start=PROJECT_ROOT))
RAW = str(resolve_path("library.raw", start=PROJECT_ROOT))
RAG = str(resolve_path("library.rag", start=PROJECT_ROOT))
DATA_DIR = str(resolve_path("rag.data", start=PROJECT_ROOT))
OUTPUT_DIR = str(resolve_path("rag.index", start=PROJECT_ROOT))
MODEL_PATH = str(resolve_path("rag.models", start=PROJECT_ROOT) / "BAAI" / "bge-m3")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# 1. 切片器 (Chunker)
# ============================================================

def strip_frontmatter(content):
    """去除 YAML frontmatter"""
    return re.sub(r'^---\n.*?\n---\n*', '', content, flags=re.DOTALL)


def parse_frontmatter(content):
    """解析 frontmatter 为 dict"""
    m = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).split('\n'):
        kv = re.match(r'^(\w+):\s*(.*)', line)
        if kv:
            fm[kv.group(1)] = kv.group(2).strip().strip('"')
    return fm


def chunk_textbook_md(md_path, source_rel):
    """教材 MD 按标题边界切片"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    body = strip_frontmatter(content)
    lines = body.split('\n')

    chunks = []
    current_h2 = ""
    current_h3 = ""
    current_chunk_lines = []
    current_h4 = ""
    chunk_idx = 0

    # 书名 (H1)
    book_title = ""
    for line in lines:
        if line.startswith('# ') and not line.startswith('## '):
            book_title = line.strip('# ').strip()
            break

    def flush():
        nonlocal chunk_idx, current_chunk_lines
        if not current_chunk_lines:
            return
        text = '\n'.join(current_chunk_lines).strip()
        if len(text) < 20:
            current_chunk_lines = []
            return

        # 构建标题路径
        title_parts = [book_title] if book_title else []
        if current_h2:
            title_parts.append(current_h2)
        if current_h3:
            title_parts.append(current_h3)
        if current_h4:
            title_parts.append(current_h4)
        title = ' - '.join(title_parts)

        # 文件名衍生 chunk id
        base_name = os.path.splitext(os.path.basename(md_path))[0]
        chunk_id = f"textbook-{base_name}-{chunk_idx:04d}"

        chunks.append({
            "id": chunk_id,
            "source": source_rel,
            "source_type": "textbook",
            "title": title,
            "text": text,
            "chunk_index": chunk_idx,
        })
        chunk_idx += 1
        current_chunk_lines = []

    for line in lines:
        s = line.strip()
        if s.startswith('## ') and not s.startswith('### '):
            flush()
            current_h2 = s.lstrip('#').strip()
            current_h3 = ""
            current_h4 = ""
        elif s.startswith('### ') and not s.startswith('#### '):
            flush()
            current_h3 = s.lstrip('#').strip()
            current_h4 = ""
        elif s.startswith('#### ') and not s.startswith('##### '):
            flush()
            current_h4 = s.lstrip('#').strip()
        elif s.startswith('# ') and not s.startswith('## '):
            continue  # skip H1 book title
        else:
            current_chunk_lines.append(line)

    flush()
    return chunks


def chunk_standard_md(md_path, source_rel):
    """标准文档按 H2/H3 切片"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    body = strip_frontmatter(content)
    lines = body.split('\n')

    chunks = []
    doc_title = ""
    current_h2 = ""
    current_chunk_lines = []
    chunk_idx = 0

    for line in lines:
        if line.startswith('# ') and not line.startswith('## '):
            doc_title = line.strip('# ').strip()
            continue

    def flush():
        nonlocal chunk_idx, current_chunk_lines
        if not current_chunk_lines:
            return
        text = '\n'.join(current_chunk_lines).strip()
        if len(text) < 20:
            current_chunk_lines = []
            return
        title_parts = [doc_title] if doc_title else []
        if current_h2:
            title_parts.append(current_h2)
        title = ' - '.join(title_parts)
        base_name = os.path.splitext(os.path.basename(md_path))[0]
        chunk_id = f"standard-{base_name}-{chunk_idx:04d}"
        chunks.append({
            "id": chunk_id,
            "source": source_rel,
            "source_type": "standard",
            "title": title,
            "text": text,
            "chunk_index": chunk_idx,
        })
        chunk_idx += 1
        current_chunk_lines = []

    for line in lines:
        s = line.strip()
        if s.startswith('## ') and not s.startswith('### '):
            flush()
            current_h2 = s.lstrip('#').strip()
        elif s.startswith('### ') and not s.startswith('#### '):
            flush()
            current_h2 = current_h2 + " / " + s.lstrip('#').strip() if current_h2 else s.lstrip('#').strip()
        elif s.startswith('# '):
            continue
        else:
            current_chunk_lines.append(line)

    flush()
    return chunks


def chunk_exercise_md(md_path, source_rel):
    """题库每题一个 chunk（不切片）"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    fm = parse_frontmatter(content)
    body = strip_frontmatter(content)

    # 提取各段
    q_match = re.search(r'## 题目\n\n(.*?)(?=\n## 答案)', body, re.DOTALL)
    a_match = re.search(r'## 答案\n\n(.*?)(?=\n## 详解)', body, re.DOTALL)
    e_match = re.search(r'## 详解\n\n(.*)', body, re.DOTALL)

    q_text = q_match.group(1).strip() if q_match else ''
    a_text = a_match.group(1).strip() if a_match else ''
    e_text = e_match.group(1).strip() if e_match else ''

    # 清理图片引用（向量不需要图片）
    q_text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', q_text).strip()
    e_text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', e_text).strip()

    # 拼成完整文本
    full_text = f"题目：{q_text}\n答案：{a_text}\n详解：{e_text}"

    chunk_id = fm.get('id', os.path.splitext(os.path.basename(md_path))[0])

    # 提取 knowledge_points
    kp_section = KNOWLEDGE_POINTS_RE.search(content)
    kps = []
    if kp_section:
        kps = re.findall(r'^[ \t]*-[ \t]+(.+)$', kp_section.group(1), re.M)
        kps = [kp.strip().strip('"') for kp in kps]

    return [{
        "id": f"exercise-{chunk_id}",
        "source": source_rel,
        "source_type": "exercise",
        "title": f"{chunk_id} {fm.get('question_type', '')} {', '.join(kps)}",
        "text": full_text,
        "chunk_index": 0,
        "question_type": fm.get('question_type', ''),
        "knowledge_points": kps,
        "difficulty": float(fm.get('difficulty', 0.5)),
        "source_title": fm.get('source_title', ''),
    }]


# ============================================================
# 2. 扫描 raw 库 + 切片
# ============================================================

def scan_and_chunk():
    """扫描 raw/ 目录，切片所有 MD 文件"""
    all_chunks = []

    # 教材
    tb_dir = os.path.join(RAW, "textbooks")
    if os.path.isdir(tb_dir):
        for book in sorted(os.listdir(tb_dir)):
            book_dir = os.path.join(tb_dir, book)
            if not os.path.isdir(book_dir):
                continue
            for f in sorted(os.listdir(book_dir)):
                if not f.endswith('.md'):
                    continue
                md_path = os.path.join(book_dir, f)
                source_rel = f"../raw/textbooks/{book}/{f}"
                chunks = chunk_textbook_md(md_path, source_rel)
                all_chunks.extend(chunks)
                print(f"  📖 {book}/{f}: {len(chunks)} chunks")

    # 标准
    st_dir = os.path.join(RAW, "standards")
    if os.path.isdir(st_dir):
        for f in sorted(os.listdir(st_dir)):
            if not f.endswith('.md'):
                continue
            md_path = os.path.join(st_dir, f)
            source_rel = f"../raw/standards/{f}"
            chunks = chunk_standard_md(md_path, source_rel)
            all_chunks.extend(chunks)
            print(f"  📋 {f}: {len(chunks)} chunks")

    # 题库 (支持两级分类: 大类/子分类)
    ex_dir = os.path.join(RAW, "exercises")
    if os.path.isdir(ex_dir):
        for main_cat in sorted(os.listdir(ex_dir)):
            main_dir = os.path.join(ex_dir, main_cat)
            if not os.path.isdir(main_dir):
                continue
            # 检查是否有子分类目录
            sub_dirs = [d for d in os.listdir(main_dir) if os.path.isdir(os.path.join(main_dir, d)) and d != 'media']
            if sub_dirs:
                # 两级分类: 大类/子分类
                for sub_cat in sorted(sub_dirs):
                    sub_dir = os.path.join(main_dir, sub_cat)
                    for f in sorted(os.listdir(sub_dir)):
                        if not f.endswith('.md'):
                            continue
                        md_path = os.path.join(sub_dir, f)
                        source_rel = f"../raw/exercises/{main_cat}/{sub_cat}/{f}"
                        chunks = chunk_exercise_md(md_path, source_rel)
                        all_chunks.extend(chunks)
                    sub_count = len([c for c in all_chunks if c['source_type']=='exercise' and f"{main_cat}/{sub_cat}" in c['source']])
                    print(f"  ✏️ {main_cat}/{sub_cat}/: {sub_count} chunks")
            else:
                # 一级分类 (兼容旧结构)
                for f in sorted(os.listdir(main_dir)):
                    if not f.endswith('.md'):
                        continue
                    md_path = os.path.join(main_dir, f)
                    source_rel = f"../raw/exercises/{main_cat}/{f}"
                    chunks = chunk_exercise_md(md_path, source_rel)
                    all_chunks.extend(chunks)
                cat_count = len([c for c in all_chunks if c['source_type']=='exercise' and main_cat in c['source']])
                print(f"  ✏️ {main_cat}/: {cat_count} chunks")

    # 讲义（讲义本体+讲义设计，分别切片）
    lec_dir = os.path.join(RAW, "lectures")
    if os.path.isdir(lec_dir):
        for lec in sorted(os.listdir(lec_dir)):
            lec_dir_path = os.path.join(lec_dir, lec)
            if not os.path.isdir(lec_dir_path):
                continue
            for f in sorted(os.listdir(lec_dir_path)):
                if not f.endswith('.md'):
                    continue
                md_path = os.path.join(lec_dir_path, f)
                source_rel = f"../raw/lectures/{lec}/{f}"
                chunks = chunk_textbook_md(md_path, source_rel)
                # 讲义设计文件单独标记 source_type
                for c in chunks:
                    if '_设计' in f:
                        c['source_type'] = 'lesson_design'
                    else:
                        c['source_type'] = 'lecture'
                all_chunks.extend(chunks)
                print(f"  📝 {lec}/{f}: {len(chunks)} chunks")

    # 视频转写（只切知识笔记，不切逐字稿和教学简案）
    tr_dir = os.path.join(RAW, "transcripts")
    if os.path.isdir(tr_dir):
        for up in sorted(os.listdir(tr_dir)):
            up_dir = os.path.join(tr_dir, up)
            if not os.path.isdir(up_dir):
                continue
            for video in sorted(os.listdir(up_dir)):
                video_dir = os.path.join(up_dir, video)
                if not os.path.isdir(video_dir):
                    continue
                for f in sorted(os.listdir(video_dir)):
                    if not f.endswith('_知识笔记.md'):
                        continue
                    md_path = os.path.join(video_dir, f)
                    source_rel = f"../raw/transcripts/{up}/{video}/{f}"
                    chunks = chunk_textbook_md(md_path, source_rel)
                    for c in chunks:
                        c['source_type'] = 'transcript'
                    all_chunks.extend(chunks)
                    print(f"  🎬 {up}/{video}/{f}: {len(chunks)} chunks")

    return all_chunks


# ============================================================
# 3. 向量化 + 索引
# ============================================================

def build_index(all_chunks, model=None):
    """向量化 + 构建 FAISS 索引"""
    if model is None:
        from FlagEmbedding import BGEM3FlagModel
        print("Loading BGE-M3 model...")
        model = BGEM3FlagModel(MODEL_PATH, use_fp16=True)
        print("Model loaded")

    texts = [c["text"] for c in all_chunks]
    print(f"Encoding {len(texts)} chunks...")

    output = model.encode(
        texts,
        batch_size=16,
        max_length=1024,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False
    )

    dense_vecs = output["dense_vecs"]
    sparse_weights = output["lexical_weights"]

    # FAISS 索引 (dense)
    dim = dense_vecs.shape[1]
    index = faiss.IndexFlatIP(dim)  # Inner Product (向量已归一化)
    index.add(dense_vecs.astype(np.float32))

    print(f"FAISS index built: {index.ntotal} vectors, dim={dim}")

    return dense_vecs, sparse_weights, index


# ============================================================
# 4. 持久化
# ============================================================

def save_all(all_chunks, dense_vecs, sparse_weights, index):
    """保存所有数据"""
    # chunks.jsonl
    chunks_path = os.path.join(DATA_DIR, "chunks.jsonl")
    with open(chunks_path, 'w', encoding='utf-8') as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + '\n')
    print(f"✅ chunks.jsonl: {len(all_chunks)} chunks")

    # embeddings.npz
    emb_path = os.path.join(OUTPUT_DIR, "embeddings.npz")
    np.savez(emb_path, dense=dense_vecs.astype(np.float32))
    print(f"✅ embeddings.npz: {dense_vecs.shape}")

    # sparse_weights.json
    sparse_path = os.path.join(OUTPUT_DIR, "sparse_weights.json")
    sparse_serializable = [{k: float(v) for k, v in w.items()} for w in sparse_weights]
    with open(sparse_path, 'w', encoding='utf-8') as f:
        json.dump(sparse_serializable, f, ensure_ascii=False)
    print(f"✅ sparse_weights.json: {len(sparse_serializable)} entries")

    # FAISS index
    faiss_path = os.path.join(OUTPUT_DIR, "index.faiss")
    faiss.write_index(index, faiss_path)
    print(f"✅ index.faiss: {index.ntotal} vectors")

    # metadata.json (id → source 映射)
    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    metadata = {
        "total_chunks": len(all_chunks),
        "total_vectors": index.ntotal,
        "dim": dense_vecs.shape[1],
        "chunks": [{"id": c["id"], "source": c["source"], "source_type": c["source_type"],
                     "title": c["title"], "chunk_index": c.get("chunk_index", 0)}
                    for c in all_chunks],
    }
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"✅ metadata.json: {len(metadata['chunks'])} entries")


# ============================================================
# 5. 检索器
# ============================================================

class RAGRetriever:
    """RAG 检索器 — 从磁盘加载索引"""

    def __init__(self):
        self.model = None
        self.index = None
        self.metadata = None
        self.chunks = None
        self.sparse_weights = None
        self._loaded = False

    def load(self):
        """从磁盘加载索引和元数据"""
        from FlagEmbedding import BGEM3FlagModel

        self.model = BGEM3FlagModel(MODEL_PATH, use_fp16=True)

        self.index = faiss.read_index(os.path.join(OUTPUT_DIR, "index.faiss"))

        with open(os.path.join(OUTPUT_DIR, "metadata.json"), 'r', encoding='utf-8') as f:
            self.metadata = json.load(f)

        chunks = []
        with open(os.path.join(DATA_DIR, "chunks.jsonl"), 'r', encoding='utf-8') as f:
            for line in f:
                chunks.append(json.loads(line))
        self.chunks = chunks

        with open(os.path.join(OUTPUT_DIR, "sparse_weights.json"), 'r', encoding='utf-8') as f:
            self.sparse_weights = json.load(f)

        self._loaded = True
        print(f"Loaded: {len(chunks)} chunks, {self.index.ntotal} vectors")

    def search(self, query, k=5, mode="dense"):
        """
        检索 top-K chunks
        mode: "dense" (语义), "sparse" (关键词), "hybrid" (混合)
        """
        if not self._loaded:
            self.load()

        if mode in ("dense", "hybrid"):
            query_output = self.model.encode([query], return_dense=True, return_sparse=(mode == "hybrid"))
            query_vec = query_output["dense_vecs"].astype(np.float32)

            # Dense 检索
            scores, indices = self.index.search(query_vec, k)
            results = []
            for i, (score, idx) in enumerate(zip(scores[0], indices[0])):
                if idx < 0:
                    continue
                chunk = self.chunks[idx]
                results.append({
                    "rank": i + 1,
                    "score": float(score),
                    "id": chunk["id"],
                    "source": chunk["source"],
                    "source_type": chunk["source_type"],
                    "title": chunk["title"],
                    "text": chunk["text"][:200],
                })
            return results

        elif mode == "sparse":
            query_output = self.model.encode([query], return_dense=False, return_sparse=True)
            query_weights = query_output["lexical_weights"][0]

            # Sparse 检索 (类似 BM25)
            scores = np.zeros(len(self.chunks))
            for i, doc_weights in enumerate(self.sparse_weights):
                common_keys = set(query_weights.keys()) & set(doc_weights.keys())
                score = sum(query_weights[k] * doc_weights[k] for k in common_keys)
                scores[i] = score

            top_k = np.argsort(scores)[::-1][:k]
            results = []
            for i, idx in enumerate(top_k):
                if scores[idx] == 0:
                    continue
                chunk = self.chunks[idx]
                results.append({
                    "rank": i + 1,
                    "score": float(scores[idx]),
                    "id": chunk["id"],
                    "source": chunk["source"],
                    "source_type": chunk["source_type"],
                    "title": chunk["title"],
                    "text": chunk["text"][:200],
                })
            return results


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("RAG 入库 — LLMWiki_BGE-M3 知识库")
    print("=" * 60)

    # Step 1: 切片
    print("\n[1/3] 扫描 raw/ 并切片...")
    all_chunks = scan_and_chunk()
    print(f"\n总计: {len(all_chunks)} chunks")
    by_type = {}
    for c in all_chunks:
        by_type[c["source_type"]] = by_type.get(c["source_type"], 0) + 1
    for t, n in by_type.items():
        print(f"  {t}: {n}")

    # Step 2: 向量化 + 索引
    print("\n[2/3] 向量化 + 构建 FAISS 索引...")
    dense_vecs, sparse_weights, index = build_index(all_chunks)

    # Step 3: 持久化
    print("\n[3/3] 保存到磁盘...")
    save_all(all_chunks, dense_vecs, sparse_weights, index)

    print("\n" + "=" * 60)
    print(f"✅ RAG 入库完成! {len(all_chunks)} chunks, {index.ntotal} vectors")
    print("=" * 60)
    print("\n检索示例:")
    retriever = RAGRetriever()
    retriever.index = index
    retriever.chunks = all_chunks
    retriever.sparse_weights = [{k: float(v) for k, v in w.items()} for w in sparse_weights]
    retriever.metadata = {"total_chunks": len(all_chunks)}
    retriever.model = model if 'model' in dir() else None
    retriever._loaded = True

    # 如果 model 还在内存中，直接测试
    if retriever.model:
        results = retriever.search("电容器充电过程中电流为什么减小", k=3)
        for r in results:
            print(f"  #{r['rank']} score={r['score']:.4f} [{r['source_type']}] {r['title'][:50]}")
            print(f"       {r['text'][:80]}")
