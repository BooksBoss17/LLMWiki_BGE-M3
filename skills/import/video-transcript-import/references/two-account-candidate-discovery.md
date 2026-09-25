# UP-甲双官方账号候选发现经验

适用于用户要求“检索并记录UP-甲两个官方账号的高中物理教学视频候选，重点找知识库未补充或覆盖较弱知识点，输出候选 BV 清单”的场景。

## 官方账号范围

本次已确认并按两个账号并列处理：

- `教物理的UP-甲` — owner.mid 常见为 `1874457568`
- `只教物理的UP-甲` — owner.mid 常见为 `473578106`

不要只按昵称合并。搜索结果中同一 BV 可能显示在旧/新账号名下，最终以 `video_detail.owner.name/mid` 和本地 BV 查重为准。

## 本地查重基线

先扫：

- `raw/transcripts/教物理的UP-甲/`
- `raw/transcripts/只教物理的UP-甲/`（可能不存在）
- `LLMWiki/concepts/视频-*UP-甲*` 或含 `up_master: UP-甲`
- `LLMWiki/_meta/*candidate*` 历史候选审计

已知要特别排除：

- `BV<已脱敏>` — UP-甲《动态平衡专题练习视频（标题已脱敏）》/本地《动态平衡两题练习》，已入库。
- `BV<已脱敏>` — UP-乙《楞次定律技巧讲解（标题已脱敏）》，已入库且非本次UP-甲账号。
- 本地已入库UP-甲 BV 还可能包括：`BV<已脱敏>`, `BV<已脱敏>`, `BV<已脱敏>`, `BV<已脱敏>`, `BV<已脱敏>`, `BV<已脱敏>`, `BV<已脱敏>`, `BV<已脱敏>`, `BV<已脱敏>`, `BV<已脱敏>`, `BV<已脱敏>`, `BV<已脱敏>`。实际以实时本地扫描为准。

## 推荐搜索组合

B站投稿接口不可用或风控时，用搜索 + `video_detail` 核验：

- `只教物理的UP-甲`
- `教物理的UP-甲`
- `教物理的UP-甲 高中物理`
- `教物理的UP-甲 静电场 电势 电场强度`
- `教物理的UP-甲 动量 碰撞 弹簧 曲面`
- `教物理的UP-甲 洛伦兹力 磁场 冲量`
- `教物理的UP-甲 电磁感应 导体棒 电容器 单棒`
- `教物理的UP-甲 运动学 图像 微元 求和`
- `教物理的UP-甲 机械能 功能关系 万有引力`
- `UP-甲 功能关系 机械能`
- `UP-甲 洛伦兹力 冲量`
- `UP-甲 碰撞 弹簧 小球 曲面`

## 覆盖判断经验

优先补“视频层弱覆盖”，不只看教材/题库是否有：

- 静电场基础、电场合成/等分法：本地常有教材/讲义/UP-乙技巧课，但UP-甲静电场视频层可能空缺。
- 动量碰撞拓展（弹簧—小球、曲面—小球）：常有讲义设计/题库，但缺专题视频。
- 洛伦兹力冲量：本地可能在真题讲解和讲义设计中零散出现，缺专题视频。
- 功能关系：常散落在机械能守恒、期末串讲、电磁感应单棒中，独立专题视频仍有价值。
- 动态平衡：若 `BV<已脱敏>` 已入库，后续候选只保留细分技法（如辅助圆、图像来源、跨电场/磁场迁移），并标注“主题重叠”。

## 候选输出格式

建议把完整审计写成 JSON，路径示例：

`tmp/logs/legacy-task-status/video_candidate_discovery_<timestamp>/up_a_candidates.json`

字段建议：

- `generated_at`, `scope`, `accepted_official_accounts`
- `local_baseline.up_a_raw_imported_bvids`
- `local_baseline.notable_weak_or_gap_topics_after_scan`
- `candidates[]`: `account_verified_name`, `bvid`, `title`, `url`, `publish_date`, `duration`, `play_count`, `topic_tags`, `inferred_knowledge_point`, `local_bvid_duplicate`, `local_topic_overlap`, `related_local_pages`, `gap_value`, `priority`, `keep_or_exclude`, `reason`
- `excluded_examples[]`: 对已入库、非教学、规划/资料评价类保留排除原因，防止下次重复判断。

完成后用 JSON 解析验证候选数、排除数、优先级分布和 BV 清单。候选发现阶段不要下载、转写、抽帧或重建 RAG。