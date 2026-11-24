"""pilot 工具包的单元测试：口径、结转、偏离、保障线与编排。"""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from pilot import analysis, ledger, plan, stats
from pilot.analysis import evaluate_gates

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_ledger():
    return ledger.Ledger.load(DATA)


class StatsTests(unittest.TestCase):
    def test_t_critical(self):
        self.assertAlmostEqual(stats.t_critical(1), 12.706)
        self.assertAlmostEqual(stats.t_critical(11), 2.201)
        self.assertAlmostEqual(stats.t_critical(30), 2.042)
        self.assertAlmostEqual(stats.t_critical(1000), 1.96)
        self.assertIsNone(stats.t_critical(0))

    def test_cluster_rate_hand_computed(self):
        items = [("A", 1.0, 1.0), ("A", 1.0, 0.0), ("B", 1.0, 1.0), ("B", 1.0, 1.0)]
        result = stats.cluster_rate(items)
        self.assertAlmostEqual(result["rate"], 0.75)
        # S_A = -0.5, S_B = 0.5；var = (2/1) * 0.5 / 16
        self.assertAlmostEqual(result["se"], 0.25)
        self.assertEqual(result["clusters"], 2)

    def test_cluster_rate_single_cluster_unidentified(self):
        self.assertIsNone(stats.cluster_rate([("A", 1.0, 1.0), ("A", 1.0, 0.0)]))

    def test_contrast(self):
        control = [("A", 1.0, 0.0), ("A", 1.0, 1.0), ("B", 1.0, 0.0), ("B", 1.0, 0.0)]
        treat = [("C", 1.0, 1.0), ("C", 1.0, 1.0), ("D", 1.0, 1.0), ("D", 1.0, 0.0)]
        result = stats.contrast(control, treat)
        self.assertAlmostEqual(result["est"], 0.5)
        self.assertEqual(result["df"], 2)


