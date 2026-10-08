# 采购知识应用迁移：简化数据工程

当前依据 [简化版指南](doc/供应链采购知识应用迁移研究实施指南_简化数据工程版.docx) 第2至7节工作。数据阶段维护四份核心文件；只用一个 `case_family_id`；知识由研究者本人逐卡确认，训练题采用批次抽查，评价题逐组核验。训练、推理与效果评分不在本次范围。

## 本次交付

**工作区现有88张知识卡。** 首批40张来自标准PDF和第15版教材EPUB；新增48张来自两份OCR Markdown（每本24张），均保留原句和页码定位。

- 首批使用GB/T 26337.2—2011 PDF及《采购与供应管理（原书第15版）》中文EPUB。
- 内容涉及采购计划、订单、供应商、物料需求、库存口径、需求描述、质量成本和供应商评估等。
- 首批40张已通过独立会话模型复核及本人确认；新增48张的模型复核与本人确认均为 `pending`，OCR文字仍需回查扫描原页。首批曾修正一处“公平分配”被误述为“平均分配”的问题，旧版与修订意见保存在卡内。
- 人工确认进度以 `python -m procurement_transfer validate` 或本机审核台的实时统计为准。模型通过不等于本人确认；当前没有正式题目或实验结果。
- EPUB部分公式/表格是图片，未据常识补写。相应来源片段保留待核对说明；例如EOQ仅抽取正文的适用条件，没有补上图片中的求解公式。

先打开 [并排审核页面](reports/knowledge_review.html)，查看知识内容、原句、定位和模型疑点。

现在也可直接启动**本机交互式审核台**，在浏览器内确认知识卡、规划案例蓝图、审核题目和训练抽样、裁决疑似重复并执行冻结；审核结果仍只写入四份核心文件，不另建台账：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = 'src'
python -m procurement_transfer serve-review --port 8765
```

打开 `http://127.0.0.1:8765/`；按 Ctrl+C 停止。界面仅绑定本机回环地址，提交时需填写研究者真实姓名、日期和审核依据，内容或审核状态已变化的旧页面会拒绝保存。模型复核、题目导入和训练后预测评判仍依现有命令／后续实验脚本进行；本界面不把模型复核冒充人工确认。

## 目录

```text
configs/                     项目设置、6份来源登记、案例蓝图示例
prompts/                     知识生成、独立模型复核、题目生成提示词
src/procurement_transfer/    精简后的数据工作流
scripts/build_first40.py     本轮原文定位及模型编写卡稿的可复现构建脚本
tests/test_transfer.py       离线回归测试
data/
  01_sources/               原始/已有OCR资料，不改写
  workspace/
    sources.jsonl           88个选定原文片段、清洗文本、定位、哈希及疑点
    knowledge.jsonl         88张卡、模型复核、本人确认、修订历史
    data.jsonl              题目、双目标、评分规则；当前为空
    freeze.json             蓝图分配、批次抽查、统计、延期资料及冻结哈希
reports/knowledge_review.html  从核心文件派生的只读视图，不是另一套台账
```

当前启用四份资料：两份直接可读资料已完成首批40张卡审核；《采购管理：降低采购成本，提高采购效率》和《供应链运作：协同优化与高效控制》的OCR Markdown各新增24张待复核卡。其余两份资料（一份OCR Markdown、一份未处理PDF）仍为 `enabled: false`。新增卡的原句取自Markdown，页码取自其PDF页码注释，不能替代对扫描原页的人工确认。

旧文档、中期报告、SQL及可能有用的原始书籍/手工资料不作为当前数据输入。历史文档里的多表台账、二人审核及固定数量门槛已被本版替代，不再按旧命令运行。

## 运行

