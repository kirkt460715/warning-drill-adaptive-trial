"""检查正常台账以及损坏的基础引用。"""

import csv
import json
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
import unittest

from input_audit import ROOT, audit


class InputAuditTests(unittest.TestCase):
    def test_supplied_registers(self):
        counts, issues = audit(ROOT / "data")
        self.assertFalse(issues, issues)
        self.assertTrue(counts and all(counts.values()))

    def test_broken_reference(self):
        contract = json.loads((ROOT / "input_contract.json").read_text(encoding="utf-8"))
        child, key, parent, _ = contract["references"][0]
        with TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            copytree(ROOT / "data", data)
            file = data / (child + ".csv")
            with file.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            rows[0][key] = "不存在的原始编号"
            with file.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=contract["tables"][child])
                writer.writeheader()
                writer.writerows(rows)
            _, issues = audit(data)
            self.assertTrue(any(parent in issue for issue in issues), issues)

    def test_bad_column(self):
        contract = json.loads((ROOT / "input_contract.json").read_text(encoding="utf-8"))
        table = next(iter(contract["tables"]))
        with TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            copytree(ROOT / "data", data)
            (data / (table + ".csv")).write_text("异常表头\n", encoding="utf-8")
            _, issues = audit(data)
            self.assertTrue(any("表头" in issue for issue in issues), issues)
