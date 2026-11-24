"""逐轮核对、证据汇总与下一轮编排。

用法：
    python3 round_review.py [台账目录] [--round R2] [--plan-round R3] [--blocks 12] [--out out]

流程：先按 input_audit 核对交付格式，再逐轮核对已完成的轮次，汇总跨轮
证据台账，给出推广范围核对结论，并为尚未开始的轮次生成可执行编排。
各轮核对结果只依赖该轮登记的分配与实播，可按当时安排随时查回。
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from input_audit import audit
from pilot import analysis as analysis_mod
from pilot import ledger as ledger_mod
from pilot import plan as plan_mod
from pilot import report


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("location", nargs="?", type=Path, default=Path("data"), help="台账目录")
    parser.add_argument("--round", dest="only_round", help="只核对并输出指定轮次")
    parser.add_argument("--plan-round", dest="plan_round", help="为指定轮次生成编排（默认取最早的待编排轮次）")
    parser.add_argument("--blocks", type=int, default=12, help="下一轮区组数")
    parser.add_argument("--out", type=Path, default=Path("out"), help="证据与编排的输出目录")
    args = parser.parse_args()

    counts, issues = audit(args.location)
    if issues:
        print(json.dumps({"源表行数": counts, "格式问题": issues}, ensure_ascii=False))
        return 1

    ledger = ledger_mod.Ledger.load(args.location)
    completed = ledger.completed_rounds()
    if args.only_round:
        if args.only_round not in completed:
            print(f"{args.only_round} 还没有可核对的分配、实播与访问记录")
            return 1
        completed = [args.only_round]

    analyses = {round_id: analysis_mod.round_analysis(ledger, round_id) for round_id in completed}
    out = args.out
    for round_id, result in analyses.items():
        _dump(out / f"analysis_{round_id}.json", result)

    sections = [report.render_round(result) for result in analyses.values()]

    if not args.only_round:
        evidence = analysis_mod.build_evidence(ledger, completed)
        scope = analysis_mod.rollout_scope(ledger, evidence, analyses)
        _dump(out / "evidence_ledger.json", evidence)
        _dump(out / "rollout_scope.json", scope)
        sections.append(report.render_scope(scope, completed))

        target = args.plan_round
        if target is None:
            candidates = ledger.plannable_rounds()
            target = candidates[0] if candidates else None
        if target is not None:
            plan = plan_mod.plan_round(ledger, evidence, target, n_blocks=args.blocks)
            _dump(out / f"plan_{target}.json", plan)
            assignments_file = out / f"plan_{target}_assignments.csv"
            with assignments_file.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=["round", "block", "horn", "assigned", "marginal_prob", "opportunity"]
                )
                writer.writeheader()
                writer.writerows(plan["assignments"])
            visits_file = out / f"plan_{target}_visits.csv"
            with visits_file.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["block", "village", "target_visits", "roster_size"])
                writer.writeheader()
                writer.writerows(plan["visits"])
            sections.append(report.render_plan(plan))
        else:
            sections.append("没有待编排的轮次（缺少覆盖图或候选安排）")

    print("\n\n".join(sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
