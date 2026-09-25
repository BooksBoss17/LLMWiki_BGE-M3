"""
LLMWiki_BGE-M3 知识库日常维护检查脚本
====================================
知识库: LLMWiki_BGE-M3 (高中物理教学, Wiki+RAG 双重检索)
路径: 由 KB_ROOT 环境变量或脚本位置自动推导
结构: raw/ -> LLMWiki/(Obsidian Wiki) + BGE-M3/(FAISS RAG)
区别: 本知识库自成一体，不依赖其他外部知识库
分类: 大类/子分类两级目录 (力学/运动学, 电磁学/电场, 等)

检查会影响检索的质量问题，发现问题输出报告，无问题静默。
"""
import os, re, json
from collections import Counter
from pathlib import Path

BASE = os.environ.get("KB_ROOT") or str(Path(__file__).resolve().parents[2])
EX_DIR = os.path.join(BASE, "raw", "exercises")
WIKI_DIR = os.path.join(BASE, "LLMWiki", "concepts")
RAG_DIR = os.path.join(BASE, "BGE-M3", "runtime", "index")

issues = []

# ===== 1. 题库检查 (两级分类: 大类/子分类) =====
total_q = 0
all_ids = []

def scan_exercise_dir(dir_path, rel_path):
    """递归扫描题库目录,支持大类/子分类两级结构"""
    media_files = set()
    media_dir = os.path.join(dir_path, "media")
    if os.path.exists(media_dir):
        media_files = set(os.listdir(media_dir))
    
    subdirs = []
    md_files = []
    for item in sorted(os.listdir(dir_path)):
        full = os.path.join(dir_path, item)
        if os.path.isdir(full) and item != 'media':
            subdirs.append((item, full))
        elif item.endswith('.md'):
            md_files.append(item)
    
    # Scan subdirectories (子分类)
    for sub_name, sub_path in subdirs:
        sub_media = os.path.join(sub_path, "media")
        sub_media_files = set(os.listdir(sub_media)) if os.path.exists(sub_media) else set()
        for fname in sorted(os.listdir(sub_path)):
            if not fname.endswith('.md'): continue
            fpath = os.path.join(sub_path, fname)
            check_exercise_file(fpath, f"{rel_path}/{sub_name}", fname, sub_media_files, sub_media)
    
    # Scan MD files directly in this directory (if not just subdirs)
    for fname in md_files:
        fpath = os.path.join(dir_path, fname)
        check_exercise_file(fpath, rel_path, fname, media_files, media_dir)

def check_exercise_file(fpath, rel_path, fname, media_files, media_dir):
    global total_q, all_ids, issues
    total_q += 1
    qid = fname.replace('.md', '')
    all_ids.append(qid)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    body = re.sub(r'^---\n.*?\n---\n*', '', content, flags=re.DOTALL)
    
    # $ 未闭合
    if body.count('$') % 2 != 0:
        issues.append(f"题库/{rel_path}/{qid}: $未闭合")
    
    # 答案=无
    a_match = re.search(r'## 答案\n\n(.+?)(?=\n## )', content, re.S)
    if a_match and a_match.group(1).strip() == "无":
        issues.append(f"题库/{rel_path}/{qid}: 答案=无")
    
    # 占位符
    e_match = re.search(r'## 详解\n\n(.+?)(?=\n## |$)', content, re.S)
    if e_match:
        expl = e_match.group(1).strip()
        if '见解析原件' in expl or '见答案' in expl:
            issues.append(f"题库/{rel_path}/{qid}: 详解有占位符")
        if len(expl) < 20:
            issues.append(f"题库/{rel_path}/{qid}: 详解短({len(expl)}c)")
    
    # 答案≠故选
    if a_match and e_match:
        answer = a_match.group(1).strip()
        explanation = e_match.group(1).strip()
        gu_xuan = re.search(r'故选\s*([A-Z]+)', explanation)
        if gu_xuan and answer != gu_xuan.group(1) and answer != "见详解":
            issues.append(f"题库/{rel_path}/{qid}: 答案={answer}≠故选={gu_xuan.group(1)}")
    
    # 图片断链 + WMF引用检查
    for img_ref in re.findall(r'!\[.*?\]\((media/[^)]+)\)', content):
        img_name = img_ref.split('/')[-1]
        if img_name not in media_files:
            issues.append(f"题库/{rel_path}/{qid}: 图片断链{img_ref}")
        if img_ref.endswith('.wmf'):
            issues.append(f"题库/{rel_path}/{qid}: WMF引用需转PNG {img_ref}")

if os.path.exists(EX_DIR):
    scan_exercise_dir(EX_DIR, "")
else:
    issues.append(f"题库目录不存在: {EX_DIR}")

# ID 连续性
id_nums = sorted([int(re.match(r'([A-Z]+)(\d+)', q).group(2)) for q in all_ids if re.match(r'([A-Z]+)(\d+)', q)])
dups = [n for n in id_nums if id_nums.count(n) > 1]
gaps = [f"{id_nums[i-1]+1}~{id_nums[i]-1}" for i in range(1, len(id_nums)) if id_nums[i] - id_nums[i-1] > 1]
if dups: issues.append(f"题库: ID重复 {set(dups)}")
if gaps: issues.append(f"题库: ID断续 {gaps}")

# ===== 2. Wiki 死链检查 =====
wiki_pages = [f for f in os.listdir(WIKI_DIR) if f.endswith('.md')] if os.path.exists(WIKI_DIR) else []
page_names = set(f.replace('.md', '') for f in wiki_pages)
dead_links = 0
total_links = 0
for f in wiki_pages:
    with open(os.path.join(WIKI_DIR, f), 'r', encoding='utf-8') as fh:
        content = fh.read()
    for m in re.finditer(r'\[\[([^\]|]+)', content):
        total_links += 1
        target = m.group(1).strip()
        if target not in page_names and not target.startswith('http'):
            dead_links += 1
            issues.append(f"Wiki/{f}: 死链[[{target}]]")

# ===== 3. RAG 完整性检查 =====
if os.path.exists(os.path.join(RAG_DIR, "metadata.json")):
    with open(os.path.join(RAG_DIR, "metadata.json"), 'r', encoding='utf-8') as f:
        meta = json.load(f)
    rag_chunks = meta.get("total_chunks", 0)
    if rag_chunks == 0:
        issues.append("RAG: chunks=0, 需重建")

# ===== 输出 =====
if issues:
    print(f"⚠️ 知识库维护检查发现 {len(issues)} 个问题:")
    print(f"  题库: {total_q}题, Wiki: {len(wiki_pages)}页, 死链: {dead_links}/{total_links}")
    for issue in issues:
        print(f"  ❌ {issue}")
else:
    # Silent when no issues (watchdog pattern)
    pass
