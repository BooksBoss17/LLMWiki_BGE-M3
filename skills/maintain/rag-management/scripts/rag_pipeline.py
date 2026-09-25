"""RAG 入库脚手架 — LLMWiki_BGE-M3 知识库
功能: 切片 → 向量化 → FAISS索引 → 检索
raw 库更新时重新运行此脚本即可同步 RAG 库

Usage:
    cd <KB_ROOT>/BGE-M3
    runtime/env/Scripts/python.exe scripts/rag_pipeline.py

See rag-management SKILL.md for full documentation.
"""

import json
import os
import re
from pathlib import Path
import numpy as np
import faiss

# ============================================================
# 配置 — 按实际路径修改
# ============================================================
BASE = os.environ.get("KB_ROOT") or str(Path(__file__).resolve().parents[4])
RAW = os.path.join(BASE, "raw")
RAG = os.path.join(BASE, "BGE-M3")
DATA_DIR = os.path.join(RAG, "data")
OUTPUT_DIR = os.path.join(RAG, "output")
MODEL_PATH = os.path.join(RAG, "models", "BAAI", "bge-m3")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# 1. 切片器 (Chunker)
# ============================================================

def strip_frontmatter(content):
    return re.sub(r'^---\n.*?\n---\n*', '', content, flags=re.DOTALL)


def parse_frontmatter(content):
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
    """教材 MD 按标题边界切片 (H3/H4 为 chunk 边界)"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    body = strip_frontmatter(content)
    lines = body.split('\n')
    chunks = []
    current_h2 = ""
    current_h3 = ""
    current_h4 = ""
    current_lines = []
    chunk_idx = 0
    book_title = ""
    for line in lines:
        if line.startswith('# ') and not line.startswith('## '):
            book_title = line.strip('# ').strip()
            break

    def flush():
        nonlocal chunk_idx, current_lines
        if not current_lines:
            return
        text = '\n'.join(current_lines).strip()
        if len(text) < 20:
            current_lines = []
            return
        parts = [book_title] if book_title else []
        if current_h2: parts.append(current_h2)
        if current_h3: parts.append(current_h3)
        if current_h4: parts.append(current_h4)
        base_name = os.path.splitext(os.path.basename(md_path))[0]
        chunks.append({
            "id": f"textbook-{base_name}-{chunk_idx:04d}",
            "source": source_rel, "source_type": "textbook",
            "title": ' - '.join(parts), "text": text, "chunk_index": chunk_idx,
        })
        chunk_idx += 1
        current_lines = []

    for line in lines:
        s = line.strip()
        if s.startswith('## ') and not s.startswith('### '):
            flush(); current_h2 = s.lstrip('#').strip(); current_h3 = ""; current_h4 = ""
        elif s.startswith('### ') and not s.startswith('#### '):
            flush(); current_h3 = s.lstrip('#').strip(); current_h4 = ""
        elif s.startswith('#### ') and not s.startswith('##### '):
            flush(); current_h4 = s.lstrip('#').strip()
        elif s.startswith('# ') and not s.startswith('## '):
            continue
        else:
            current_lines.append(line)
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
    current_lines = []
    chunk_idx = 0
    for line in lines:
        if line.startswith('# ') and not line.startswith('## '):
            doc_title = line.strip('# ').strip()
            continue

    def flush():
        nonlocal chunk_idx, current_lines
        if not current_lines: return
        text = '\n'.join(current_lines).strip()
        if len(text) < 20: current_lines = []; return
        parts = [doc_title] if doc_title else []
        if current_h2: parts.append(current_h2)
        base_name = os.path.splitext(os.path.basename(md_path))[0]
        chunks.append({
            "id": f"standard-{base_name}-{chunk_idx:04d}",
            "source": source_rel, "source_type": "standard",
            "title": ' - '.join(parts), "text": text, "chunk_index": chunk_idx,
        })
        chunk_idx += 1
        current_lines = []

    for line in lines:
        s = line.strip()
        if s.startswith('## ') and not s.startswith('### '):
            flush(); current_h2 = s.lstrip('#').strip()
        elif s.startswith('### ') and not s.startswith('#### '):
            flush(); current_h2 = (current_h2 + " / " + s.lstrip('#').strip()) if current_h2 else s.lstrip('#').strip()
        elif s.startswith('# '): continue
        else: current_lines.append(line)
    flush()
    return chunks


def chunk_exercise_md(md_path, source_rel):
    """题库每题一个 chunk（不切片）"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    fm = parse_frontmatter(content)
    body = strip_frontmatter(content)
    q_m = re.search(r'## 题目\n\n(.*?)(?=\n## 答案)', body, re.DOTALL)
    a_m = re.search(r'## 答案\n\n(.*?)(?=\n## 详解)', body, re.DOTALL)
    e_m = re.search(r'## 详解\n\n(.*)', body, re.DOTALL)
    q_text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', q_m.group(1).strip()) if q_m else ''
    a_text = a_m.group(1).strip() if a_m else ''
    e_text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', e_m.group(1).strip()) if e_m else ''
    full_text = f"题目：{q_text}\n答案：{a_text}\n详解：{e_text}"
    chunk_id = fm.get('id', os.path.splitext(os.path.basename(md_path))[0])
    kp_section = re.search(r'^knowledge_points:\s*\n((?:  - .+\n)+)', content, re.M)
    kps = [kp.strip().strip('"') for kp in re.findall(r'^\s+-\s+(.+)$', kp_section.group(1), re.M)] if kp_section else []
    return [{
        "id": f"exercise-{chunk_id}", "source": source_rel, "source_type": "exercise",
        "title": f"{chunk_id} {fm.get('question_type', '')} {', '.join(kps)}",
        "text": full_text, "chunk_index": 0,
        "question_type": fm.get('question_type', ''),
        "knowledge_points": kps, "difficulty": float(fm.get('difficulty', 0.5)),
        "source_title": fm.get('source_title', ''),
    }]


