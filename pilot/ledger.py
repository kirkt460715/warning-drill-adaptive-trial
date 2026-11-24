"""读取台账并整理轮次领域对象。

台账格式见 input_contract.json，交付前应先通过 input_audit.audit 核对。
本模块在核对过的表格上建立：

- 覆盖单元：同村、同喇叭连成的片。分配单位是同一轮独立场次的喇叭，
  共用一台喇叭的村只能同进同退；单元内各村不互相独立，避免把一起
  覆盖的村当成互不影响。同一解释员跨村服务不捆住分配，但会作为
  相关性提示登记（见 RoundContext.teacher_groups）。
- 轮次上下文：候选机会、站点版次、现场可用性、语言×时段分层。
- 户级帧：把分配、实播、核实应答与此前是否会口令接到同一行上，
  供"原本分到／现场可听／核实证实"三种口径分别统计。
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

TABLES = (
    "catchments",
    "arrangements",
    "safeguards",
    "assignments",
    "roster",
    "playback",
    "interviews",
)

# 可比较的两臂；FALLBACK 是维修期间仅现行渠道继续播报的状态，不参与对比。
ARMS = ("full", "short")
FALLBACK = "full_only"
ANSWERS = ("correct", "wrong", "unclear")


def load_tables(directory) -> dict[str, list[dict]]:
    """读取台账目录；缺失的表按空表处理（未开始的轮次本就没有分配）。"""
    directory = Path(directory)
    tables: dict[str, list[dict]] = {}
    for name in TABLES:
        path = directory / (name + ".csv")
        if not path.exists():
            tables[name] = []
            continue
        with path.open(encoding="utf-8", newline="") as stream:
            tables[name] = list(csv.DictReader(stream))
    return tables


def _components(edges: list[tuple]) -> list[set]:
    """按边集求连通分量，用于把村、喇叭、解释员连成覆盖单元。"""
    parent: dict = {}

    def find(node):
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in edges:
        root_l, root_r = find(left), find(right)
        if root_l != root_r:
            parent[root_r] = root_l
    groups: dict = {}
    for node in list(parent):
        groups.setdefault(find(node), set()).add(node)
    return list(groups.values())


@dataclass(frozen=True)
class Unit:
    """共用一台喇叭连成的一片；单元内各村不互相独立。"""

    key: str
    horns: tuple
    villages: tuple
    teachers: tuple
    available: bool
    strata: tuple


class RoundContext:
    """单轮的覆盖图与候选安排。"""

    def __init__(self, round_id: str, catchments: list[dict], arrangements: list[dict]):
        self.round = round_id
        self.opportunities: dict[str, dict] = {}
        for row in arrangements:
            self.opportunities[row["opportunity"]] = {
                "variants": frozenset(v for v in row["variants"].split("|") if v),
                "standard_required": row["standard_required"] == "true",
                "max_visits": int(row["max_visits"]),
                "site_version": row["site_version"],
            }
        edges: list[tuple] = []
        self.village_horn: dict[str, str] = {}
        self.village_stratum: dict[str, tuple] = {}
        self.village_teacher: dict[str, str] = {}
        self.village_available: dict[str, bool] = {}
        for row in catchments:
            village, horn, teacher = row["village"], row["horn"], row["teacher"]
            # 只有"一起覆盖"（同喇叭）才捆住分配；解释员跨村另行登记
            edges.append((("village", village), ("horn", horn)))
            self.village_horn[village] = horn
            self.village_stratum[village] = (row["language"], row["service"])
            self.village_teacher[village] = teacher
            self.village_available[village] = row["available"] == "true"
        units: list[Unit] = []
        for group in _components(edges):
            villages = sorted(node[1] for node in group if node[0] == "village")
            horns = sorted(node[1] for node in group if node[0] == "horn")
            teachers = sorted({self.village_teacher[v] for v in villages})
            units.append(
                Unit(
                    key="|".join(horns),
                    horns=tuple(horns),
                    villages=tuple(villages),
                    teachers=tuple(teachers),
                    available=all(self.village_available[v] for v in villages),
                    strata=tuple(sorted({self.village_stratum[v] for v in villages})),
                )
            )
        self.units = {unit.key: unit for unit in units}
        self.village_unit: dict[str, str] = {}
        for unit in units:
            for village in unit.villages:
                self.village_unit[village] = unit.key
        # 同一解释员跨村服务：不捆住分配，但相关村的结果不互相独立
        self.teacher_groups: dict[str, list] = {}
        for village, teacher in self.village_teacher.items():
            self.teacher_groups.setdefault(teacher, []).append(village)


def _dedupe(rows: list[dict], roster_village: dict) -> tuple[dict, list]:
    """访问记录按（区组, 户）去重。

    被两组同时访问的户保留本村访问组的记录（缺失时取首条），并标记
    cross_visited；应答不一致的重复记录列入 duplicates 供核对。
    """
    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        groups[(row["block"], row["household"])].append(row)
    deduped: dict[tuple, dict] = {}
    duplicates: list[dict] = []
    for key, records in groups.items():
        visitors = sorted({r["visiting_group"] for r in records})
        own = roster_village.get(key)
        chosen = next((r for r in records if r["visiting_group"] == own), records[0])
        deduped[key] = {
            "visiting_group": chosen["visiting_group"],
            "answer": chosen["answer"],
            "prob_contact": chosen["prob_contact"],
            "cross_visited": len(visitors) > 1,
            "visiting_groups": visitors,
        }
        if len(records) > 1:
            duplicates.append(
                {
                    "block": key[0],
                    "household": key[1],
                    "visiting_groups": visitors,
                    "answers": sorted({r["answer"] for r in records}),
                }
            )
    return deduped, duplicates


class Ledger:
    """核对过的台账，按轮次提供上下文、户级帧与各表索引。"""

    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables
        self._contexts: dict[str, RoundContext] = {}

    @classmethod
    def load(cls, directory) -> "Ledger":
        return cls(load_tables(directory))

    def rounds(self) -> list[str]:
        seen = {row["round"] for table in self.tables.values() for row in table}
        return sorted(seen)

    def context(self, round_id: str) -> RoundContext | None:
        if round_id not in self._contexts:
            catchments = [r for r in self.tables["catchments"] if r["round"] == round_id]
            arrangements = [r for r in self.tables["arrangements"] if r["round"] == round_id]
            if not catchments and not arrangements:
                return None
            self._contexts[round_id] = RoundContext(round_id, catchments, arrangements)
        return self._contexts[round_id]

    def safeguards(self) -> dict[tuple, dict]:
        out: dict[tuple, dict] = {}
        for row in self.tables["safeguards"]:
            out[(row["language"], row["service"])] = {
                "min_gain": float(row["min_gain"]),
                "max_wrong": float(row["max_wrong"]),
                "eligible_blocks": int(row["eligible_blocks"]),
            }
        return out

    def assignments(self, round_id: str) -> dict[tuple, dict]:
        return {
            (r["block"], r["horn"]): r
            for r in self.tables["assignments"]
            if r["round"] == round_id
        }

    def playback(self, round_id: str) -> dict[tuple, dict]:
        return {
            (r["block"], r["horn"]): r
            for r in self.tables["playback"]
            if r["round"] == round_id
        }

    def roster(self, round_id: str) -> list[dict]:
        return [r for r in self.tables["roster"] if r["round"] == round_id]

    def interviews(self, round_id: str) -> list[dict]:
        return [r for r in self.tables["interviews"] if r["round"] == round_id]

    def completed_rounds(self) -> list[str]:
        """已有分配、实播和访问记录、可以核对的轮次。"""
        rounds = {r["round"] for r in self.tables["assignments"]}
        rounds &= {r["round"] for r in self.tables["playback"]}
        rounds &= {r["round"] for r in self.tables["interviews"]}
        return sorted(rounds)

    def plannable_rounds(self) -> list[str]:
        """已有覆盖图和候选安排、尚未开始分配的轮次。"""
        ready = {r["round"] for r in self.tables["catchments"]}
        ready &= {r["round"] for r in self.tables["arrangements"]}
        started = {r["round"] for r in self.tables["assignments"]}
        return sorted(ready - started)

    def site_versions(self) -> list[str]:
        versions = {r["site_version"] for r in self.tables["arrangements"]}
        versions |= {r["site_version"] for r in self.tables["playback"]}
        return sorted(versions)

    def household_frame(self, round_id: str) -> tuple[list[dict], list[dict]]:
        """户级帧：每行一户一场次，带齐三种口径所需的全部字段。

        返回 (frame, duplicates)；duplicates 是被重复访问的户清单。
        """
        ctx = self.context(round_id)
        if ctx is None:
            raise ValueError(f"{round_id} 缺少覆盖图与候选安排")
        assignments = self.assignments(round_id)
        playback = self.playback(round_id)
        roster = self.roster(round_id)
        roster_village = {(r["block"], r["household"]): r["village"] for r in roster}
        deduped, duplicates = _dedupe(self.interviews(round_id), roster_village)
        frame: list[dict] = []
        for row in roster:
            key = (row["block"], row["household"])
            horn = row["horn"]
            assigned = assignments.get((row["block"], horn))
            played = playback.get((row["block"], horn))
            visit = deduped.get(key)
            village = row["village"]
            stratum = ctx.village_stratum.get(village, ("", ""))
            prob = None
            if visit is not None:
                try:
                    prob = float(visit["prob_contact"])
                except (TypeError, ValueError):
                    prob = None
            frame.append(
                {
                    "round": round_id,
                    "block": row["block"],
                    "household": row["household"],
                    "village": village,
                    "horn": horn,
                    "unit": ctx.village_unit.get(village, ""),
                    "language": stratum[0],
                    "service": stratum[1],
                    "previous_known": row["previous_known"],
                    "assigned": assigned["assigned"] if assigned else None,
                    "marginal_prob": float(assigned["marginal_prob"]) if assigned else None,
                    "opportunity": assigned["opportunity"] if assigned else None,
                    "delivered": played["delivered"] if played else None,
                    "site_version": played["site_version"] if played else None,
                    "verified": visit is not None,
                    "answer": visit["answer"] if visit else None,
                    "prob_contact": prob,
                    "cross_visited": visit["cross_visited"] if visit else False,
                    "visiting_groups": visit["visiting_groups"] if visit else [],
                }
            )
        return frame, duplicates