class LedgerTests(unittest.TestCase):
    def test_units_follow_shared_horn_not_shared_teacher(self):
        book = load_ledger()
        r1 = book.context("R1")
        self.assertEqual(sorted(r1.units), ["H1", "H2", "H3"])
        self.assertEqual(r1.units["H1"].villages, ("T1", "T2"))
        # 解释员 I1 跨村服务只登记为提示，不把 H1、H2 捆成一个单元
        self.assertEqual(r1.teacher_groups["I1"], ["T1", "T2"])
        r2 = book.context("R2")
        self.assertEqual(r2.units["H2"].villages, ("T2", "T3"))
        self.assertFalse(r2.units["H3"].available)
        r3 = book.context("R3")
        self.assertEqual(r3.units["H2"].villages, ("T2", "T3"))

    def test_dedupe_cross_visited(self):
        book = load_ledger()
        frame, duplicates = book.household_frame("R1")
        raw = book.interviews("R1")
        self.assertEqual(len(raw) - len({(r["block"], r["household"]) for r in raw}), len(duplicates))
        record = next(r for r in frame if r["block"] == "B01" and r["household"] == "T3-H3")
        self.assertTrue(record["cross_visited"])
        self.assertEqual(record["visiting_groups"], ["T2", "T3"])
        # 去重只留一条：全村每户每场次的核实记录唯一
        keys = [(r["block"], r["household"]) for r in frame if r["verified"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_completed_and_plannable_rounds(self):
        book = load_ledger()
        self.assertEqual(book.completed_rounds(), ["R1", "R2"])
        self.assertEqual(book.plannable_rounds(), ["R3"])


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = load_ledger()
        cls.r1 = analysis.round_analysis(cls.book, "R1")
        cls.r2 = analysis.round_analysis(cls.book, "R2")
        cls.evidence = analysis.build_evidence(cls.book, ["R1", "R2"])

    def test_r1_no_deviation_and_lenses_agree(self):
        self.assertEqual(self.r1["deviations"], [])
        for stratum in self.r1["strata"].values():
            assigned = stratum["lens"]["assigned"]["correct"]["contrast"]
            delivered = stratum["lens"]["delivered"]["correct"]["contrast"]
            self.assertAlmostEqual(assigned["est"], delivered["est"])

    def test_r2_repair_deviation_recorded(self):
        self.assertEqual(len(self.r2["deviations"]), 12)
        self.assertTrue(all(d["horn"] == "H3" for d in self.r2["deviations"]))
        self.assertEqual(self.r2["deviation_strata"], ["mandarin|day"])
        # 实播口径下该分层没有 short 臂，口令效果无法估计
        delivered = self.r2["strata"]["mandarin|day"]["lens"]["delivered"]
        self.assertEqual(delivered["blocks"]["short"], 0)
        self.assertIsNone(delivered["correct"]["contrast"])

    def test_deviated_round_excluded_from_evidence(self):
        self.assertIn(
            {"round": "R2", "stratum": "mandarin|day", "reason": "该轮该分层发生偏离，不并入后续统计"},
            self.evidence["excluded"],
        )
        # mandarin|day 的 P01 证据只剩 R1 的 6+6 个区组
        pooled = self.evidence["by_stratum"]["mandarin|day"]["versions"]["P01"]["lens"]["assigned"]
        self.assertEqual(pooled["blocks"], {"full": 6, "short": 6})

    def test_carryover_not_counted_as_improvement(self):
        funnel = self.r2["strata"]["jin|night"]["funnel"]
        self.assertEqual(
            funnel["primary"] + funnel["carryover_yes"] + funnel["previous_unknown"],
            funnel["verified"],
        )
        self.assertGreater(funnel["carryover_yes"], 0)
        # 主分析只含 previous_known=no：按名册独立复算
        frame, _ = self.book.household_frame("R2")
        expected = sum(
            1
            for r in frame
            if r["verified"] and r["previous_known"] == "no" and (r["language"], r["service"]) == ("jin", "night")
        )
        self.assertEqual(funnel["primary"], expected)

    def test_funnel_accounts_unvisited_and_cross_visited(self):
        funnel = self.r1["strata"]["jin|day"]["funnel"]
        self.assertEqual(funnel["rostered"] - funnel["verified"], funnel["unvisited"])
        self.assertGreater(funnel["cross_visited"], 0)

    def test_current_info_guarantee(self):
        self.assertTrue(self.r1["current_info"]["ok"])
        self.assertTrue(self.r2["current_info"]["ok"])  # full_only 仍保持现行渠道

    def test_rollout_scope_lists_where_not_to_roll_out(self):
        scope = analysis.rollout_scope(self.book, self.evidence, {"R1": self.r1, "R2": self.r2})
        self.assertTrue(scope["cannot_roll_out"])
        self.assertIn("mandarin|day", {item["stratum"] for item in scope["cannot_roll_out"]})
        self.assertIn("night", scope["key_populations"])
        self.assertIn("jin", scope["key_populations"])


class GateTests(unittest.TestCase):
    SAFEGUARD = {"min_gain": 0.04, "max_wrong": 0.10, "eligible_blocks": 4}

    def make_bundle(self, blocks=(3, 3), gain_low=0.05, wrong_high=0.08):
        return {
            "blocks": {"full": blocks[0], "short": blocks[1]},
            "correct": {"contrast": {"low": gain_low}},
            "wrong": {"short": {"high": wrong_high}},
        }

    def test_supported(self):
        gates = evaluate_gates(self.SAFEGUARD, self.make_bundle())
        self.assertEqual(gates["verdict"], "支持推广")

    def test_insufficient_blocks(self):
        gates = evaluate_gates(self.SAFEGUARD, self.make_bundle(blocks=(3, 1)))
        self.assertEqual(gates["verdict"], "证据不足")
        self.assertFalse(gates["blocks_ok"])

    def test_gain_shortfall_and_wrong_breach(self):
        gates = evaluate_gates(self.SAFEGUARD, self.make_bundle(gain_low=0.01, wrong_high=0.2))
        self.assertEqual(gates["verdict"], "未达标")
        self.assertFalse(gates["gain_ok"])
        self.assertFalse(gates["wrong_ok"])

    def test_unestimable_is_insufficient(self):
        bundle = {"blocks": {"full": 0, "short": 0}, "correct": {"contrast": None}, "wrong": {"short": None}}
        gates = evaluate_gates(self.SAFEGUARD, bundle)
        self.assertEqual(gates["verdict"], "证据不足")


def write_tables(directory, tables):
    for name, rows in tables.items():
        if not rows:
            continue  # 缺失的表按空表处理
        with (Path(directory) / (name + ".csv")).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


class FabricatedLedgerTests(unittest.TestCase):
    def build(self, playback_rows):
        tables = {
            "catchments": [
                {"round": "R1", "village": "T1", "horn": "H1", "language": "jin", "teacher": "I1", "service": "night", "available": "true"},
            ],
            "arrangements": [
                {"round": "R1", "opportunity": "O1", "variants": "full|short", "standard_required": "true", "max_visits": "10", "site_version": "P01"},
            ],
            "safeguards": [
                {"language": "jin", "service": "night", "min_gain": "0.05", "max_wrong": "0.1", "eligible_blocks": "2"},
            ],
            "assignments": [
                {"round": "R1", "block": "B01", "horn": "H1", "assigned": "short", "marginal_prob": "0.5", "opportunity": "O1"},
                {"round": "R1", "block": "B02", "horn": "H1", "assigned": "full", "marginal_prob": "0.5", "opportunity": "O1"},
            ],
            "roster": [
                {"round": "R1", "block": "B01", "household": "T1-H1", "village": "T1", "horn": "H1", "previous_known": "no"},
                {"round": "R1", "block": "B02", "household": "T1-H1", "village": "T1", "horn": "H1", "previous_known": "no"},
            ],
            "playback": playback_rows,
            "interviews": [
                {"round": "R1", "block": "B01", "household": "T1-H1", "visiting_group": "T1", "answer": "correct", "prob_contact": "0.8"},
                {"round": "R1", "block": "B02", "household": "T1-H1", "visiting_group": "T1", "answer": "wrong", "prob_contact": "0.8"},
            ],
        }
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        write_tables(directory.name, tables)
        return ledger.Ledger.load(directory.name)

    def test_missing_playback_breaks_current_info_guarantee(self):
        book = self.build([
            {"round": "R1", "block": "B02", "horn": "H1", "delivered": "full", "site_version": "P01"},
        ])
        result = analysis.round_analysis(book, "R1")
        self.assertFalse(result["current_info"]["ok"])
        self.assertEqual(result["current_info"]["breaches"][0]["village"], "T1")

    def test_full_only_is_not_an_arm(self):
        book = self.build([
            {"round": "R1", "block": "B01", "horn": "H1", "delivered": "full_only", "site_version": "P01"},
            {"round": "R1", "block": "B02", "horn": "H1", "delivered": "full", "site_version": "P01"},
        ])
        result = analysis.round_analysis(book, "R1")
        delivered = result["strata"]["jin|night"]["lens"]["delivered"]
        self.assertEqual(delivered["blocks"]["short"], 0)
        self.assertEqual(delivered["blocks"]["full"], 1)
        self.assertTrue(result["current_info"]["ok"])  # 现行渠道仍可达


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = load_ledger()
        cls.evidence = analysis.build_evidence(cls.book, ["R1", "R2"])
        cls.plan = plan.plan_round(cls.book, cls.evidence, "R3", n_blocks=12)

    def test_deterministic(self):
        again = plan.plan_round(self.book, self.evidence, "R3", n_blocks=12)
        self.assertEqual(self.plan, again)

    def test_assignments_within_allowed_variants(self):
        ctx = self.book.context("R3")
        for row in self.plan["assignments"]:
            allowed = ctx.opportunities[row["opportunity"]]["variants"]
            self.assertIn(row["assigned"], allowed)
            self.assertEqual(row["round"], "R3")

    def test_each_unit_balanced_within_version(self):
        from collections import Counter

        ctx = self.book.context("R3")
        counts = Counter()
        for row in self.plan["assignments"]:
            version = ctx.opportunities[row["opportunity"]]["site_version"]
            counts[(row["horn"], version, row["assigned"])] += 1
        for horn in ("H1", "H2", "H3"):
            for version in ("P01", "P02"):
                self.assertGreaterEqual(counts[(horn, version, "full")], 2, (horn, version))
                self.assertGreaterEqual(counts[(horn, version, "short")], 2, (horn, version))

    def test_visits_within_capacity_and_roster(self):
        from collections import Counter

        ctx = self.book.context("R3")
        capacity = {b["block"]: b["max_visits"] for b in self.plan["blocks"]}
        totals = Counter()
        for row in self.plan["visits"]:
            totals[row["block"]] += row["target_visits"]
            self.assertLessEqual(row["target_visits"], row["roster_size"])
        for block, total in totals.items():
            self.assertLessEqual(total, capacity[block])

    def test_shared_horn_villages_move_together(self):
        # T2、T3 在 R3 共用 H2：两村只能拿到同一分配
        by_block = {}
        for row in self.plan["assignments"]:
            if row["horn"] == "H2":
                by_block[row["block"]] = row["assigned"]
        self.assertEqual(len(by_block), self.plan["n_blocks"])

    def test_plan_registers_opportunities_and_ranges(self):
        self.assertTrue(self.plan["blocks"])
        self.assertTrue(all(b["opportunity"] for b in self.plan["blocks"]))
        self.assertTrue(self.plan["units"])
        self.assertTrue(any("现行信息" in note for note in self.plan["notes"]))

    def test_unavailable_unit_not_assigned(self):
        tables = {
            "catchments": [
                {"round": "R9", "village": "T1", "horn": "H1", "language": "jin", "teacher": "I1", "service": "night", "available": "true"},
                {"round": "R9", "village": "T2", "horn": "H2", "language": "jin", "teacher": "I2", "service": "day", "available": "false"},
            ],
            "arrangements": [
                {"round": "R9", "opportunity": "O1", "variants": "full|short", "standard_required": "true", "max_visits": "10", "site_version": "P01"},
            ],
            "safeguards": [
                {"language": "jin", "service": "night", "min_gain": "0.05", "max_wrong": "0.1", "eligible_blocks": "2"},
                {"language": "jin", "service": "day", "min_gain": "0.05", "max_wrong": "0.1", "eligible_blocks": "2"},
            ],
            "assignments": [], "roster": [], "playback": [], "interviews": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            write_tables(directory, tables)
            book = ledger.Ledger.load(directory)
            result = plan.plan_round(book, {"by_stratum": {}}, "R9", n_blocks=4)
        horns = {row["horn"] for row in result["assignments"]}
        self.assertNotIn("H2", horns)
        self.assertTrue(any("不可用" in note for note in result["notes"]))
        self.assertFalse(result["feasible"])


if __name__ == "__main__":
    unittest.main()
