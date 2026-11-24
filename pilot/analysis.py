"""三种口径的聚类估计、保障线评估与跨轮证据汇总。

口径约定：
- assigned（原本分到哪一版）：按登记分配比较，回答"此次通知安排的效果"；
- delivered（现场实际可能听到什么）：按实播比较，回答"可识别覆盖范围内
  的口令效果"；维修等偏离会使两种口径分离，需分别报告；
- 核实样本（真正能证实是否学会）：已去重的访问记录。主分析只计
  previous_known 为 no 的户——上一轮已会的不算本轮改善；unknown 作
  敏感性分析；yes 只作结转描述。

不确定度以区组（独立场次）为聚类单位给出；同一覆盖单元内的村不互相
独立，不按村分别估计。
"""

from __future__ import annotations

from .ledger import ARMS, FALLBACK
from .stats import cluster_rate, contrast

LENSES = ("assigned", "delivered")
PRIMARY = ("no",)
WITH_UNKNOWN = ("no", "unknown")
CARRYOVER = ("yes",)


def stratum_key(language: str, service: str) -> str:
    return f"{language}|{service}"


def _select(rows: list[dict], stratum: tuple | None = None, version: str | None = None) -> list[dict]:
    out = []
    for row in rows:
        if stratum is not None and (row["language"], row["service"]) != stratum:
            continue
        if version is not None and row["site_version"] != version:
            continue
        out.append(row)
    return out


def _items(rows: list[dict], lens: str, arm: str, outcome: str, population: tuple) -> list[tuple]:
    """组某口径某臂某结局的聚类观测：(cluster, weight, y)。

    权重为预抽联系概率的倒数；assigned 口径再除以登记的分配概率，
    使不等概率分配仍可比（本台账均为 0.5 时不改变数值）。
    """
    items = []
    for row in rows:
        if not row["verified"] or row["previous_known"] not in population:
            continue
        if row[lens] != arm:
            continue
        weight = 1.0 / row["prob_contact"] if row["prob_contact"] else 1.0
        if lens == "assigned" and row["marginal_prob"]:
            weight /= row["marginal_prob"]
        items.append(((row["round"], row["block"]), weight, 1.0 if row["answer"] == outcome else 0.0))
    return items


def bundle(rows: list[dict], lens: str, population: tuple = PRIMARY) -> dict:
    """某一口径下两臂的理解度与误选聚类估计。"""
    result: dict = {"blocks": {}, "correct": {}, "wrong": {}}
    arms_items = {}
    for arm in ARMS:
        correct_items = _items(rows, lens, arm, "correct", population)
        wrong_items = _items(rows, lens, arm, "wrong", population)
        arms_items[arm] = (correct_items, wrong_items)
        result["blocks"][arm] = len({cluster for cluster, _, _ in correct_items})
        result["correct"][arm] = cluster_rate(correct_items)
        result["wrong"][arm] = cluster_rate(wrong_items)
    result["correct"]["contrast"] = contrast(arms_items["full"][0], arms_items["short"][0])
    result["wrong"]["contrast"] = contrast(arms_items["full"][1], arms_items["short"][1])
    return result


def evaluate_gates(safeguard: dict, assigned_bundle: dict) -> dict:
    """按预先约定的底线评估某分层（语言×时段）的安排效果证据。

    - 独立区组：两臂合计不少于 eligible_blocks 且各臂不少于 2；
    - 理解度改善下限不低于 min_gain；
    - short 臂误选行动比例上限不越过 max_wrong。
    """
    blocks_full = assigned_bundle["blocks"]["full"]
    blocks_short = assigned_bundle["blocks"]["short"]
    blocks_ok = (
        blocks_full >= 2
        and blocks_short >= 2
        and blocks_full + blocks_short >= safeguard["eligible_blocks"]
    )
    gain = assigned_bundle["correct"]["contrast"]
    gain_ok = gain is not None and gain["low"] >= safeguard["min_gain"]
    wrong_short = assigned_bundle["wrong"]["short"]
    wrong_ok = wrong_short is not None and wrong_short["high"] <= safeguard["max_wrong"]
    reasons: list[str] = []
    if not blocks_ok:
        reasons.append(
            f"独立区组不足（full {blocks_full}、short {blocks_short}，"
            f"至少共需 {safeguard['eligible_blocks']} 且各臂不少于 2）"
        )
        verdict = "证据不足"
    else:
        if not gain_ok:
            low = "无法估计" if gain is None else f"{gain['low']:.3f}"
            reasons.append(f"理解度改善下限 {low} 未达到 {safeguard['min_gain']}")
        if not wrong_ok:
            high = "无法估计" if wrong_short is None else f"{wrong_short['high']:.3f}"
            reasons.append(f"误选行动比例上限 {high} 越过 {safeguard['max_wrong']}")
        verdict = "支持推广" if not reasons else "未达标"
    return {
        "blocks_ok": blocks_ok,
        "gain_ok": gain_ok,
        "wrong_ok": wrong_ok,
        "verdict": verdict,
        "reasons": reasons,
    }


