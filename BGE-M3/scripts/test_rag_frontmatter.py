import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


PIPELINE = Path(__file__).with_name("rag_pipeline.py")


def load_pipeline():
    sys.modules.setdefault("faiss", types.ModuleType("faiss"))
    sys.modules.setdefault("numpy", types.ModuleType("numpy"))
    spec = importlib.util.spec_from_file_location("rag_pipeline_frontmatter_test", PIPELINE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RagFrontmatterTests(unittest.TestCase):
    def test_chunker_accepts_indented_and_indentless_knowledge_points(self):
        pipeline = load_pipeline()
        template = """---
id: MC0000001
question_type: MC
knowledge_points:
{items}---

## 题目

示例题。

## 答案

A

## 详解

示例详解。
"""
        with tempfile.TemporaryDirectory() as directory:
            for name, items in {
                "indentless.md": "- 力学/运动的描述/机械运动\n",
                "indented.md": "  - 力学/运动的描述/机械运动\n",
            }.items():
                path = Path(directory) / name
                path.write_text(template.format(items=items), encoding="utf-8")
                chunk = pipeline.chunk_exercise_md(path, name)[0]
                self.assertEqual(chunk["knowledge_points"], ["力学/运动的描述/机械运动"])


if __name__ == "__main__":
    unittest.main()
