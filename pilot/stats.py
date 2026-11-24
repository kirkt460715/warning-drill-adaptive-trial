"""聚类层面的加权比例估计与区间。

只使用标准库。估计以区组（独立场次）为聚类单位：同一区组内同喇叭、
同解释员覆盖的户不互相独立，比例与对比的方差都按聚类稳健方式计算。
"""

from __future__ import annotations

import math

# 双侧 95% t 临界值（自由度 1..30），更大自由度用正态近似。
_T95 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)


def t_critical(df: int) -> float | None:
    """自由度 df 的双侧 95% t 临界值；df<1 时无法给出。"""
    if df < 1:
        return None
    if df <= 30:
        return _T95[df - 1]
    return 1.96


def cluster_rate(items: list[tuple]) -> dict | None:
    """加权比例的聚类稳健估计。

    items 为 (cluster, weight, y) 三元组，y 取 0/1。聚类少于两个时
    无法估计方差，返回 None（调用方应判为证据不足）。
    """
    clusters: dict = {}
    for cluster, weight, y in items:
        cell = clusters.setdefault(cluster, [0.0, 0.0])
        cell[0] += weight * y
        cell[1] += weight
    groups = len(clusters)
    total_w = sum(cell[1] for cell in clusters.values())
    if groups < 2 or total_w <= 0:
        return None
    rate = sum(cell[0] for cell in clusters.values()) / total_w
    ss = sum((cell[0] - rate * cell[1]) ** 2 for cell in clusters.values())
    var = (groups / (groups - 1)) * ss / (total_w ** 2)
    se = math.sqrt(var)
    tc = t_critical(groups - 1)
    return {
        "rate": rate,
        "se": se,
        "clusters": groups,
        "n": len(items),
        "low": rate - tc * se,
        "high": rate + tc * se,
    }


def contrast(control_items: list[tuple], treat_items: list[tuple]) -> dict | None:
    """两臂聚类估计之差（treat − control）及 95% 区间。

    两臂使用互不重叠的聚类集合；任一臂无法估计时返回 None。
    """
    control = cluster_rate(control_items)
    treat = cluster_rate(treat_items)
    if control is None or treat is None:
        return None
    est = treat["rate"] - control["rate"]
    se = math.sqrt(control["se"] ** 2 + treat["se"] ** 2)
    df = control["clusters"] + treat["clusters"] - 2
    tc = t_critical(df)
    return {
        "est": est,
        "se": se,
        "df": df,
        "low": est - tc * se,
        "high": est + tc * se,
        "control": control,
        "treat": treat,
    }