Python 3.10+；本机验证为3.12。仅依赖PyYAML和pypdf，不安装训练框架或改动其他环境。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = 'src'
python -m procurement_transfer doctor
python -m procurement_transfer validate
python -m unittest discover -s tests -v
python -m procurement_transfer review-view --output reports/knowledge_review.html
```

新环境可 `python -m pip install -e .`。`python scripts/build_first40.py` 仅用于在空workspace构建本轮初稿；已有卡片时拒绝覆盖，避免丢失审核与修订。当前知识卡才是最新状态，构建脚本不会自动重建已发生的独立审核。

## 本人确认知识

你是唯一人工审核者。逐卡阅读并排页面；公式、边界、否定词和疑点回看对应页/章节。原文未说明的条件、例外或单位用null表示，不应填成“无”。

确认后运行（替换为实际身份、日期及意见）：

```powershell
python -m procurement_transfer review-knowledge --id K_2ef81231ee4000ed --decision approved --reviewer 你的姓名 --date 2026-09-28 --notes 已对照原文确认
```

三种决定为 `approved`、`returned`、`discarded`。已弃用卡的派生题从活动数据移除并在freeze中保留弃用记录。需要改内容时，复制完整卡片对象到临时JSON、保留ID、revision加1，再执行：

```powershell
python -m procurement_transfer revise-knowledge --file 你的修订卡.json
```

旧内容留在revision_history，模型与人工状态重置；受影响题目按knowledge_id标记需要重新复核。独立模型会话返回复核JSON后使用：

```powershell
python -m procurement_transfer model-review --entity knowledge --file 独立会话审核结果.json
```

返回结构见 `prompts/model_review.md`。脚本核对独立会话标识与内容哈希，不能直接把生成会话标成复核会话。临时交换文件不是长期核心台账。

## 后续数据工作

知识确认后再开始：

1. 模型提出简短案例蓝图，参考 `configs/case_blueprints.example.jsonl`；同构结构归同一case_family_id。
2. `plan-cases --file 蓝图.jsonl` 按族整体分配，写入freeze。已有题目后不原地重分。
3. `prepare-generation --split train --output 临时训练任务.json` 只读取训练蓝图、对应已审知识和原文；该命令完全不读合并data文件。验证和测试用独立任务；测试任务输出必须放在sealed_test目录。
4. 模型按 `prompts/questions.md` 一次生成题目、参考、双目标与评分规则，使用 `import-data --split train --file 题目.jsonl` 导入。验证/测试分批导入完整三联组。
5. 在独立会话全量模型复核，使用 `model-review --entity data --file 审核结果.json` 写回各题。脚本检查字段、引用、知识版本、数值复算及三联关系。自然语言双目标的事实等价依赖模型全审与本人重点核验，不能仅凭格式声称自动证明。
6. `sample-batch --batch B001`：首批每池至少20题全审；高风险条件、公式、例外和模型疑难全审；其余按池和操作抽查约10%，每批至少3题。使用 `review-data --split train --id 题目编号 ...` 确认抽中题，再 `confirm-batch --batch B001 --reviewer ... --date ... --notes ...` 确认该批。
7. 诊断题本人全审，验证/测试逐组检查全部三题。未抽中训练题仍保留human_review=pending，只通过批次状态获得训练资格，不冒充逐题人工确认。

数值或引用错误直接阻断；抽查出现实质问题时不得确认批次，应统一修订受影响题、模型再审、本人重新抽查。同类错误反复出现须按指南全批人工核验或弃用，不通过重新抽样逃避问题。当前代码保留批次抽样及内容哈希；错误归类和“同类错误再次发生”的判定由本人负责。

`dedup` 将开发题的规范化/去数字疑似对写入freeze；它不比较共享原文。语义同构还需模型比较与本人裁决，本版没有embedding或多层关联图。冻结前会受控检查全量题，疑似对写入freeze的duplicate_candidates，裁决意见集中放在dedup_reviews中并绑定题目内容哈希。完全相同题须修订或剔除，不能靠改ID绕过。

## 冻结与派生导出

数量为工作目标：先每池100题，再按需要到500～1000；不因未达固定数量就阻断冻结，不强制操作配比，也不单设未见组合评价。真正门禁是本人确认知识、训练批次通过抽查、评价全审、族/组三联隔离、疑似重复已解决及测试知识在两池均有覆盖。

```powershell
python -m procurement_transfer freeze --reviewer 你的姓名 --date 实际日期 --notes 已完成集中确认
python -m procurement_transfer verify-freeze
python -m procurement_transfer export-train --output exports/train_v1
```

G1/G3、G2/G4各自逐题输入完全相同，只改变目标。四组文件是派生导出，不是新的人工维护台账。当前data为空，导出和冻结会明确拒绝。

四核心文件集中存放不等于操作系统级测试隔离；普通开发校验不校验测试内容，生成任务仅读取所选蓝图。人工切勿把测试题带回训练会话。冻结保存来源文件、核心数据、配置、提示词与代码哈希；freeze自身也校验完整性，冻结后修订应另建版本。

本次未训练、未诊断模型能力、未产生论文效果指标。后续先对新增48张OCR卡做独立模型复核和本人确认，再规划正式题目。