# ============================================================
# 2. 扫描 raw + 切片
# ============================================================

def scan_and_chunk():
    all_chunks = []
    # 教材
    tb_dir = os.path.join(RAW, "textbooks")
    if os.path.isdir(tb_dir):
        for book in sorted(os.listdir(tb_dir)):
            book_dir = os.path.join(tb_dir, book)
            if not os.path.isdir(book_dir): continue
            for f in sorted(os.listdir(book_dir)):
                if not f.endswith('.md'): continue
                chunks = chunk_textbook_md(os.path.join(book_dir, f), f"../raw/textbooks/{book}/{f}")
                all_chunks.extend(chunks)
                print(f"  📖 {book}/{f}: {len(chunks)} chunks")
    # 标准
    st_dir = os.path.join(RAW, "standards")
    if os.path.isdir(st_dir):
        for f in sorted(os.listdir(st_dir)):
            if not f.endswith('.md'): continue
            chunks = chunk_standard_md(os.path.join(st_dir, f), f"../raw/standards/{f}")
            all_chunks.extend(chunks)
            print(f"  📋 {f}: {len(chunks)} chunks")
    # 题库
    ex_dir = os.path.join(RAW, "exercises")
    if os.path.isdir(ex_dir):
        for topic in sorted(os.listdir(ex_dir)):
            topic_dir = os.path.join(ex_dir, topic)
            if not os.path.isdir(topic_dir): continue
            count = 0
            for f in sorted(os.listdir(topic_dir)):
                if not f.endswith('.md'): continue
                chunks = chunk_exercise_md(os.path.join(topic_dir, f), f"../raw/exercises/{topic}/{f}")
                all_chunks.extend(chunks)
                count += 1
            print(f"  ✏️ {topic}/: {count} chunks")
    return all_chunks


# ============================================================
# 3. 向量化 + 索引
# ============================================================

def build_index(all_chunks, model=None):
    if model is None:
        from FlagEmbedding import BGEM3FlagModel
        print("Loading BGE-M3 model...")
        model = BGEM3FlagModel(MODEL_PATH, use_fp16=True)
        print("Model loaded")
    texts = [c["text"] for c in all_chunks]
    print(f"Encoding {len(texts)} chunks...")
    output = model.encode(texts, batch_size=16, max_length=1024,
                          return_dense=True, return_sparse=True, return_colbert_vecs=False)
    dense_vecs = output["dense_vecs"]
    sparse_weights = output["lexical_weights"]
    dim = dense_vecs.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(dense_vecs.astype(np.float32))
    print(f"FAISS index built: {index.ntotal} vectors, dim={dim}")
    return dense_vecs, sparse_weights, index


# ============================================================
# 4. 持久化
# ============================================================

