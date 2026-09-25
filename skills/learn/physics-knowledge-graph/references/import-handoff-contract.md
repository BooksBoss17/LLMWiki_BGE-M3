# Import Handoff Contract

```json
{
  "schema_version": 1,
  "handoff_id": "import-20260711-demo",
  "source_skill": "exercise-bank-import",
  "changed_raw_files": ["raw/exercises/.../MC0000001.md"],
  "changed_wiki_files": ["LLMWiki/concepts/题库-示例.md"],
  "question_ids": ["MC0000001"],
  "kp_ids": ["kp_000001"],
  "quality_gates": {"passed": true, "unresolved_issues": 0},
  "file_hashes": {"LLMWiki/concepts/高中物理知识关系图谱.md": "..."},
  "graph_changes": [
    {
      "action": "append_relation",
      "page": "高中物理知识关系图谱.md",
      "text": "- [[新专题]]：与 [[已有专题]] 的关系。"
    }
  ]
}
```

Allowed safe actions are `append_relation`, `append_backlink`, and
`create_managed_page`. Destructive actions are `delete_page`, `rename_page`,
`merge_pages`, `move_page`, and `replace_page`; they never auto-apply.
