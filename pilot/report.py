"""把分析与编排结果渲染成中文文本，供现场核对与留存。"""

from __future__ import annotations


def _pct(value) -> str:
    return "—" if value is None else f"{value:.3f}"


def _ci(entry) -> str:
    if entry is None:
        return "无法估计"
    return f"{_pct(entry['rate'])} [{_pct(entry['low'])}, {_pct(entry['high'])}]（{entry['clusters']} 区组）"


def _contrast_line(entry) -> str:
    if entry is None:
        return "无法估计"
    return f"{_pct(entry['est'])} [{_pct(entry['low'])}, {_pct(entry['high'])}]"


def render_round(analysis: dict) -> str:
    """单轮核对结果的文本摘要。"""
    lines = [f"轮次 {analysis['round']}（安排效果＝按分配，口令效果＝按实播；主分析只计此前未学的户）"]
    units = []
    for unit in analysis["units"]:
        members = "、".join(f"{v}" for v in unit["villages"])
        flag = "" if unit["available"] else "（现场不可用）"
        units.append(f"{unit['key']}→{members}{flag}")
    lines.append("  覆盖单元：" + "；".join(units))
    if analysis["deviations"]:
        horns = sorted({d["horn"] for d in analysis["deviations"]})
        lines.append(
            f"  偏离：{len(analysis['deviations'])} 处分配与实播不一致（喇叭 {', '.join(horns)}），按当时安排留存"
        )
    else:
        lines.append("  偏离：无（分配与实播一致）")
    info = analysis["current_info"]
    lines.append("  现行信息：" + ("各村的现行信息均保持可达" if info["ok"] else f"存在不可达：{info['breaches']}"))
    dup = analysis["duplicates"]
    if dup["count"]:
        lines.append(f"  重复访问：{dup['count']} 户被两组访问，已去重（应答不一致 {dup['conflicted']} 户）")
    for skey, stratum in analysis["strata"].items():
        sg = stratum["safeguard"]
        lines.append(
            f"  分层 {skey}（底线：改善≥{sg['min_gain']}，误选≤{sg['max_wrong']}，区组≥{sg['eligible_blocks']}）"
        )
        funnel = stratum["funnel"]
        lines.append(
            "    可比较人群：名册 {rostered} → 已访 {verified}（未访 {unvisited}，两村同访 {cross_visited}）"
            " → 主分析 {primary}；结转已会 {carryover_yes}、状态未知 {previous_unknown} 不计入改善".format(**funnel)
        )
        if funnel["fallback_exposed"]:
            lines.append(f"    维修影响：{funnel['fallback_exposed']} 户次仅听到现行渠道（full_only）")
        assigned = stratum["lens"]["assigned"]
        delivered = stratum["lens"]["delivered"]
        lines.append(f"    安排效果（按分配）：理解度差 {_contrast_line(assigned['correct']['contrast'])}；"
                     f"误选 short 臂 {_ci(assigned['wrong']['short'])}")
        lines.append(f"    口令效果（按实播）：理解度差 {_contrast_line(delivered['correct']['contrast'])}；"
                     f"误选 short 臂 {_ci(delivered['wrong']['short'])}")
        gates = stratum["gates"]
        verdict = gates["verdict"] if gates["verdict"] == "支持推广" else f"{gates['verdict']}（{'；'.join(gates['reasons'])}）"
        lines.append(f"    判定：{verdict}")
    overall = analysis["overall"]["assigned"]
    lines.append(f"  整体安排效果：理解度差 {_contrast_line(overall['correct']['contrast'])}；"
                 f"误选差 {_contrast_line(overall['wrong']['contrast'])}")
    for warning in analysis["warnings"]:
        lines.append(f"  提醒：{warning}")
    return "\n".join(lines)


def render_scope(scope: dict, rounds: list[str]) -> str:
    """推广范围核对的文本摘要。"""
    lines = [f"推广范围核对（依据轮次：{', '.join(rounds) or '无'}；证据按 语言|时段 × 站点版次 汇总）"]
    for skey, record in scope["strata"].items():
        basis = f"，依据版次 {record['basis_version']}" if record["basis_version"] else ""
        lines.append(f"  {skey}：{record['verdict']}{basis}")
        for reason in record["reasons"]:
            lines.append(f"    - {reason}")
    lines.append("  现行信息保障：" + ("各轮均保持可达" if scope["current_info_ok"] else "存在失效，见例外"))
    night = scope["key_populations"]["night"]
    dialect = scope["key_populations"]["jin"]
    lines.append("  夜间分层：" + ("、".join(f"{k}={v}" for k, v in night.items()) or "无"))
    lines.append("  方言区分层：" + ("、".join(f"{k}={v}" for k, v in dialect.items()) or "无"))
    if scope["cannot_roll_out"]:
        lines.append("  不能推广到：")
        for item in scope["cannot_roll_out"]:
            lines.append(f"    - {item['stratum']}（{item['verdict']}：{'；'.join(item['reasons'])}）")
    for exception in scope["exceptions"]:
        lines.append(f"  例外：{exception}")
    return "\n".join(lines)


def render_plan(plan: dict) -> str:
    """下一轮编排的文本摘要。"""
    lines = [f"轮次 {plan['round']} 编排（{plan['n_blocks']} 个区组"
             + ("，巩固性轮次" if plan["confirmatory"] else "") + "）"]
    for version, lacking in sorted(plan["deficits"].items()):
        if lacking:
            items = "、".join(f"{d['stratum']}（{d['reason']}）" for d in lacking)
            lines.append(f"  证据缺口 {version}：{items}")
    allocation = "、".join(f"{v} {n} 区组" for v, n in sorted(plan["allocation"].items()))
    lines.append(f"  版次分配：{allocation}")
    opp_seen = {}
    for block in plan["blocks"]:
        opp_seen.setdefault(block["opportunity"], []).append(block["block"])
    for opp, blocks in sorted(opp_seen.items()):
        lines.append(f"  机会 {opp}：{len(blocks)} 区组（{blocks[0]}…{blocks[-1]}）")
    arms = {}
    for row in plan["assignments"]:
        arms[row["assigned"]] = arms.get(row["assigned"], 0) + 1
    lines.append("  分配：" + "、".join(f"{arm} {n} 喇叭·区组" for arm, n in sorted(arms.items())))
    capacity_ok = all(
        sum(v["target_visits"] for v in plan["visits"] if v["block"] == block["block"]) <= block["max_visits"]
        for block in plan["blocks"]
    )
    lines.append("  核实容量：每区组均不超过席位上限" if capacity_ok else "  核实容量：存在超限，需调整")
    for skey, versions in sorted(plan["expected"].items()):
        marks = []
        for version, counts in sorted(versions.items()):
            flag = "可检验" if counts["meets_eligible"] else "不足以单独检验"
            marks.append(f"{version}（full {counts['full_blocks']}、short {counts['short_blocks']}，{flag}）")
        lines.append(f"  预计 {skey}：" + "；".join(marks))
    for note in plan["notes"]:
        lines.append(f"  注意：{note}")
    for warning in plan["warnings"]:
        lines.append(f"  警告：{warning}")
    lines.append("  可执行性：" + ("现场可执行" if plan["feasible"] else "存在限制，见警告"))
    return "\n".join(lines)