def save_all(all_chunks, dense_vecs, sparse_weights, index):
    with open(os.path.join(DATA_DIR, "chunks.jsonl"), 'w', encoding='utf-8') as f:
        for c in all_chunks: f.write(json.dumps(c, ensure_ascii=False) + '\n')
    np.savez(os.path.join(OUTPUT_DIR, "embeddings.npz"), dense=dense_vecs.astype(np.float32))
    sparse_serializable = [{k: float(v) for k, v in w.items()} for w in sparse_weights]
    with open(os.path.join(OUTPUT_DIR, "sparse_weights.json"), 'w', encoding='utf-8') as f:
        json.dump(sparse_serializable, f, ensure_ascii=False)
    faiss.write_index(index, os.path.join(OUTPUT_DIR, "index.faiss"))
    metadata = {
        "total_chunks": len(all_chunks), "total_vectors": index.ntotal,
        "dim": dense_vecs.shape[1],
        "chunks": [{"id": c["id"], "source": c["source"], "source_type": c["source_type"],
                     "title": c["title"], "chunk_index": c.get("chunk_index", 0)} for c in all_chunks],
    }
    with open(os.path.join(OUTPUT_DIR, "metadata.json"), 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"✅ Saved: {len(all_chunks)} chunks, {index.ntotal} vectors")


# ============================================================
# 5. 检索器
# ============================================================

class RAGRetriever:
    def __init__(self):
        self.model = None; self.index = None; self.metadata = None
        self.chunks = None; self.sparse_weights = None; self._loaded = False

    def load(self):
        from FlagEmbedding import BGEM3FlagModel
        self.model = BGEM3FlagModel(MODEL_PATH, use_fp16=True)
        self.index = faiss.read_index(os.path.join(OUTPUT_DIR, "index.faiss"))
        with open(os.path.join(OUTPUT_DIR, "metadata.json"), 'r', encoding='utf-8') as f:
            self.metadata = json.load(f)
        chunks = []
        with open(os.path.join(DATA_DIR, "chunks.jsonl"), 'r', encoding='utf-8') as f:
            for line in f: chunks.append(json.loads(line))
        self.chunks = chunks
        with open(os.path.join(OUTPUT_DIR, "sparse_weights.json"), 'r', encoding='utf-8') as f:
            self.sparse_weights = json.load(f)
        self._loaded = True
        print(f"Loaded: {len(chunks)} chunks, {self.index.ntotal} vectors")

    def search(self, query, k=5, mode="dense"):
        if not self._loaded: self.load()
        if mode in ("dense", "hybrid"):
            q_out = self.model.encode([query], return_dense=True, return_sparse=(mode=="hybrid"))
            q_vec = q_out["dense_vecs"].astype(np.float32)
            scores, indices = self.index.search(q_vec, k)
            results = []
            for i, (score, idx) in enumerate(zip(scores[0], indices[0])):
                if idx < 0: continue
                c = self.chunks[idx]
                results.append({"rank": i+1, "score": float(score), "id": c["id"],
                    "source": c["source"], "source_type": c["source_type"],
                    "title": c["title"], "text": c["text"][:200]})
            return results
        elif mode == "sparse":
            q_out = self.model.encode([query], return_dense=False, return_sparse=True)
            qw = q_out["lexical_weights"][0]
            scores = np.zeros(len(self.chunks))
            for i, dw in enumerate(self.sparse_weights):
                common = set(qw.keys()) & set(dw.keys())
                scores[i] = sum(qw[k] * dw[k] for k in common)
            top_k = np.argsort(scores)[::-1][:k]
            results = []
            for i, idx in enumerate(top_k):
                if scores[idx] == 0: continue
                c = self.chunks[idx]
                results.append({"rank": i+1, "score": float(scores[idx]), "id": c["id"],
                    "source": c["source"], "source_type": c["source_type"],
                    "title": c["title"], "text": c["text"][:200]})
            return results


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("RAG 入库 — LLMWiki_BGE-M3 知识库")
    print("=" * 60)
    print("\n[1/3] 扫描 raw/ 并切片...")
    all_chunks = scan_and_chunk()
    print(f"\n总计: {len(all_chunks)} chunks")
    by_type = {}
    for c in all_chunks: by_type[c["source_type"]] = by_type.get(c["source_type"], 0) + 1
    for t, n in by_type.items(): print(f"  {t}: {n}")
    print("\n[2/3] 向量化 + 构建 FAISS 索引...")
    dense_vecs, sparse_weights, index = build_index(all_chunks)
    print("\n[3/3] 保存到磁盘...")
    save_all(all_chunks, dense_vecs, sparse_weights, index)
    print("\n" + "=" * 60)
    print(f"✅ RAG 入库完成! {len(all_chunks)} chunks, {index.ntotal} vectors")
    print("=" * 60)
