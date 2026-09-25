---
name: physics-knowledge-graph
description: 根据教学内容导入 handoff 局部维护“高中物理知识关系图谱”和登记的专题枢纽。用于导入后的图谱增补、反向链接、专题页和统计更新；删页、改名、合并或大范围迁移必须教师审批。
---

# 高中物理知识关系图谱维护

只修改登记的 Role D managed 页面和受控区块。根页的其他链接只是引用，不自动获得写权限。
`strong` 可检查更多候选关系和重复边，但仍只能输出本 skill 的固定 action。

## 线性流程

1. 接收 Role A 生成的 `import_handoff.json`，确认质量门禁全部通过且文件 hash 可核验。
2. 需要字段定义时读取 `references/import-handoff-contract.md`；需要判断页面权限时读取
   `references/managed-graph-pages.yaml`。
3. 先运行 dry-run：

```bash
python skills/learn/physics-knowledge-graph/scripts/update_graph.py \
  --handoff <import-handoff.json> --dry-run --json
```

4. 检查 proposal 只包含受控区块内的 `append_relation`、`append_backlink` 或明确登记的
   `create_managed_page`。
   强模型可以一次提出多个局部变更，但每项必须独立给出已有页面证据，且不得自由改写整页。
5. 对安全新增运行 `--apply`。脚本遇到删页、改名、合并、移动或整页替换时只生成审批项；只有教师明确授权后才能加
   `--teacher-authorized --allow-destructive`。
6. 运行图谱校验并保存 `graph_update_result.json`。Role A 必须用该结果通过 import handoff 完成门禁后才能重建 RAG 并声明导入完成。

## 失败关闭

- handoff 缺字段、质量门禁未通过、hash 冲突或目标不受控时拒绝修改。
- 不允许弱模型提交整页替换文本；脚本只接受固定 action schema。
- 强模型同样不得提交整页替换、近义 action 或越过 managed page 边界。
- 不自动把根页现有全部链接纳入 managed pages。
- 不读取或修改 StudentDataSQL，不把图谱 skill 内容加入教学 RAG。

## 完成清单

- 每个 handoff 都有 `applied_safe`、`no_change` 或 `approval_required` 的成功结果。
- 修改页面均在 managed 清单或带 `role_d_managed: true` 的新页中。
- 无无关页面改动、无新增断链、备份和差异报告位于 ignored runtime。
