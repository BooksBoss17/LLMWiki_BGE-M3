# physics-diagram-toolkit 检索卡

- status: active
- category: learn
- role: role-d-physics-learning
- canonical_skill: `learn/physics-diagram-toolkit/SKILL.md`
- manifest: `learn/physics-diagram-toolkit/manifest.yaml`
- description: 用 request v2、各向同性 scene spec v3、题库视觉参考和自动几何断言生成题目黑白图、解析彩色图、原图批注及函数/实验数据图；正式交付要求 geometry report 与 hash 绑定语义复核。
- triggers: 物理示意图生成, 原创物理图, 题目情境图, 答案解析图, 原图受力批注, 运动学批注, 画受力图, 受力箭头, 坐标系图像, 函数图, 实验数据图, 实验数据折线, x-t/v-t/a-t/F-t 图, 矢量图, 轨迹图, 图上批注, 辅助线, 把受力图写入题库, 修复题库原图
- use_when: 需要生成黑白题目图或彩色解析图, 需要自动校验角度/接触/共点/相切/尺寸, 在原图上精确添加受力或运动学批注, 绘制函数图和实验数据图, strong curator 需要把复核图写回题干或解析
- do_not_use_when: 仅识别题目文字, 修改题库解析但不需要图, 制作教学 PPT
- input: diagram_request v2（purpose/style/diagram_intent）, calibration/reference review（含制图规则）, diagram spec v3, semantic review；request v1/spec v2 仅作草稿兼容
- output: 坐标/参考报告, PNG/SVG/PDF, geometry_report.json, review_sheet.png, render_report.json, completion.json；strong curator 可输出题库写回 proposal
- next_skills: learn/physics-question-tutoring/SKILL.md, learn/exercise-solution-curation/SKILL.md, retrieve/md-to-docx/SKILL.md

## 加载规则

路由只读本 card；成为 primary 后读取 `SKILL.md`，仅在编写 spec 时读取 reference。
