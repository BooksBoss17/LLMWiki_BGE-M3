---
name: exercise-solution-curation
description: 安全校对已有高中物理题库题目的题干、答案、详解和必要题图。strong curator 可在单题 hash 绑定 proposal 中完成整题修复；只在教师授权后应用写入。
---

# 题库完整记录校对

本 skill 只允许 `curator` 模式。默认动作是 proposal + dry-run，不直接改题库。

- `weak` 兼容 v1，只能同时更新答案与详解。
- 显式 `strong` 使用 v2，可在一次校对中更新题干、答案、详解、`assets`、`ai_extra_tags`，并新增哈希绑定的题目图或解析图。
- strong 不绕过 question id、文件 hash、dry-run、`--teacher-authorized`、备份或验证门禁；覆盖错误图片时还必须提供目标图当前 SHA-256。

## 线性流程

1. 对照原件、知识库检索和可核验物理推导，逐项复核题干、选项、答案、详解和图片。不要读取 StudentDataSQL。
   `strong` 先用标准检索，证据不足时再深度检索；仅在有检查价值时比较第二种解法。题干或图像有关键不确定项时不得猜测。
2. 按 `references/curation-proposal-schema.md` 生成 `curation_proposal.json`，其中必须包含
   `question_id`、目标相对路径和当前文件 SHA-256。
   strong 必须写 `schema_version: 2` 和 `model_profile: strong`，只使用 schema 已声明的 `stem/answer/solution/assets/ai_extra_tags/media_files`。除纯 `ai_extra_tags` 更新外，还必须提交 hash 绑定的 `verification`，记录原件证据、两轮复核、题干/图片完整性和独立答案核验。
   `media_files` 的 source 必须已生成或已标注完成、位于仓库内并提供 SHA-256；目标只能是当前题目录下的 `media/<filename>`。
3. 先运行：

```bash
python skills/learn/exercise-solution-curation/scripts/curate_exercise.py \
  --proposal <curation.json> --dry-run --json
```

4. 检查差异报告、题干/答案/详解一致性、公式和图片引用。MC/MA 的独立正确选项集合必须与最终答案一致；其他题型提供 confirmed 推导摘要。若新增或改变正文图片引用，`updates.assets` 必须与最终正文 `media/...` 引用完全一致。题干明确依赖图片而最终零图片时 dry-run 必须失败。hash 冲突时重新读取文件并重做 proposal。
5. 只有教师明确授权本次应用时运行：

```bash
python skills/learn/exercise-solution-curation/scripts/curate_exercise.py \
  --proposal <curation.json> --apply --teacher-authorized --json
```

6. 应用脚本会在 ignored runtime 下备份，并按 proposal 局部替换 `## 题目`/`## 答案`/`## 详解` 和 frontmatter `assets`。图片原子复制到当前题 `media/`；覆盖现有图片前核对 `expected_target_sha256` 并备份原图。
7. 应用后调用题目 RAG 兼容和图片审计。若校验失败，恢复题目文件并删除本次新建媒体。
8. 可写 `ai_extra_tags` 记录题型、方法、情境、易错点或能力维度；不得使用标准路径或 `kp_XXXXXX` 形式。
9. 若调整正式 `knowledge_points`、`knowledge_point_ids` 或 `kp_id`，必须转入 `taxonomy/exercise-knowledge-tags/SKILL.md`；本 skill 不得写这些字段。

## Batch v3 长任务

多批题目统一由 `maintain/agent-campaign-orchestration/SKILL.md` 管理。控制面只有
`init → dispatch → submit → recover → status`，本 skill 只负责业务 proposal、dry-run 和 canonical write。

- A+D executor 每批最多 5 题，只生成 proposal、证据和 dry-run receipt，不修改 `raw/`。图片、公式或原件复杂时拆为 1–3 题；OCR 只作候选，无法确认时返回 `source_blocked` 或 `unresolved`，不得猜修。
- 全新 C+D auditor 逐题对照原件独立核验题干、题图、答案、详解和推导，只返回 `passed` 或缺陷码，不拥有 writer 权限。
- 只有审计通过题才由调度器调用本 canonical writer。writer 回查 active batch、batch-card SHA-256、审计结果、教师授权、proposal/dry-run 以及 target/source/media hash；成功后输出 `apply_receipt v3`。写后验证失败时 writer 内部立即恢复备份。
- 审计失败题只允许一个全新 executor repair 和一个全新 auditor 复审；第二次失败即 `blocked`。旧 `run_id` 的迟到结果由控制面拒绝。
- worker 只读取当前 batch card 与其引用的 artifact；完整题干、OCR 和图片证据留在本地 artifact，控制面只保存路径与 SHA-256。

v3 dry-run 额外绑定：

```bash
python skills/learn/exercise-solution-curation/scripts/curate_exercise.py \
  --proposal <curation.json> --dry-run \
  --batch-card <executor-card.json> --batch-card-sha256 <sha256> \
  --authorization <authorization.json> --authorization-sha256 <sha256> \
  --receipt-out <dry-run-receipt.json> --json
```

正式 apply 由调度器传入 auditor batch card、独立审计结果和上述 dry-run receipt；人工或 worker 不应绕过调度器拼装 apply。

## 失败关闭

- 未授权 apply、question id 不匹配、目标不在 `raw/exercises/`、hash 不匹配或更新内容含占位符时拒绝写入。
- weak 不得写题干或媒体；strong 只可修改三个 H2 section、`assets` 和 `ai_extra_tags`，不得改其他 frontmatter、题目 ID、分类或正式知识点字段。
- 图片可新增、复用或修复覆盖。不同内容覆盖必须提供正确的 `expected_target_sha256`；hash 不符时失败关闭，原图备份与题目备份同目录保存。
- 强模型的第二次推理或视觉复核只是证据，不得代替源文件 hash、媒体 hash、dry-run 或教师授权。
- 现有校验失败时保留备份和差异报告，不得声称任务完成。

## 完成清单

- dry-run 和最终 apply 使用同一 proposal/hash；媒体 source hash 也未变化。
- 题干、选项、答案、详解和必要图一致，校验报告无 unresolved issue。
- 变更范围只有指定 question id；备份和报告位于 ignored runtime。
- 长任务的本批状态、artifact 和 receipt 已落盘；未把其他批次内容带入当前上下文。
