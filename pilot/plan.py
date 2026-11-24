"""下一轮试点的编排：机会选择、单元分配、核实容量与可支持的比较。

编排原则：
- 只使用台账允许的版本组合（arrangements.variants），且所有候选安排
  均要求现行信息保持可达（standard_required），编排不减少现行渠道；
- 分配按覆盖单元进行：同喇叭或同解释员连成的片只能同进同退，避免把
  一起覆盖的村当成互不影响；
- 证据缺口按 分层 × 站点版次 计算，区组优先补到还无力判断的版次上；
- 现场不可用的单元不安排试播，只保持现行信息；
- 编排结果连同选择的范围与机会一并登记，供执行后按当时安排查回。
"""

from __future__ import annotations

from .analysis import stratum_key
from .ledger import ARMS


def _allocate(need: dict[str, int], total: int) -> tuple[dict[str, int], list[str]]:
    """把区组分配到站点版次：先满足各版次缺口，余量轮流分配保留可比性。"""
    warnings: list[str] = []
    versions = sorted(need)
    order = sorted(versions, key=lambda v: (-need[v], v))
    allocation = {v: 0 for v in versions}
    remaining = total
    # 先满足各版次的证据缺口
    for version in order:
        take = min(need[version], remaining)
        allocation[version] = take
        remaining -= take
    shortfall = {v: need[v] - allocation[v] for v in versions if allocation[v] < need[v]}
    if shortfall:
        warnings.append(f"区组数不足以覆盖全部证据缺口，未满足：{shortfall}")
    # 暂无缺口的版次各保留两个区组作监测（预算允许时）
    for version in order:
        if allocation[version] == 0 and remaining >= 2:
            allocation[version] = 2
            remaining -= 2
    # 余量在版次间轮流分配
    while remaining > 0:
        for version in order:
            if remaining <= 0:
                break
            allocation[version] += 1
            remaining -= 1
    return allocation, warnings


def _spread(allocation: dict[str, int]) -> list[str]:
    """把各区组应使用的版次均匀铺开，避免同一版次挤在相邻区组。"""
    remaining = dict(allocation)
    sequence: list[str] = []
    while any(count > 0 for count in remaining.values()):
        for version in sorted(remaining):
            if remaining[version] > 0:
                sequence.append(version)
                remaining[version] -= 1
    return sequence