def _funnel(rows: list[dict]) -> dict:
    """可比较人群漏斗：名册 → 已访 → 主分析（未学过的户）。"""
    verified = [row for row in rows if row["verified"]]
    return {
        "rostered": len(rows),
        "verified": len(verified),
        "unvisited": len(rows) - len(verified),
        "cross_visited": sum(1 for row in verified if row["cross_visited"]),
        "carryover_yes": sum(1 for row in verified if row["previous_known"] == "yes"),
        "previous_unknown": sum(1 for row in verified if row["previous_known"] == "unknown"),
        "primary": sum(1 for row in verified if row["previous_known"] == "no"),
        "fallback_exposed": sum(1 for row in rows if row["delivered"] == FALLBACK),
        "no_playback": sum(1 for row in rows if not row["delivered"]),
    }


def deviation_strata(ledger, round_id: str) -> tuple[list[dict], set[str]]:
    """某轮分配与实播不一致的记录，以及受影响的分层集合。"""
    ctx = ledger.context(round_id)
    assignments = ledger.assignments(round_id)
    playback = ledger.playback(round_id)
    deviations = []
    affected: set[str] = set()
    for (block, horn), assigned in sorted(assignments.items()):
        played = playback.get((block, horn))
        if played and played["delivered"] != assigned["assigned"]:
            villages = sorted(v for v, h in ctx.village_horn.items() if h == horn)
            strata = sorted(stratum_key(*ctx.village_stratum[v]) for v in villages)
            affected.update(strata)
            deviations.append(
                {
                    "block": block,
                    "horn": horn,
                    "assigned": assigned["assigned"],
                    "delivered": played["delivered"],
                    "site_version": played["site_version"],
                    "villages": villages,
                    "strata": strata,
                }
            )
    return deviations, affected


def round_analysis(ledger, round_id: str) -> dict:
    """单轮核对：偏离、现行信息保障、分层三种口径与保障线判定。"""
    ctx = ledger.context(round_id)
    frame, duplicates = ledger.household_frame(round_id)
    safeguards = ledger.safeguards()

    deviations, affected = deviation_strata(ledger, round_id)

    breaches = sorted({(r["block"], r["village"], r["horn"]) for r in frame if not r["delivered"]})
    current_info = {
        "ok": not breaches,
        "breaches": [{"block": b, "village": v, "horn": h} for b, v, h in breaches],
    }

    opportunities_used: dict[str, dict] = {}
    for row in frame:
        opp = row["opportunity"]
        if opp and opp in ctx.opportunities and opp not in opportunities_used:
            spec = ctx.opportunities[opp]
            opportunities_used[opp] = {
                "site_version": spec["site_version"],
                "standard_required": spec["standard_required"],
                "max_visits": spec["max_visits"],
            }

    strata: dict[str, dict] = {}
    for key, safeguard in sorted(safeguards.items()):
        rows = _select(frame, key)
        lenses = {lens: bundle(rows, lens) for lens in LENSES}
        carryover = {
            arm: cluster_rate(_items(rows, "assigned", arm, "correct", CARRYOVER)) for arm in ARMS
        }
        strata[stratum_key(*key)] = {
            "language": key[0],
            "service": key[1],
            "funnel": _funnel(rows),
            "lens": lenses,
            "sensitivity": {
                "assigned_correct_contrast_incl_unknown": bundle(rows, "assigned", WITH_UNKNOWN)[
                    "correct"
                ]["contrast"]
            },
            "carryover_correct_rate": carryover,
            "safeguard": safeguard,
            "gates": evaluate_gates(safeguard, lenses["assigned"]),
        }

    warnings: list[str] = []
    conflicted = [d for d in duplicates if len(d["answers"]) > 1]
    if conflicted:
        warnings.append(f"{len(conflicted)} 户重复访问且应答不一致，已按本村访问组记录取值")
    missing_prob = sum(1 for row in frame if row["verified"] and not row["prob_contact"])
    if missing_prob:
        warnings.append(f"{missing_prob} 条访问缺少联系概率，按权重 1 计入")

    return {
        "round": round_id,
        "units": [
            {
                "key": u.key,
                "horns": list(u.horns),
                "villages": list(u.villages),
                "teachers": list(u.teachers),
                "available": u.available,
                "strata": [stratum_key(*s) for s in u.strata],
            }
            for u in sorted(ctx.units.values(), key=lambda u: u.key)
        ],
        "opportunities_used": opportunities_used,
        "deviations": deviations,
        "deviation_strata": sorted(affected),
        "current_info": current_info,
        "duplicates": {"count": len(duplicates), "conflicted": len(conflicted)},
        "strata": strata,
        "overall": {lens: bundle(frame, lens) for lens in LENSES},
        "warnings": warnings,
    }


