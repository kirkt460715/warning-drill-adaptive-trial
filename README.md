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