def plan_round(ledger, evidence: dict, round_id: str, n_blocks: int = 12) -> dict:
    """为指定轮次生成可执行的播报与核实安排。"""
    ctx = ledger.context(round_id)
    if ctx is None:
        raise ValueError(f"{round_id} 缺少覆盖图与候选安排，无法编排")
    if not ctx.opportunities:
        raise ValueError(f"{round_id} 没有候选安排（arrangements）")
    safeguards = ledger.safeguards()
    opportunities = ctx.opportunities
    versions = sorted({spec["site_version"] for spec in opportunities.values()})

    # 证据缺口：按版次列出尚未达到"支持推广"的分层
    deficits: dict[str, list] = {}
    for version in versions:
        lacking = []
        for key, safeguard in sorted(safeguards.items()):
            skey = stratum_key(*key)
            gates = (
                evidence.get("by_stratum", {})
                .get(skey, {})
                .get("versions", {})
                .get(version, {})
                .get("gates")
            )
            if gates is None or gates["verdict"] != "支持推广":
                lacking.append(
                    {
                        "stratum": skey,
                        "eligible_blocks": safeguard["eligible_blocks"],
                        "reason": "无证据" if gates is None or not gates["blocks_ok"] else "；".join(gates["reasons"]),
                    }
                )
        deficits[version] = lacking
    need_blocks = {
        v: max((d["eligible_blocks"] for d in lacking), default=0) + 2 if lacking else 0
        for v, lacking in deficits.items()
    }
    confirmatory = not any(need_blocks.values())
    if confirmatory:
        need_blocks = {v: 4 for v in versions}
    allocation, warnings = _allocate(need_blocks, n_blocks)
    sequence = _spread(allocation)

    # 每个版次选定机会：优先两臂都允许的，其次核实席位多的
    opportunity_by_version: dict[str, str] = {}
    for version in versions:
        candidates = [
            (oid, spec) for oid, spec in opportunities.items() if spec["site_version"] == version
        ]
        candidates.sort(key=lambda item: (not set(ARMS) <= item[1]["variants"], -item[1]["max_visits"], item[0]))
        opportunity_by_version[version] = candidates[0][0]

    blocks = [f"B{i:02d}" for i in range(1, n_blocks + 1)]
    block_plan: dict[str, dict] = {}
    positions: dict[str, int] = {}
    for block, version in zip(blocks, sequence):
        opp = opportunity_by_version[version]
        spec = opportunities[opp]
        position = positions.get(version, 0)
        positions[version] = position + 1
        block_plan[block] = {
            "opportunity": opp,
            "site_version": version,
            "position": position,
            "max_visits": spec["max_visits"],
            "variants": sorted(spec["variants"]),
        }

    # 单元分配：同单元同进同退；每个版次内各单元两臂轮换，保证均衡
    units = sorted(ctx.units.values(), key=lambda u: u.key)
    assignments: list[dict] = []
    notes: list[str] = []
    for block in blocks:
        plan = block_plan[block]
        allowed = set(plan["variants"])
        for index, unit in enumerate(units):
            if not unit.available:
                continue
            if set(ARMS) <= allowed:
                arm = "short" if (plan["position"] + index) % 2 == 0 else "full"
                prob = "0.5"
            else:
                arm = sorted(allowed)[0]
                prob = "1"
            for horn in unit.horns:
                assignments.append(
                    {
                        "round": round_id,
                        "block": block,
                        "horn": horn,
                        "assigned": arm,
                        "marginal_prob": prob,
                        "opportunity": plan["opportunity"],
                    }
                )

    for unit in units:
        if not unit.available:
            notes.append(f"单元 {unit.key}（{', '.join(unit.villages)}）现场不可用，本轮不安排试播，仅保持现行信息")
        elif len(unit.villages) > 1:
            notes.append(
                f"单元 {unit.key} 共用一台喇叭覆盖 {', '.join(unit.villages)}，"
                "这些村不互相独立，按同一单元分配"
            )
    for item in evidence.get("excluded", []):
        notes.append(f"{item['round']} 的 {item['stratum']} 因偏离未并入证据台账，缺口已计入本轮目标")

    # 与上一轮相比的覆盖与人员变化
    completed = ledger.completed_rounds()
    if completed:
        previous = ledger.context(completed[-1])
        if previous is not None:
            for village in sorted(ctx.village_horn):
                old_horn = previous.village_horn.get(village)
                if old_horn and old_horn != ctx.village_horn[village]:
                    notes.append(f"{village} 的喇叭由 {old_horn} 变为 {ctx.village_horn[village]}")
                old_teacher = previous.village_teacher.get(village)
                if old_teacher and old_teacher != ctx.village_teacher.get(village):
                    notes.append(f"{village} 的解释员由 {old_teacher} 变为 {ctx.village_teacher[village]}")
    teacher_villages: dict[str, list] = {}
    for village, teacher in ctx.village_teacher.items():
        teacher_villages.setdefault(teacher, []).append(village)
    for teacher, villages in sorted(teacher_villages.items()):
        if len(villages) > 1:
            notes.append(f"解释员 {teacher} 本轮同时负责 {', '.join(sorted(villages))}，相关村的结果不互相独立")

    # 结转提示：上一轮已会口令的户不计入本轮新学会
    carryover_notes: list[str] = []
    if completed:
        last_roster = ledger.roster(completed[-1])
        seen: dict[tuple, list] = {}
        for row in last_roster:
            key = (row["village"], row["household"])
            seen.setdefault(key, []).append(row["previous_known"])
        stratum_flags: dict[str, list] = {}
        for (village, _), flags in seen.items():
            stratum = ctx.village_stratum.get(village)
            if stratum is None:
                continue
            skey = stratum_key(*stratum)
            stratum_flags.setdefault(skey, []).append(any(f != "no" for f in flags))
        for skey, flags in sorted(stratum_flags.items()):
            share = sum(flags) / len(flags)
            if share > 0:
                carryover_notes.append(
                    f"分层 {skey} 上轮约 {share:.0%} 的户已会或状态未知，"
                    "本轮新学会结论只基于 previous_known=no 的户"
                )
    notes.extend(carryover_notes)
    notes.append("所有候选安排均要求现行信息保持可达；本编排不减少现行渠道")
    if len(versions) > 1:
        notes.append(f"本轮同时使用站点版次 {', '.join(versions)}，跨版次比较按版次分层汇总")

    # 核实容量：按缺口加权分配到村，每区组不超过该机会的席位上限
    deficit_strata = {d["stratum"] for lacking in deficits.values() for d in lacking}
    village_sizes: dict[str, int] = {}
    if completed:
        for row in ledger.roster(completed[-1]):
            village_sizes.setdefault(row["village"], set())
            village_sizes[row["village"]].add(row["household"])
    visits: list[dict] = []
    for block in blocks:
        plan = block_plan[block]
        capacity = plan["max_visits"]
        weights = {}
        for village in sorted(ctx.village_horn):
            stratum = ctx.village_stratum.get(village, ("", ""))
            size = len(village_sizes.get(village, ())) or 1
            weights[village] = size * (3 if stratum_key(*stratum) in deficit_strata else 1)
        total_weight = sum(weights.values())
        sizes = {v: len(village_sizes.get(v, ())) or 1 for v in weights}
        targets = {
            v: min(sizes[v], max(1, int(capacity * w / total_weight))) for v, w in weights.items()
        }
        while sum(targets.values()) > capacity:
            victim = max(targets, key=lambda v: (targets[v], v))
            if targets[victim] <= 1:
                break
            targets[victim] -= 1
        for village, target in sorted(targets.items()):
            visits.append(
                {
                    "block": block,
                    "village": village,
                    "target_visits": target,
                    "roster_size": sizes[village],
                }
            )

    # 本轮可支持的比较：按分层 × 版次预计两臂区组数
    expected: dict[str, dict] = {}
    horn_arm = {(a["block"], a["horn"]): a["assigned"] for a in assignments}
    for key, safeguard in sorted(safeguards.items()):
        skey = stratum_key(*key)
        villages = [v for v, s in ctx.village_stratum.items() if s == key]
        per_version: dict[str, dict] = {}
        for version in versions:
            counts = {arm: 0 for arm in ARMS}
            for block in blocks:
                if block_plan[block]["site_version"] != version:
                    continue
                arms_seen = {
                    horn_arm.get((block, ctx.village_horn[v])) for v in villages if v in ctx.village_horn
                }
                for arm in arms_seen:
                    if arm in counts:
                        counts[arm] += 1
            per_version[version] = {
                "full_blocks": counts["full"],
                "short_blocks": counts["short"],
                "meets_eligible": counts["full"] >= 2
                and counts["short"] >= 2
                and counts["full"] + counts["short"] >= safeguard["eligible_blocks"],
            }
        expected[skey] = per_version

    feasible = not warnings and all(unit.available for unit in units)
    return {
        "round": round_id,
        "n_blocks": n_blocks,
        "confirmatory": confirmatory,
        "deficits": deficits,
        "need_blocks": need_blocks,
        "allocation": allocation,
        "blocks": [
            {
                "block": block,
                "opportunity": block_plan[block]["opportunity"],
                "site_version": block_plan[block]["site_version"],
                "max_visits": block_plan[block]["max_visits"],
            }
            for block in blocks
        ],
        "assignments": assignments,
        "visits": visits,
        "units": [
            {
                "key": u.key,
                "horns": list(u.horns),
                "villages": list(u.villages),
                "teachers": list(u.teachers),
                "available": u.available,
            }
            for u in units
        ],
        "expected": expected,
        "notes": notes,
        "warnings": warnings,
        "feasible": feasible,
    }
