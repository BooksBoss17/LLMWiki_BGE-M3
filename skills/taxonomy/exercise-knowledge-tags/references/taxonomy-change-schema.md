# Taxonomy Change Proposal

```json
{
  "schema_version": 1,
  "expected_registry_sha256": "64 lowercase hex characters",
  "changes": [
    {"action": "add", "title": "新标签", "parent_id": "kp_000001"},
    {"action": "rename", "kp_id": "kp_000101", "new_title": "新名称"},
    {"action": "move", "kp_id": "kp_000101", "new_parent_id": "kp_000010"},
    {"action": "merge", "source_id": "kp_000101", "target_id": "kp_000099"},
    {"action": "deprecate", "kp_id": "kp_000101", "replacement_ids": ["kp_000099"]},
    {"action": "reorder", "kp_id": "kp_000101", "sort_order": 3},
    {
      "action": "assign_question",
      "target_path": "raw/exercises/.../MC0000001.md",
      "expected_sha256": "64 lowercase hex characters",
      "knowledge_point_ids": ["kp_000099"],
      "ai_extra_tags": ["图像法", "易错-方向判断"]
    }
  ]
}
```

Never emit `delete`. `merge` is implemented as source `deprecated` plus the
target in `replacement_ids`; no ID is removed or reused. Run every proposal in
dry-run first. Apply needs `--teacher-authorized`; rename, move, merge, and
deprecate also need `--allow-destructive`.

`assign_question` 至少提供 `knowledge_point_ids` 或 `ai_extra_tags` 之一。正式 `knowledge_points` 与 `knowledge_point_ids` 只能由本 action 根据 registry 同步写入；`ai_extra_tags` 不进入 registry，最多 16 个、每项 2–32 字符，且不得包含 `/` 或 `kp_XXXXXX` 形式。