def build_evidence(ledger, rounds: list[str]) -> dict:
    """跨轮证据台账：按 分层 × 站点版次 × 口径 汇总可比较的区组。

    轮间改变了指引地点（站点版次）时，只有同版次的区组可以合并；
    聚类单位为（轮次, 区组），轮次间不共享聚类。某轮因维修等原因
    发生偏离的分层不再并入后续统计（该轮结果仍可按当时安排查回），
    剔除记录留在 excluded 中。
    """
    safeguards = ledger.safeguards()
    versions = ledger.site_versions()
    rows: list[dict] = []
    excluded: list[dict] = []
    for round_id in rounds:
        frame, _ = ledger.household_frame(round_id)
        _, affected = deviation_strata(ledger, round_id)
        for stratum in sorted(affected):
            excluded.append({"round": round_id, "stratum": stratum, "reason": "该轮该分层发生偏离，不并入后续统计"})
        rows.extend(row for row in frame if stratum_key(row["language"], row["service"]) not in affected)

    by_stratum: dict[str, dict] = {}
    for key, safeguard in sorted(safeguards.items()):
        skey = stratum_key(*key)
        stratum_rows = _select(rows, key)
        per_version = {}
        for version in versions:
            version_rows = _select(stratum_rows, version=version)
            lenses = {lens: bundle(version_rows, lens) for lens in LENSES}
            per_version[version] = {
                "lens": lenses,
                "gates": evaluate_gates(safeguard, lenses["assigned"]),
            }
        by_stratum[skey] = {"safeguard": safeguard, "versions": per_version}

    overall: dict[str, dict] = {}
    for version in versions:
        version_rows = _select(rows, version=version)
        overall[version] = {lens: bundle(version_rows, lens) for lens in LENSES}

    return {
        "rounds": list(rounds),
        "versions": versions,
        "excluded": excluded,
        "by_stratum": by_stratum,
        "overall": overall,
    }


def rollout_scope(ledger, evidence: dict, analyses: dict) -> dict:
    """推广范围核对：每个分层能不能推广、依据哪几轮、例外是什么。"""
    analyses = analyses or {}
    strata_out: dict[str, dict] = {}
    cannot: list[dict] = []
    for skey, entry in evidence["by_stratum"].items():
        versions = entry["versions"]
        supported = [v for v, d in versions.items() if d["gates"]["verdict"] == "支持推广"]
        informed = [v for v, d in versions.items() if d["gates"]["blocks_ok"]]
        if supported:
            verdict = "可推广"
            basis = sorted(supported)[0]
            reasons = [f"站点版次 {basis} 的安排效果达到预先约定的底线"]
        elif informed:
            verdict = "暂不推广"
            basis = sorted(informed)[0]
            reasons = list(versions[basis]["gates"]["reasons"])
        else:
            verdict = "无证据"
            basis = None
            reasons = ["各站点版次下都缺少可比较的独立区组，样本无力判断"]
        detail = {}
        for version, data in versions.items():
            assigned = data["lens"]["assigned"]
            detail[version] = {
                "blocks": assigned["blocks"],
                "gates": data["gates"],
            }
        record = {
            "verdict": verdict,
            "basis_version": basis,
            "reasons": reasons,
            "by_version": detail,
            "evidence_rounds": evidence["rounds"],
        }
        strata_out[skey] = record
        if verdict != "可推广":
            cannot.append({"stratum": skey, "verdict": verdict, "reasons": reasons})

    exceptions: list[str] = []
    current_info_ok = True
    for round_id, analysis in analyses.items():
        if analysis["deviations"]:
            horns = sorted({d["horn"] for d in analysis["deviations"]})
            exceptions.append(
                f"{round_id} 有 {len(analysis['deviations'])} 处分配与实播不一致（喇叭 {', '.join(horns)}），"
                "该轮按当时安排留存，后续统计已按实播重判"
            )
        if not analysis["current_info"]["ok"]:
            current_info_ok = False
            villages = sorted({b["village"] for b in analysis["current_info"]["breaches"]})
            exceptions.append(f"{round_id} 现行信息未能保持可达的村：{', '.join(villages)}")

    night = {k: v["verdict"] for k, v in strata_out.items() if k.endswith("|night")}
    dialect = {k: v["verdict"] for k, v in strata_out.items() if k.startswith("jin|")}
    return {
        "strata": strata_out,
        "cannot_roll_out": cannot,
        "current_info_ok": current_info_ok,
        "exceptions": exceptions,
        "key_populations": {"night": night, "jin": dialect},
    }
