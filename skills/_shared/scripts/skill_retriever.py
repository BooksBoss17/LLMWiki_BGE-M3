#!/usr/bin/env python
"""Isolated hybrid retrieval for LLMWiki skill selection.

The teaching RAG index is intentionally not used here. This script only reads
skills/registry.yaml plus each skill's card.md/frontmatter and writes optional
cache files under skills/_ops/runtime/state/skill-retrieval/.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from project_paths import resolve_path

KB_ROOT = resolve_path("project.root", start=Path(__file__))
SKILLS_ROOT = resolve_path("skills.root", start=KB_ROOT)
REGISTRY = SKILLS_ROOT / "registry.yaml"
CACHE_DIR = resolve_path("skills.ops.runtime", start=KB_ROOT) / "state" / "skill-retrieval"
INDEX_JSON = CACHE_DIR / "skill_index.json"
VECTORS_JSON = CACHE_DIR / "skill_vectors.json"
FIXTURES = SKILLS_ROOT / "_registry" / "skill-routing-fixtures.jsonl"
BGE_MODEL = resolve_path("rag.models", start=KB_ROOT) / "BAAI" / "bge-m3"
BGE_PYTHON = resolve_path("rag.env", start=KB_ROOT) / "Scripts" / "python.exe"

CARD_FIELDS = ["use_when", "do_not_use_when", "input", "output", "next_skills"]

PATHS = {
    "bemarkdown": "import/bemarkdown/SKILL.md",
    "textbook": "import/textbook-import/SKILL.md",
    "standards": "import/standards-import/SKILL.md",
    "exercise": "import/exercise-bank-import/SKILL.md",
    "student_data_import": "import/student-data-import/SKILL.md",
    "handwriting_ocr": "import/chinese-handwriting-formula-transcriber/SKILL.md",
    "video": "import/video-transcript-import/SKILL.md",
    "rag_retrieval": "retrieve/llmwiki-rag-retrieval/SKILL.md",
    "lecture": "retrieve/lecture-generation/SKILL.md",
    "checklist": "retrieve/knowledge-checklist/SKILL.md",
    "docx": "retrieve/md-to-docx/SKILL.md",
    "format": "retrieve/teaching-output-format/SKILL.md",
    "wiki_maint": "maintain/llmwiki-maintenance/SKILL.md",
    "rag_manage": "maintain/rag-management/SKILL.md",
    "cleanup_import": "maintain/cleanup-import-workspace/SKILL.md",
    "cleanup_runtime": "maintain/cleanup-runtime-assets/SKILL.md",
    "framework_admin": "maintain/kb-framework-admin/SKILL.md",
    "agent_campaign": "maintain/agent-campaign-orchestration/SKILL.md",
    "package_update": "maintain/integration-package-update/SKILL.md",
    "git_manage": "maintain/git-repository-management/SKILL.md",
    "ppt": "analyze/teaching-ppt-analysis/SKILL.md",
    "student_data": "analyze/student-data-analysis/SKILL.md",
    "tutoring": "learn/physics-question-tutoring/SKILL.md",
    "diagram": "learn/physics-diagram-toolkit/SKILL.md",
    "solution_curation": "learn/exercise-solution-curation/SKILL.md",
    "physics_graph": "learn/physics-knowledge-graph/SKILL.md",
    "tags": "taxonomy/exercise-knowledge-tags/SKILL.md",
}

RULE_HINTS: dict[str, dict[str, Any]] = {
    PATHS["bemarkdown"]: {
        "priority": 60,
        "hints": [
            "docx",
            "pdf",
            "图片型pdf",
            "扫描pdf",
            "pdf图片转写",
            "omml",
            "eq",
            "wmf",
            "ocr",
            "本地ocr兜底",
            "paddleocr-vl pdf兜底",
            "marker",
            "公式",
            "转换",
            "markdown转换",
            "转markdown",
            "干净markdown",
        ],
        "negative": ["生成讲义", "组卷", "导出word", "试卷入库", "题库导入", "视频"],
    },
    PATHS["textbook"]: {
        "priority": 80,
        "hints": ["教材", "课本", "章节拆分", "课本入库", "教材导入"],
        "negative": ["试卷", "题库", "视频", "讲义生成"],
    },
    PATHS["standards"]: {
        "priority": 80,
        "hints": ["课程标准", "评价体系", "标准导入", "试题分析报告", "命题标准"],
        "negative": ["普通试卷", "学生练习", "讲义生成"],
    },
    PATHS["exercise"]: {
        "priority": 95,
        "hints": ["题库", "题库录入", "录入题库", "试卷", "试题", "习题", "作业", "题目提取", "抽取题目", "题目配对", "入库", "导入", "题库图片视觉索引同步", "组题前题库修复", "题干缺失修复", "原图错配修复", "组题缺陷回退"],
        "negative": ["仅检索", "简单解题", "不入库", "只回答"],
    },
    PATHS["student_data_import"]: {
        "priority": 98,
        "hints": [
            "studentdatasql导入",
            "学生数据导入",
            "名册导入",
            "导入名单",
            "成绩单导入",
            "小题分导入",
            "答题卡manifest导入",
            "答题卡导入",
            "学校成绩单",
            "考试成绩导入",
            "seat-only",
            "座位号导入",
            "录入成绩",
            "录入小题分",
            "录入答题卡",
            "导入答题卡manifest",
            "考试知识点映射回填",
            "studentdatasql知识点回填",
            "复用题库已有knowledge_point_ids",
        ],
        "negative": ["schema", "migration", "迁移", "框架", "router", "registry", "脚本维护", "修改脚本", "改脚本"],
    },
    PATHS["handwriting_ocr"]: {
        "priority": 99,
        "hints": [
            "中文手写转写",
            "手写公式ocr",
            "手写公式",
            "学生答题卡识别",
            "答题卡ocr",
            "答题卡作答区",
            "机器转写",
            "ocr转写",
            "paddleocr-vl",
            "pp-ocrv6",
            "pp-formulanet",
            "texteller",
            "本地ocr",
            "不上传学生数据",
        ],
        "negative": ["成绩分析", "小题分", "StudentDataSQL", "studentdatasql", "导入成绩", "知识点掌握", "失分分析"],
    },
    PATHS["video"]: {
        "priority": 95,
        "hints": ["b站", "视频", "转写", "字幕", "抽帧", "faster-whisper", "whisper", "关键帧", "vlm"],
        "negative": ["ppt", "word导出"],
    },
    PATHS["rag_retrieval"]: {
        "priority": 55,
        "hints": ["知识库检索", "检索", "查询", "回答", "解题", "解释", "备课", "知识点"],
        "negative": ["导入", "入库", "重建", "导出word", "生成讲义", "生成试卷", "学生拍题", "分层提示"],
    },
    PATHS["lecture"]: {
        "priority": 90,
        "hints": ["讲义", "作业生成", "复习", "组卷", "练习卷", "学案", "试卷生成", "题库原题组题", "题库原题", "原题原图", "原创组题", "原创题", "原创练习", "命题", "备课", "生成"],
        "negative": ["只导出", "仅排版", "已有markdown", "学生成绩", "作业分析", "学生数据", "学生数据库", "StudentDataSQL", "studentdatasql", "真实库", "密钥", "key", "脱敏班级周报"],
    },
    PATHS["docx"]: {
        "priority": 70,
        "hints": ["word", "docx", "pdf", "排版", "omml", "导出", "转word", "输出word"],
        "negative": ["导入试卷", "题库入库", "生成讲义并"],
    },
    PATHS["format"]: {
        "priority": 50,
        "hints": ["试卷格式", "讲义格式", "格式规范", "宋体", "times new roman", "样式", "版式"],
        "negative": ["导入", "题库", "检索"],
    },
    PATHS["wiki_maint"]: {
        "priority": 70,
        "hints": ["wiki维护", "死链", "孤立页", "图谱", "图谱健康", "视频三页", "index.md", "log.md"],
        "negative": ["简单问答", "导出word"],
    },
    PATHS["rag_manage"]: {
        "priority": 75,
        "hints": ["rag重建", "重建rag", "向量", "faiss", "索引", "chunks", "bge", "embedding", "metadata"],
        "negative": ["知识库检索", "简单检索", "解题", "生成讲义"],
    },
    PATHS["cleanup_import"]: {
        "priority": 110,
        "hints": ["清理导入任务临时文件", "清理已完成导入任务", "清理题库导入临时文件", "清理视频导入临时文件", "cleanup manifest", "cleanup_manifest"],
        "negative": ["全局清理", "共享runtime", "旧版程序", "rag缓存", "windows软件", "imports清理"],
    },
    PATHS["cleanup_runtime"]: {
        "priority": 115,
        "hints": ["全局清理tmp", "清理旧的临时文件", "清理临时文件和程序", "清理共享runtime", "清理旧版便携程序", "清理rag缓存", "清理程序残留", "runtime cleanup"],
        "negative": ["清理已完成导入任务", "题库导入临时", "视频导入临时", "studentdatasql", "imports清理", "卸载windows"],
    },
    PATHS["framework_admin"]: {
        "priority": 95,
        "hints": ["框架", "框架维护", "角色c", "role c", "知识库管理员", "registry", "router", "入口", "入口文件", "agents.md", "schema.md", "claude.md", "gemini.md", "shim", "agent shim", "角色ab", "角色a/b", "角色d", "创建角色d", "调整角色d", "测试角色", "扩大强档权限", "修改strong权限", "skill_retriever", "组题流程问题", "生成skill问题", "生成作业流程问题", "作业生成流程问题", "题源标记泄露", "答题区错误", "角色回退错误", "整合包", "部署包", "整合包完整性", "整合包边界", "新设备整合包", "package_boundary", "package boundary", "临时代码", "临时脚本", "temp-script", "归档临时代码", "清理临时代码", "studentdatasql框架", "sql学生数据库框架", "学生数据schema", "studentdatasql脚本维护", "成绩单预处理脚本", "答题卡导入脚本", "修改成绩单预处理脚本", "修改答题卡导入脚本"],
        "negative": ["导入试卷", "解题", "生成讲义", "重建rag", "普通导入", "录入成绩"],
    },
    PATHS["agent_campaign"]: {
        "priority": 108,
        "hints": ["agent campaign", "多 agent campaign", "多 agent 编排", "多智能体任务编排", "campaign 批次调度", "campaign 续跑", "campaign 状态汇总", "一次性子agent", "独立 auditor", "子agent审计", "长任务调度"],
        "negative": ["新增skill", "修改skill", "更新skill", "修改router", "更新router", "修改schema", "更新schema"],
    },
    PATHS["package_update"]: {
        "priority": 20,
        "hints": ["用户授意更新整合包", "授权更新整合包", "执行整合包同步", "同步整合包", "更新部署包", "同步部署包", "apply integration package"],
        "negative": ["检查整合包完整性", "整合包完整性", "整合包边界", "讨论整合包", "部署问题", "部署问题分析", "不要带生产数据"],
    },
    PATHS["git_manage"]: {
        "priority": 95,
        "hints": ["git", "git仓库", ".gitignore", "初始化仓库", "版本控制", "commit", "提交", "回滚", "git status", "gitignore"],
        "negative": ["导入试卷", "解题", "生成讲义"],
    },
    PATHS["ppt"]: {
        "priority": 80,
        "hints": ["ppt", "powerpoint", "课件", "投影", "课堂投影", "教学逻辑", "课件分析"],
        "negative": ["导出word", "题库入库"],
    },
    PATHS["student_data"]: {
        "priority": 98,
        "hints": [
            "sql学生数据分析",
            "studentdatasql",
            "STUDENT_DATA_DB_KEY",
            "真实库",
            "真实库key",
            "真实库密钥",
            "学生数据key",
            "学生数据密钥",
            "学生数据库",
            "学生数据分析",
            "学生成绩",
            "分析学生成绩",
            "成绩分析",
            "作业分析",
            "作业薄弱点",
            "日常作业分析",
            "学生掌握度",
            "单生期中知识点掌握",
            "单生知识点报告",
            "生成期中单生知识点报告",
            "期中考试报告",
            "期中报告",
            "期中考试知识点掌握报告",
            "座位号报告",
            "按座位号生成报告",
            "期中考掌握情况",
            "学生知识点掌握情况",
            "各知识点掌握情况",
            "期中考试各知识点",
            "任意一名学生",
            "班级薄弱点",
            "脱敏班级周报",
            "脱敏报告",
            "安全视图",
            "知识点掌握度",
            "考试小题分析",
            "方法错误分析",
            "知识点犯错",
            "答题卡分析",
            "考试成绩分析",
            "成绩情况",
            "小题分分析",
            "失分分析",
            "试卷和答题卡",
            "试卷答题卡",
        ],
        "negative": ["搭建", "创建", "schema", "migration", "迁移", "框架", "路由", "router", "git", "预处理", "导入", "录入", "导入预处理", "成绩单预处理", "成绩单导入", "小题分导入", "答题卡导入", "manifest", "名册导入", "两班两场考试", "seat-only"],
    },
    PATHS["tutoring"]: {
        "priority": 105,
        "hints": ["学生拍题", "拍照识别并讲解", "讲解这道题", "题意确认", "分层提示", "逐步提示", "物理题图片", "学习会话"],
        "negative": ["导入试卷", "题库导入", "教师备课", "修改题目解析", "成绩分析", "答题卡成绩"],
    },
    PATHS["diagram"]: {
        "priority": 110,
        "hints": ["物理示意图生成", "原创物理图", "原创物理示意图", "原图受力批注", "运动学批注", "视觉参考图", "实验数据图", "实验数据折线", "画受力图", "受力箭头", "坐标系图像", "函数图", "矢量图", "运动轨迹", "轨迹图", "图上批注", "添加箭头", "辅助线", "把受力图写入题库", "把示意图放入解析", "修复题库原图", "写入题库详解", "错误原图", "题库图写回"],
        "negative": ["ppt分析", "仅识别文字", "题库导入"],
    },
    PATHS["solution_curation"]: {
        "priority": 108,
        "hints": ["校对题库题干答案详解", "继续校对", "继续校对题库", "role d strong 校对题干答案详解", "题库校对断点续跑", "校对并修改题库", "修改题干", "修改题目解析", "修改解析", "补写解析", "补写详解", "修复解析", "答案解析不一致", "修复题目原图", "题库校对", "题库解析维护", "question id", "review exercise answer", "modify question stem", "detailed solution", "modify mc analysis", "proofread question bank answer"],
        "negative": ["学生拍题", "只讲解", "导入新试卷", "生成讲义"],
    },
    PATHS["physics_graph"]: {
        "priority": 107,
        "hints": ["高中物理知识关系图谱", "更新知识关系图谱", "知识关系图谱维护", "专题枢纽更新", "图谱反向链接", "import handoff", "import_handoff"],
        "negative": ["创建角色d", "一般wiki死链", "图谱健康检查", "学生成绩"],
    },
    PATHS["tags"]: {
        "priority": 100,
        "hints": ["知识点标签", "题库分类", "打标签", "knowledge_points", "knowledge_point_ids", "ai_extra_tags", "合并标签", "改名标签", "移动标签", "停用标签", "kp_id"],
        "negative": ["导入试卷", "抽取题目", "题目配对", "生成讲义"],
    },
}

STAGE_RULES = [
    ("integration_package_update", PATHS["package_update"], ["用户授意更新整合包", "授权更新整合包", "明确授意更新整合包", "执行整合包同步", "同步整合包", "更新整合包", "整合包更新", "更新部署包", "同步部署包", "apply integration package", "sync integration package"]),
    ("cleanup_import_workspace", PATHS["cleanup_import"], ["清理导入任务临时文件", "清理已完成导入任务", "清理题库导入临时文件", "清理视频导入临时文件", "cleanup manifest", "cleanup_manifest"]),
    ("cleanup_runtime_assets", PATHS["cleanup_runtime"], ["全局清理tmp", "清理旧的临时文件", "清理临时文件和程序", "清理共享runtime", "清理旧版便携程序", "清理rag缓存", "清理程序残留", "runtime cleanup"]),
    ("student_data_knowledge_mapping_repair", PATHS["student_data_import"], ["考试知识点映射回填", "StudentDataSQL知识点回填", "studentdatasql知识点回填", "复用题库已有knowledge_point_ids", "修复考试知识点映射"]),
    ("role_d_framework", PATHS["framework_admin"], ["创建角色d", "新增角色d", "搭建角色d", "调整角色d路由", "修改角色d权限", "强模型权限", "扩大强档权限", "修改strong权限", "role c", "role c 管理员", "测试角色d", "role d framework", "role-d-physics-learning", "skill门禁", "验证器", "validator", "schema升级"]),
    ("student_data_framework", PATHS["framework_admin"], ["搭建 sql", "搭建SQL", "搭建 SQL", "学生数据库框架", "studentdatasql 框架", "studentdatasql schema", "学生数据schema", "学生数据 schema", "sql学生数据库框架", "sql 学生数据库框架", "修改StudentDataSQL脚本", "修改成绩单预处理脚本", "修改答题卡导入脚本", "StudentDataSQL成绩单预处理脚本", "StudentDataSQL答题卡导入脚本", "StudentDataSQL脚本维护", "学生数据导入脚本维护"]),
    ("role_d_diagram", PATHS["diagram"], ["物理示意图生成", "原创物理图", "原创物理示意图", "题目情境图", "答案解析图", "原图受力批注", "运动学批注", "视觉参考图", "实验数据图", "实验数据折线", "实验数据散点", "画受力图", "画受力箭头", "受力箭头", "坐标系图像", "坐标系图", "函数图", "矢量图", "运动轨迹图", "轨迹图", "图上批注", "在原图上", "添加箭头", "画辅助线", "把受力图写入题库", "把示意图放入解析", "修复题库原图", "写入题库详解", "错误原图", "题库图写回"]),
    ("role_d_curation", PATHS["solution_curation"], ["校对题库题干答案详解", "继续校对", "继续校对题库", "role d strong 校对题干答案详解", "题库校对断点续跑", "校对并修改题库", "修改题干", "修改题目解析", "修改解析", "补写解析", "补写详解", "修复解析", "答案解析不一致", "修复题目原图", "题库校对", "题库解析维护", "review exercise answer", "modify question stem", "detailed solution", "modify mc analysis", "proofread question bank answer"]),
    ("role_d_graph", PATHS["physics_graph"], ["更新高中物理知识关系图谱", "高中物理知识关系图谱", "知识关系图谱维护", "专题枢纽更新", "图谱反向链接", "import handoff", "import_handoff"]),
    ("role_d_tutoring", PATHS["tutoring"], ["学生拍题", "拍照识别并讲解", "讲解这道题", "题意确认", "分层提示", "逐步提示", "物理题图片", "学习会话"]),
    ("bemd_image_pdf_conversion", PATHS["bemarkdown"], ["图片型PDF", "图片型pdf", "扫描PDF", "扫描pdf", "PDF图片转写", "pdf图片转写", "pdf转markdown", "PDF转Markdown", "本地OCR兜底", "本地ocr兜底", "marker质量", "干净Markdown", "干净markdown"]),
    ("handwriting_ocr", PATHS["handwriting_ocr"], ["中文手写转写", "手写公式OCR", "手写公式ocr", "手写公式", "学生答题卡识别", "答题卡OCR", "答题卡ocr", "答题卡作答区", "机器转写", "OCR转写", "ocr转写", "本地OCR", "本地ocr", "PaddleOCR-VL", "PP-OCRv6", "PP-FormulaNet", "TexTeller", "不上传学生数据"]),
    ("student_data_import", PATHS["student_data_import"], ["StudentDataSQL导入", "学生数据导入", "名册导入", "导入名单", "导入这份名单", "名单", "成绩单导入", "成绩单", "小题分导入", "导入小题分", "小题分", "答题卡manifest导入", "答题卡 manifest 导入", "导入答题卡manifest", "答题卡导入", "考试成绩导入", "学校成绩单", "学生最近两场考试", "seat-only", "座位号导入", "录入成绩", "录入小题分", "录入答题卡", "小量导入测试"]),
    ("exercise_report_sql_import", PATHS["exercise"], ["试卷分析报告导入 StudentDataSQL", "试卷报告导入SQL", "StudentDataSQL试卷报告", "试卷报告目录", "试卷分析报告复制到 StudentDataSQL"]),
    ("student_data_analysis", PATHS["student_data"], ["sql学生数据分析", "studentdatasql", "studentdatasql分析", "STUDENT_DATA_DB_KEY", "真实库", "真实库key", "真实库密钥", "学生数据key", "学生数据密钥", "学生数据库分析", "学生数据分析", "学生成绩分析", "分析学生成绩", "成绩分析", "作业分析", "作业薄弱点", "日常作业分析", "学生掌握度", "单生期中知识点掌握", "单生知识点报告", "生成期中单生知识点报告", "期中考掌握情况", "学生知识点掌握情况", "各知识点掌握情况", "期中考试各知识点", "任意一名学生", "一名学生", "班级薄弱点", "脱敏班级周报", "脱敏报告", "答题卡分析", "答题卡", "考试成绩分析", "考试成绩", "成绩情况", "小题分分析", "小题分", "失分分析", "试卷和答题卡", "试卷答题卡"]),
    ("video_import", PATHS["video"], ["b站", "视频导入", "视频转写", "字幕", "抽帧", "faster-whisper", "关键帧"]),
    ("exercise_import", PATHS["exercise"], ["试卷入库", "题库导入", "题库录入", "录入题库", "导入试卷", "导入一套", "抽取题目", "提取题目", "题目配对", "题库图片视觉索引同步", "试题导入", "习题导入", "作业导入", "组题前题库修复", "题干缺失修复", "原图错配修复", "组题缺陷回退", "考试卷分析报告", "试卷设计分析报告", "试卷导入分析报告", "试卷报告导入SQL", "StudentDataSQL试卷报告", "期中考试卷导入", "月考试卷导入", "模拟考试卷导入", "整卷分析报告"]),
    ("textbook_import", PATHS["textbook"], ["教材导入", "课本入库", "导入教材", "导入课本", "章节拆分"]),
    ("standards_import", PATHS["standards"], ["课程标准", "评价体系", "标准导入", "试题分析报告"]),
    ("knowledge_checklist", PATHS["checklist"], ["知识清单", "章节知识清单", "全册知识清单", "复习清单", "考点梳理"]),
    ("lecture_generation", PATHS["lecture"], ["生成讲义", "生成作业", "生成一份", "复习讲义", "生成试卷", "生成练习", "题库原题组题", "题库原题", "原题原图", "原创组题", "原创题", "原创练习", "组卷", "出一份", "命制", "学案", "备课材料"]),
    ("ppt_analysis", PATHS["ppt"], ["ppt分析", "课件分析", "课堂投影", "教学逻辑", "powerpoint"]),
    ("doc_export", PATHS["docx"], ["导出word", "输出word", "转word", "生成docx", "导出pdf", "markdown导出", "导出成", "导出", "已有markdown"]),
    ("output_format", PATHS["format"], ["试卷格式", "讲义格式", "格式规范", "宋体", "times new roman", "版式"]),
    ("rag_management", PATHS["rag_manage"], ["rag重建", "重建rag", "向量索引", "重建索引", "重建向量", "faiss", "chunks"]),
    ("git_management", PATHS["git_manage"], ["git仓库", ".gitignore", "初始化仓库", "创建git", "创建 git", "版本控制", "git status", "commit", "提交"]),
    ("framework_admin", PATHS["framework_admin"], ["框架维护", "角色c", "role c", "知识库管理员", "registry", "router", "validator", "验证器", "skill门禁", "schema升级", "入口文件", "agents.md", "schema.md", "claude.md", "gemini.md", "agent shim", "更新入口", "入口规范", "角色ab", "角色a/b", "角色d", "测试角色ab", "测试角色d", "扩大强档权限", "修改strong权限", "skill_retriever", "组题流程问题", "生成skill问题", "生成作业流程问题", "作业生成流程问题", "题源标记泄露", "答题区错误", "角色回退错误", "整合包", "部署包", "整合包完整性", "整合包边界", "新设备整合包", "package_boundary", "package boundary", "临时代码", "临时脚本", "temp-script", "归档临时代码", "清理临时代码"]),
    ("wiki_maintenance", PATHS["wiki_maint"], ["wiki维护", "死链", "孤立页", "图谱健康", "视频三页"]),
    ("taxonomy", PATHS["tags"], ["知识点标签", "题库分类", "打标签", "已有题目分类", "knowledge_points", "knowledge_point_ids", "ai_extra_tags", "合并两个知识点标签", "合并知识点标签", "改名知识点标签", "移动知识点标签", "停用知识点标签", "kp_id"]),
    ("bemd_conversion", PATHS["bemarkdown"], ["docx转markdown", "pdf转markdown", "omml", "wmf", "公式转换", "ocr清理", "bemarkdown"]),
    ("retrieval", PATHS["rag_retrieval"], ["知识库检索", "查询", "回答", "解题", "解释", "简单问答", "检索"]),
]


def lower_text(text: str) -> str:
    return text.lower()


def term_hit(query_lower: str, term: str) -> bool:
    t = lower_text(term.strip())
    return bool(t) and t in query_lower


def plot_notation_hits(query_lower: str) -> list[str]:
    return sorted(
        set(re.findall(r"(?<![a-z0-9])(?:x|v|a|f)\s*-\s*t(?![a-z0-9])", query_lower))
    )


def split_terms(value: str) -> list[str]:
    parts = re.split(r"[,，;；、]", value)
    return [p.strip().strip("`") for p in parts if p.strip()]


def parse_registry(text: str) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    in_triggers = False
    for line in text.splitlines():
        if re.match(r"\s*-\s+name:\s*", line):
            if cur:
                skills.append(cur)
            cur = {"name": line.split(":", 1)[1].strip(), "triggers": []}
            in_triggers = False
        elif cur and re.match(r"\s+category:\s*", line):
            cur["category"] = line.split(":", 1)[1].strip()
        elif cur and re.match(r"\s+role:\s*", line):
            cur["role"] = line.split(":", 1)[1].strip()
        elif cur and re.match(r"\s+path:\s*", line):
            cur["path"] = line.split(":", 1)[1].strip()
        elif cur and re.match(r"\s+manifest:\s*", line):
            cur["manifest"] = line.split(":", 1)[1].strip()
        elif cur and re.match(r"\s+triggers:\s*", line):
            in_triggers = True
        elif cur and in_triggers and re.match(r"\s+-\s+", line):
            cur["triggers"].append(re.sub(r"^\s+-\s+", "", line).strip())
        elif cur and in_triggers and line.strip() and not line.startswith(" "):
            in_triggers = False
    if cur:
        skills.append(cur)
    return skills


def parse_card(card_path: Path) -> dict[str, str]:
    fields = {key: "" for key in CARD_FIELDS}
    if not card_path.exists():
        return fields
    for line in card_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*-\s+([a-z_]+):\s*(.*)", line)
        if m and m.group(1) in fields:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def read_card_text(skill_path: str) -> str:
    card_path = SKILLS_ROOT / Path(skill_path).parent / "card.md"
    if not card_path.exists():
        return ""
    return card_path.read_text(encoding="utf-8", errors="ignore")


def build_records() -> list[dict[str, Any]]:
    skills = parse_registry(REGISTRY.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for skill in skills:
        path = skill.get("path", "")
        card_path = SKILLS_ROOT / Path(path).parent / "card.md"
        card_text = read_card_text(path)
        card_fields = parse_card(card_path)
        rule = RULE_HINTS.get(path, {})
        doc_parts = [
            skill.get("name", ""),
            skill.get("category", ""),
            skill.get("role", ""),
            " ".join(skill.get("triggers", [])),
            card_text,
            " ".join(rule.get("hints", [])),
        ]
        records.append(
            {
                **skill,
                "card": str(card_path.relative_to(SKILLS_ROOT)),
                "card_fields": card_fields,
                "rule_hints": rule.get("hints", []),
                "negative_triggers": rule.get("negative", []),
                "priority": rule.get("priority", 50),
                "retrieval_doc": "\n".join(doc_parts),
            }
        )
    return records


def docs_hash(records: list[dict[str, Any]]) -> str:
    payload = [
        {
            "name": r.get("name"),
            "path": r.get("path"),
            "triggers": r.get("triggers", []),
            "card_fields": r.get("card_fields", {}),
            "retrieval_doc": r.get("retrieval_doc", ""),
        }
        for r in records
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def write_index(records: list[dict[str, Any]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "llmwiki_skill_retrieval_index",
        "version": 1,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "skills_root": str(SKILLS_ROOT),
        "docs_hash": docs_hash(records),
        "records": records,
    }
    INDEX_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def detect_stage(query: str) -> tuple[str | None, str | None, list[str]]:
    q = lower_text(query)
    plot_hits = plot_notation_hits(q)
    maintenance_verbs = [
        term
        for term in ["修改", "更新", "维护", "修复", "升级", "分析原因", "排查原因", "完善"]
        if term_hit(q, term)
    ]
    framework_targets = [
        term
        for term in ["skill", "router", "路由", "validator", "验证器", "schema", "门禁", "registry", "manifest", "card", "角色边界", "工作流"]
        if term_hit(q, term)
    ]
    if maintenance_verbs and framework_targets:
        return "framework_maintenance", PATHS["framework_admin"], maintenance_verbs + framework_targets
    campaign_hits = [
        term
        for term in ["agent campaign", "多 agent campaign", "多agent campaign", "多 agent 编排", "多agent编排", "多智能体任务编排", "campaign 批次", "campaign 续跑", "campaign 状态", "一次性 executor", "独立 auditor", "一次性子agent", "子agent审计", "长任务调度"]
        if term_hit(q, term)
    ]
    campaign_actions = [
        term
        for term in ["启动", "继续", "续跑", "检查", "查看", "汇总", "调度", "重试", "恢复", "接管", "执行", "运行"]
        if term_hit(q, term)
    ]
    if campaign_hits and campaign_actions:
        return "agent_campaign_orchestration", PATHS["agent_campaign"], campaign_actions + campaign_hits
    composition_hits = [
        term for term in ["组题", "组卷", "生成讲义", "生成作业", "作业生成", "生成练习", "练习生成", "课后练习", "生成试卷", "试卷生成", "role b"] if term_hit(q, term)
    ]
    if composition_hits:
        flow_issue_hits = [
            term
            for term in ["skill流程问题", "skill 流程问题", "skill流程有问题", "skill 流程有问题", "生成skill问题", "组题流程问题", "生成作业流程问题", "生成作业流程有问题", "作业生成流程问题", "练习生成流程问题", "试卷生成流程问题", "题源标记泄露", "题库标记泄露", "答题区错误", "答题区多余", "横线多余", "路由错误", "角色回退错误", "验证门禁不足"]
            if term_hit(q, term)
        ]
        if flow_issue_hits:
            return "composition_flow_repair", PATHS["framework_admin"], composition_hits + flow_issue_hits
        source_defect_hits = [
            term
            for term in ["题干缺失", "题干不完整", "选项缺失", "小问缺失", "明确指图但缺图", "缺图", "原图错误", "错误原图", "原图错配", "图片错配", "题图错误", "图像语义错误"]
            if term_hit(q, term)
        ]
        if source_defect_hits:
            return "composition_source_repair", PATHS["exercise"], composition_hits + source_defect_hits
        diagram_handoff_hits = [
            term
            for term in ["原创物理图", "原创图", "改编图", "自绘图", "物理示意图", "函数图", "实验数据图", "实验数据折线", "实验数据散点", "受力图"]
            if term_hit(q, term)
        ]
        diagram_handoff_hits.extend(plot_hits)
        if diagram_handoff_hits and not any(term_hit(q, term) for term in ["role c", "角色c"]):
            return "lecture_with_diagram_handoff", PATHS["lecture"], composition_hits + diagram_handoff_hits
    if (
        re.search(r"\b[A-Za-z]{1,3}\d{6,8}\b", query)
        and any(term_hit(q, term) for term in ["修改", "修复", "补写", "重写"])
        and any(term_hit(q, term) for term in ["解析", "详解", "答案"])
    ):
        return "role_d_curation", PATHS["solution_curation"], ["question_id", "解析维护"]
    cleanup_requested = any(term_hit(q, term) for term in ["清理", "cleanup", "清除"])
    if cleanup_requested and term_hit(q, "studentdatasql") and any(
        term_hit(q, term) for term in ["imports", "导入文件", "导入目录", "加密导入"]
    ):
        return "student_data_import", PATHS["student_data_import"], ["StudentDataSQL", "imports清理"]
    if cleanup_requested and any(
        term_hit(q, term) for term in ["题库导入", "视频导入", "教材导入", "标准导入", "导入任务", "cleanup_manifest", "cleanup manifest"]
    ) and any(term_hit(q, term) for term in ["临时", "工作区", "manifest"]):
        return "cleanup_import_workspace", PATHS["cleanup_import"], ["导入任务", "临时清理"]
    if cleanup_requested and (
        any(term_hit(q, term) for term in ["旧的临时文件", "全局", "共享 runtime", "共享runtime", "旧版便携程序", "程序残留"])
        or (term_hit(q, "rag") and any(term_hit(q, term) for term in ["缓存", "旧日志", "runtime"]))
    ):
        return "cleanup_runtime_assets", PATHS["cleanup_runtime"], ["runtime", "全局清理"]
    diagram_direct_hits = [
        term
        for term in ["实验数据折线", "实验数据散点"]
        if term_hit(q, term)
    ]
    diagram_direct_hits.extend(plot_hits)
    if re.search(r"(?:原图|图片|题图).{0,24}(?:精确坐标|坐标系|受力|运动学|批注|箭头|辅助线)", q):
        diagram_direct_hits.append("source_image_annotation")
    if diagram_direct_hits:
        return "role_d_diagram", PATHS["diagram"], diagram_direct_hits
    for stage, path, terms in STAGE_RULES:
        hits = [t for t in terms if term_hit(q, t)]
        if not hits:
            continue
        has_image_pdf_exercise_import_context = any(
            term_hit(q, t) for t in ["PDF", "pdf", "图片型PDF", "图片型pdf", "扫描PDF", "扫描pdf"]
        ) and any(
            term_hit(q, t)
            for t in [
                "题库",
                "入库",
                "导入试卷",
                "试卷导入",
                "题目配对",
                "抽取题目",
                "提取题目",
                "试卷分析报告",
                "试卷设计分析报告",
            ]
        )
        if stage == "bemd_image_pdf_conversion" and has_image_pdf_exercise_import_context:
            return "exercise_import", PATHS["exercise"], [t for t in hits if term_hit(q, t)]
        if stage == "bemd_image_pdf_conversion" and any(
            term_hit(q, t) for t in ["答题卡", "手写", "作答区", "学生作答", "不上传学生数据", "机器转写"]
        ):
            continue
        if stage == "bemd_image_pdf_conversion" and any(
            term_hit(q, t)
            for t in [
                "题库",
                "入库",
                "导入试卷",
                "试卷导入",
                "题目配对",
                "抽取题目",
                "提取题目",
                "试卷分析报告",
                "试卷设计分析报告",
            ]
        ):
            continue
        if stage == "handwriting_ocr":
            has_document_conversion_context = any(
                term_hit(q, t)
                for t in ["pdf", "PDF", "markdown", "Markdown", "扫描pdf", "扫描PDF", "图片型pdf", "图片型PDF", "marker"]
            )
            has_handwriting_context = any(
                term_hit(q, t) for t in ["答题卡", "手写", "作答区", "学生作答", "不上传学生数据", "机器转写"]
            )
            if has_document_conversion_context and not has_handwriting_context:
                continue
        if stage == "taxonomy" and any(term_hit(q, t) for t in ["导入试卷", "题库导入", "抽取题目", "提取题目"]):
            continue
        if stage == "role_d_tutoring" and any(
            term_hit(q, t) for t in ["导入试卷", "题库导入", "修改题目解析", "教师备课", "学生成绩分析"]
        ):
            continue
        if stage == "bemd_conversion" and any(term_hit(q, t) for t in ["试卷入库", "题库导入", "视频导入", "教材导入"]):
            continue
        if stage == "lecture_generation" and any(
            term_hit(q, t)
            for t in [
                "导入",
                "入库",
                "题库",
                "题目配对",
                "抽取题目",
                "提取题目",
                "试卷分析报告",
                "试卷设计分析报告",
                "StudentDataSQL",
            ]
        ):
            continue
        if stage == "student_data_import" and term_hit(q, "脚本") and any(
            term_hit(q, t) for t in ["修改", "维护", "更新", "修复", "改"]
        ):
            continue
        if stage == "student_data_import" and not any(
            term_hit(q, t)
            for t in ["导入", "录入", "预处理", "名单", "名册", "成绩单", "manifest", "seat-only", "座位号"]
        ):
            continue
        if stage == "student_data_analysis" and any(
            term_hit(q, t) for t in ["导入", "录入", "预处理", "manifest", "seat-only"]
        ):
            continue
        if stage == "rag_management" and any(term_hit(q, t) for t in ["知识库检索", "简单检索", "解题"]):
            continue
        if stage == "doc_export" and any(term_hit(q, t) for t in ["生成讲义", "生成试卷", "组卷"]):
            continue
        return stage, path, hits
    return None, None, []


def lexical_score(query: str, record: dict[str, Any]) -> tuple[int, list[str]]:
    q = lower_text(query)
    score = int(record.get("priority", 50)) // 10
    hits: list[str] = []
    for trig in record.get("triggers", []):
        if term_hit(q, trig):
            score += 28 + min(len(trig), 8)
            hits.append(trig)
    for hint in record.get("rule_hints", []):
        if term_hit(q, hint):
            score += 14
            hits.append(hint)
    for field in ["use_when", "input", "output"]:
        for term in split_terms(record.get("card_fields", {}).get(field, "")):
            if term_hit(q, term):
                score += 10
                hits.append(term)
    for neg in record.get("negative_triggers", []):
        if term_hit(q, neg):
            score -= 35
            hits.append(f"-{neg}")
    for field in [record.get("name", ""), record.get("category", "")]:
        if field and term_hit(q, field):
            score += 4
            hits.append(field)
    return score, hits


def run_semantic_worker(texts: list[str], output_path: Path, timeout: int = 180) -> bool:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    input_hash = hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    input_path = CACHE_DIR / f"semantic-input-{input_hash}.json"
    input_path.write_text(
        json.dumps({"model_path": str(BGE_MODEL), "texts": texts}, ensure_ascii=False),
        encoding="utf-8",
    )
    py = BGE_PYTHON if BGE_PYTHON.exists() else Path(sys.executable)
    try:
        proc = subprocess.run(
            [str(py), str(Path(__file__).resolve()), "--semantic-worker", str(input_path), str(output_path)],
            cwd=str(KB_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return False
    finally:
        input_path.unlink(missing_ok=True)
    return proc.returncode == 0 and output_path.exists()


def normalize_vector(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [float(x) / norm for x in vec]


def semantic_worker(input_path: Path, output_path: Path) -> int:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    model_path = payload["model_path"]
    texts = payload["texts"]
    from FlagEmbedding import BGEM3FlagModel  # type: ignore

    model = BGEM3FlagModel(model_path, use_fp16=True)
    encoded = model.encode(
        texts,
        batch_size=4,
        max_length=512,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    dense = encoded["dense_vecs"] if isinstance(encoded, dict) else encoded
    vectors = [normalize_vector([float(x) for x in row]) for row in dense]
    output_path.write_text(json.dumps({"vectors": vectors}, ensure_ascii=False), encoding="utf-8")
    return 0


def rebuild_semantic_cache(records: list[dict[str, Any]]) -> bool:
    if not BGE_MODEL.exists():
        return False
    temp_vectors = CACHE_DIR / "skill_vectors.tmp.json"
    ok = run_semantic_worker([r["retrieval_doc"] for r in records], temp_vectors)
    if not ok:
        return False
    vectors_payload = json.loads(temp_vectors.read_text(encoding="utf-8"))
    payload = {
        "kind": "llmwiki_skill_retrieval_vectors",
        "version": 1,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "model_path": str(BGE_MODEL),
        "docs_hash": docs_hash(records),
        "vectors": {r["path"]: v for r, v in zip(records, vectors_payload["vectors"])},
    }
    VECTORS_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temp_vectors.unlink(missing_ok=True)
    return True


def semantic_scores(query: str, records: list[dict[str, Any]], use_semantic: bool) -> tuple[dict[str, float], str]:
    if not use_semantic:
        return {}, "disabled"
    if not VECTORS_JSON.exists():
        return {}, "unavailable:no-cache"
    try:
        payload = json.loads(VECTORS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}, "unavailable:bad-cache"
    if payload.get("docs_hash") != docs_hash(records):
        return {}, "unavailable:stale-cache"
    query_output = CACHE_DIR / "semantic-query-vector.json"
    if not run_semantic_worker([query], query_output, timeout=120):
        return {}, "unavailable:query-encode-failed"
    try:
        qvec = json.loads(query_output.read_text(encoding="utf-8"))["vectors"][0]
    except Exception:
        return {}, "unavailable:bad-query-vector"
    vectors = payload.get("vectors", {})
    scores: dict[str, float] = {}
    for record in records:
        vec = vectors.get(record["path"])
        if not vec:
            continue
        scores[record["path"]] = float(sum(a * b for a, b in zip(qvec, vec)))
    return scores, "active"


def infer_queue(query: str, primary: str) -> list[str]:
    q = lower_text(query)
    queued: list[str] = []

    def add(path: str) -> None:
        if path != primary and path not in queued:
            queued.append(path)

    if primary == PATHS["exercise"]:
        if any(term_hit(q, t) for t in ["解析风险", "缺少解析", "修复解析", "答案不一致"]):
            add(PATHS["solution_curation"])
        add(PATHS["tags"])
        add(PATHS["physics_graph"])
        add(PATHS["rag_manage"])
        if any(term_hit(q, t) for t in ["组题", "组卷", "生成讲义", "生成作业", "生成练习", "生成试卷", "return_to=lecture-generation"]):
            add(PATHS["lecture"])
    elif primary in {PATHS["textbook"], PATHS["standards"], PATHS["video"]}:
        add(PATHS["wiki_maint"])
        add(PATHS["physics_graph"])
        add(PATHS["rag_manage"])
    elif primary == PATHS["lecture"]:
        if any(term_hit(q, t) for t in ["原创物理图", "原创图", "改编图", "自绘图", "物理示意图", "函数图", "实验数据图", "实验数据折线", "实验数据散点", "受力图"]) or plot_notation_hits(q):
            add(PATHS["diagram"])
        if any(term_hit(q, t) for t in ["word", "docx", "pdf", "导出", "输出", "排版"]):
            add(PATHS["docx"])
        if any(term_hit(q, t) for t in ["格式", "宋体", "times new roman", "版式"]):
            add(PATHS["format"])
    elif primary == PATHS["checklist"]:
        # Knowledge checklists always complete the shared Word/PDF and format-QA phases.
        add(PATHS["docx"])
        add(PATHS["format"])
    elif primary == PATHS["docx"]:
        if any(term_hit(q, t) for t in ["格式", "宋体", "times new roman", "版式"]):
            add(PATHS["format"])
    elif primary == PATHS["wiki_maint"] and any(term_hit(q, t) for t in ["rag", "向量", "索引", "同步"]):
        add(PATHS["rag_manage"])
    elif primary == PATHS["framework_admin"]:
        if any(term_hit(q, t) for t in ["git", ".gitignore", "仓库", "commit", "提交"]):
            add(PATHS["git_manage"])
        if any(term_hit(q, t) for t in ["学生成绩分析", "成绩分析", "作业分析", "方法错误", "知识点犯错", "脱敏报告"]):
            add(PATHS["student_data"])
        if any(term_hit(q, t) for t in ["组题", "组卷", "生成讲义", "生成作业", "作业生成", "生成练习", "练习生成", "课后练习", "生成试卷", "试卷生成"]):
            add(PATHS["lecture"])
    elif primary == PATHS["git_manage"] and any(term_hit(q, t) for t in ["入口", "角色", "router", "registry", "框架"]):
        add(PATHS["framework_admin"])
    elif primary == PATHS["student_data_import"]:
        if any(term_hit(q, t) for t in ["分析", "报告", "薄弱点", "失分", "讲评", "成绩情况"]):
            add(PATHS["student_data"])
    elif primary == PATHS["student_data"]:
        if any(term_hit(q, t) for t in ["讲义", "讲评", "练习", "备课", "教学材料"]):
            add(PATHS["rag_retrieval"])
            add(PATHS["lecture"])
    elif primary == PATHS["tutoring"]:
        add(PATHS["rag_retrieval"])
        if any(term_hit(q, t) for t in ["受力图", "坐标", "函数图", "矢量", "轨迹", "批注", "箭头"]):
            add(PATHS["diagram"])
    elif primary == PATHS["diagram"]:
        if any(term_hit(q, t) for t in ["讲解", "解题", "提示", "学生"]):
            add(PATHS["tutoring"])
    elif primary == PATHS["solution_curation"]:
        if any(term_hit(q, t) for t in ["标签", "知识点", "kp_id", "knowledge_point"]):
            add(PATHS["tags"])
        add(PATHS["physics_graph"])
        add(PATHS["rag_manage"])
    elif primary == PATHS["tags"]:
        add(PATHS["physics_graph"])
        add(PATHS["rag_manage"])
    elif primary == PATHS["physics_graph"]:
        if any(term_hit(q, t) for t in ["导入", "handoff", "同步", "更新"]):
            add(PATHS["rag_manage"])
    return queued


def confidence_from_scores(top_score: int, second_score: int, stage_primary: str | None) -> float:
    if top_score <= 0:
        return 0.42
    gap = max(0, top_score - second_score)
    base = 0.72 if stage_primary else 0.58
    value = base + min(gap / 220.0, 0.18) + min(top_score / 500.0, 0.08)
    return round(max(0.35, min(0.98, value)), 2)


def route_query(query: str, *, top_k: int = 5, use_semantic: bool = True, rebuild: bool = False) -> dict[str, Any]:
    records = build_records()
    semantic_status = "not-requested"
    if rebuild:
        write_index(records)
        semantic_status = "rebuilt" if use_semantic and rebuild_semantic_cache(records) else "disabled-or-unavailable"
    sem, sem_status = semantic_scores(query, records, use_semantic)
    semantic_status = sem_status
    stage, stage_primary, stage_hits = detect_stage(query)
    ranked: list[dict[str, Any]] = []
    for record in records:
        base, hits = lexical_score(query, record)
        stage_bonus = 100 if record["path"] == stage_primary else 0
        sem_component = int(max(0.0, sem.get(record["path"], 0.0)) * 15)
        score = base + stage_bonus + sem_component
        ranked.append(
            {
                "score": score,
                "lexical_score": base,
                "semantic_score": round(sem.get(record["path"], 0.0), 4) if record["path"] in sem else None,
                "stage_bonus": stage_bonus,
                "matched": hits,
                "name": record.get("name"),
                "category": record.get("category"),
                "role": record.get("role"),
                "path": record.get("path"),
                "manifest": record.get("manifest"),
            }
        )
    ranked.sort(key=lambda item: (item["score"], item.get("stage_bonus", 0)), reverse=True)
    if not ranked or ranked[0]["score"] <= 0:
        fallback = next(r for r in records if r["path"] == PATHS["rag_retrieval"])
        ranked = [
            {
                "score": 0,
                "lexical_score": 0,
                "semantic_score": None,
                "stage_bonus": 0,
                "matched": [],
                "name": fallback.get("name"),
                "category": fallback.get("category"),
                "role": fallback.get("role"),
                "path": fallback.get("path"),
                "manifest": fallback.get("manifest"),
            }
        ]
    primary = ranked[0]
    second_score = ranked[1]["score"] if len(ranked) > 1 else 0
    queued = infer_queue(query, primary["path"])
    reason_bits = []
    if stage:
        reason_bits.append(f"stage={stage}")
    if stage_hits:
        reason_bits.append("signals=" + ",".join(stage_hits[:4]))
    if primary.get("matched"):
        reason_bits.append("matched=" + ",".join(str(x) for x in primary["matched"][:4]))
    reason = "; ".join(reason_bits) or "safe default to read-only retrieval"
    result = {
        "query": query,
        "skills_root": str(SKILLS_ROOT),
        "registry": str(REGISTRY),
        "cache_dir": str(CACHE_DIR),
        "semantic_status": semantic_status,
        "role": primary.get("role"),
        "primary": primary.get("path"),
        "load_now": [primary.get("path")],
        "queued": queued,
        "alternatives": ranked[1:top_k],
        "route": ranked[:top_k],
        "confidence": confidence_from_scores(primary["score"], second_score, stage_primary),
        "reason": reason,
        "instructions": [
            "Read skills/registry.yaml first.",
            "Load only primary SKILL.md from load_now[0].",
            "Treat queued skills as later phases; do not read them until that phase starts.",
            "Read manifest.yaml only for scripts/runtime paths and MCP/plugin dependencies.",
            "Never add skills/ content to the teaching BGE-M3 RAG index.",
            "Skill retrieval cache belongs only under skills/_ops/runtime/state/skill-retrieval/.",
            "Role D defaults to student read-only mode; curator writes require explicit teacher authorization.",
        ],
    }
    return result


def run_self_test() -> int:
    if not FIXTURES.exists():
        print(json.dumps({"ok": False, "issue": f"missing fixtures: {FIXTURES}"}, ensure_ascii=False, indent=2))
        return 1
    cases = []
    for line in FIXTURES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    failures = []
    for case in cases:
        result = route_query(case["query"], top_k=5, use_semantic=False)
        expected_primary = case["expected_primary"]
        expected_queued = case.get("expected_queued", [])
        missing_queued = [p for p in expected_queued if p not in result["queued"]]
        min_conf = float(case.get("min_confidence", 0.55))
        if result["primary"] != expected_primary or missing_queued or result["confidence"] < min_conf:
            failures.append(
                {
                    "query": case["query"],
                    "expected_primary": expected_primary,
                    "actual_primary": result["primary"],
                    "expected_queued": expected_queued,
                    "actual_queued": result["queued"],
                    "missing_queued": missing_queued,
                    "confidence": result["confidence"],
                    "reason": result["reason"],
                }
            )
    payload = {"ok": not failures, "case_count": len(cases), "failure_count": len(failures), "failures": failures}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def print_human(result: dict[str, Any]) -> None:
    print(f"Registry: {result['registry']}")
    print(f"Primary skill: {result['primary']}  (confidence={result['confidence']})")
    print(f"Role: {result['role']}")
    print(f"Reason: {result['reason']}")
    if result["queued"]:
        print("Queued next-phase skills:")
        for path in result["queued"]:
            print(f"- {path}")
    if result["alternatives"]:
        print("Alternatives:")
        for item in result["alternatives"]:
            print(f"- {item['path']} (score={item['score']})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="*", help="task description")
    ap.add_argument("--json", action="store_true", help="machine-readable JSON")
    ap.add_argument("--rebuild", action="store_true", help="rebuild isolated skill retrieval cache")
    ap.add_argument("--no-semantic", action="store_true", help="disable BGE-M3 semantic scoring")
    ap.add_argument("--top-k", type=int, default=5, help="number of route candidates to return")
    ap.add_argument("--self-test", action="store_true", help="run routing fixture tests")
    ap.add_argument("--semantic-worker", nargs=2, metavar=("INPUT", "OUTPUT"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.semantic_worker:
        return semantic_worker(Path(args.semantic_worker[0]), Path(args.semantic_worker[1]))
    if args.self_test:
        return run_self_test()
    query = " ".join(args.query).strip()
    if not query:
        print("Usage: skill_retriever.py [--json] [--rebuild] [--no-semantic] <task description>", file=sys.stderr)
        return 2
    result = route_query(query, top_k=max(1, args.top_k), use_semantic=not args.no_semantic, rebuild=args.rebuild)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
