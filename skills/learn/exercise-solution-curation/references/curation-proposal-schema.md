# Curation Proposal Contract

## v2 — strong curator 完整题目校对

```json
{
  "schema_version": 2,
  "model_profile": "strong",
  "question_id": "MC0001426",
  "target_path": "raw/exercises/综合/example/MC0001426.md",
  "expected_sha256": "64 lowercase hex characters",
  "updates": {
    "stem": "完整题干、选项和必要的 ![图](media/MC0001426_solution01.png)",
    "answer": "B",
    "solution": "分步、可核验的 Markdown 解析，并在需要时引用新增示意图。",
    "assets": ["media/MC0001426_solution01.png"],
    "ai_extra_tags": ["受力分析", "图解法", "易错-方向判断"]
  },
  "media_files": [
    {
      "source_path": "skills/_ops/runtime/staging/MC0001426/solution01.png",
      "source_sha256": "64 lowercase hex characters",
      "target": "media/MC0001426_solution01.png",
      "expected_target_sha256": "仅覆盖现有图片时填写其当前 SHA-256"
    }
  ],
  "verification": {
    "source_evidence": [
      {
        "path": "source-library/权威解析版.docx",
        "sha256": "64 lowercase hex characters",
        "page": 12,
        "relationship_id": "rId8"
      }
    ],
    "stem_complete": true,
    "figure_dependencies_resolved": true,
    "review_passes": 2,
    "derived_correct_options": ["B"]
  }
}
```

Rules:

- v2 requires `model_profile: strong` for `stem`, `assets` or `media_files`.
- v2 对 `stem/answer/solution/assets/media_files` 的任何实质修改都必须提供 `verification`；只有纯 `ai_extra_tags` 更新可省略。
- `source_evidence` 至少一项，路径必须在仓库内并绑定当前 SHA-256；`page` 和 `relationship_id` 在可取得时填写。
- `stem_complete`、`figure_dependencies_resolved` 必须为 `true`，`review_passes` 至少为 2。
- `MC/MA` 必须提供 `derived_correct_options`，并与最终答案选项集合完全一致。其他题型改用 `non_choice_check: {"status": "confirmed", "summary": "..."}`，摘要不少于 20 字符。
- 题干明确出现“如图所示/图中/图甲/下图/图1”等指代而最终正文零图片且 `assets` 为空时，以 `stem_requires_figure_but_assets_empty` 失败关闭；“图像法/图像特征”等泛指不触发。
- `updates.stem`, `updates.answer`, `updates.solution`, `updates.assets` 和 `updates.ai_extra_tags` 是可写字段；未变化字段可以省略。
- `updates.assets` must exactly equal the final Markdown body's distinct `media/...` references whenever image references change.
- Every `media_files.target` must be `media/<filename>`, must appear in the final body and assets list, and must use a supported image extension.
- 新图片不填写 `expected_target_sha256`；相同内容的现有图片会直接复用。
- 覆盖错误图片时必须填写当前目标图的 `expected_target_sha256`。脚本核对后备份原图，再原子替换；hash 不符时拒绝写入。
- Use the taxonomy skill for `knowledge_points` or `knowledge_point_ids`.
- `ai_extra_tags` 可由 strong curator 自主维护，最多 16 个、每项 2–32 字符；不得包含 `/`、反斜杠或 `kp_XXXXXX` 形式，也不得替代正式知识点。
- The proposal must never contain student identity or StudentDataSQL data.

## v1 — weak compatibility

Existing v1 proposals remain valid and must contain both `updates.answer` and `updates.solution`. v1 cannot write stems, assets or media.

## Batch v3 binding

Proposal schema 不嵌入调度身份。长任务通过 CLI 参数把同一 proposal 绑定到 active `batch_card v3`、教师授权和 dry-run receipt；apply 还必须绑定全新 auditor 的 `batch_result v3`，且该题 verdict 为 `passed`。最终 `apply_receipt v3` 逐文件记录 before/after hash、备份和确定性验证。控制面只保存 artifact/receipt 路径与 SHA-256，不复制题干、OCR 或图片证据正文。
