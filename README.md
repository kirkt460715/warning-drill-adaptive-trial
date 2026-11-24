# 广播疏散口令的现场理解度试点

这些演练台账是本地合成资料，使用虚构编号。CSV 采用 UTF-8 和带表头的逗号分隔格式。同一单元内用竖线列出的值表示多项或候选集合，含义见各表说明。空值通常表示未记录或不适用，明确的零会保留为 0。

## 资料

- `data/catchments.csv`  轮次开始时公布的可听区域。teacher 为解释员组。每轮改动不追溯前轮，false 表示现场不可使用。
  字段为 `round / village / horn / language / teacher / service / available`。
- `data/arrangements.csv`  候选历史安排以及下轮允许的脚本。standard_required 为继续传达现行信息要求。max_visits 是该安排独立核实席位总数。编码按说明短口令 short 和现行 full。
  字段为 `round / opportunity / variants / standard_required / max_visits / site_version`。
- `data/safeguards.csv`  试点核准依据。min_gain 是理解率的最低改善，max_wrong 是不可越过的错误行动比例；eligible_blocks 为正常比较至少需要的独立演练区组。
  字段为 `language / service / min_gain / max_wrong / eligible_blocks`。
- `data/assignments.csv`  分配单位是同一轮独立场次的喇叭，记录本轮可核实的实际分配概率。
  字段为 `round / block / horn / assigned / marginal_prob / opportunity`。
- `data/roster.csv`  现场匿名户号。previous_known 指此前是否已熟悉相同口令。
  字段为 `round / block / household / village / horn / previous_known`。
- `data/playback.csv`  现场录下的播发脚本与行动地点版次。full_only 为维修期间只有现行渠道继续播报。
  字段为 `round / block / horn / delivered / site_version`。
- `data/interviews.csv`  按名单访问的应答及执行访问组，prob_contact 为已登记的预抽联系机会。
  字段为 `round / block / household / visiting_group / answer / prob_contact`。

## 核对台账

环境为 Python 3.11.2，只使用标准库。在仓库根目录运行下列命令检查文件格式、表头和基础外键。输出为各张源表可读行数。

```sh
python3 input_audit.py
python3 -m unittest discover -s tests -v
```

可在第一条命令后指定另一个输入目录，目录中的同名 CSV 沿用上述格式。列名和基础引用清单见 `input_contract.json`。

## 逐轮核对与编排

`round_review.py` 在格式核对之上做三件事：核对已完成轮次、汇总跨轮证据并核对推广范围、为下一个待办轮次生成可执行编排。只使用标准库。

```sh
python3 round_review.py                  # 核对已完成轮次，并编排最早的待办轮次
python3 round_review.py --round R2       # 只按当时登记安排查回 R2 的核对结果
python3 round_review.py --plan-round R3 --blocks 12 --out out
```

### 口径

- **原本分到哪一版（assigned）**：按登记分配比较，回答"此次通知安排的效果"；
- **现场实际可能听到什么（delivered）**：按实播比较，回答"可识别覆盖范围内的口令效果"。维修等偏离（如 R2 的 H3 全部塌缩为 `full_only`）会使两种口径分离，分别报告；`full_only` 不算任何一臂；
- **真正能证实是否学会（核实样本）**：访问记录按（区组, 户）去重，被两村同时访问的户只计一次；主分析只计 `previous_known=no` 的户——上一轮已会的不算本轮改善，`unknown` 作敏感性分析。未访问户通过预抽联系概率的倒数加权回到名册人群。

同一台喇叭覆盖的村不互相独立：分配与估计都按喇叭单元对齐，不确定度以区组（独立场次）为聚类单位给出（95% 区间，t 临界值）。同一解释员跨村服务不捆住分配，但会登记为相关性提示。

### 判定

每个 语言×时段 分层对照 safeguards 的预先约定：独立区组数（两臂合计不少于 `eligible_blocks` 且各臂不少于 2）、理解度改善下限不低于 `min_gain`、short 臂误选行动比例上限不越过 `max_wrong`。结论分为 支持推广 / 未达标 / 证据不足。跨轮证据按 分层 × 站点版次 汇总——轮间改变指引地点时只有同版次可合并；发生偏离的轮次分层不并入后续统计（该轮结果仍可按当时安排查回），剔除记录留在证据台账的 `excluded` 中。现行信息保障逐轮核查：任何名册户的喇叭缺少播放记录即列为例外。

### 产物（留存证据）

写入 `--out` 目录：

- `analysis_R*.json` 各轮核对：覆盖单元、偏离、可比较人群漏斗、三种口径的聚类估计与分层判定；
- `evidence_ledger.json` 跨轮证据台账（分层 × 版次 × 口径，含剔除记录）；
- `rollout_scope.json` 推广范围核对：每个分层能否推广、依据哪几轮哪个版次、不能推广到哪里及原因、夜间与方言区分层的状态、现行信息保障是否失效；
- `plan_R*.json` 与 `plan_R*_assignments.csv`、`plan_R*_visits.csv` 下一轮编排：登记实际选择的范围与机会、单元分配（含分配概率）、按证据缺口加权的核实席位（不超过各机会 `max_visits` 与村名册规模）、本轮可支持的比较与现场注意事项（共用喇叭、解释员变更、结转比例、版次混用）。

样本无力判断时，编排会把区组优先补到对应 分层 × 版次 的缺口上；现场不可用的单元不安排试播，只保持现行信息。
