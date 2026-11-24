"""检查演练台账的交付格式。"""

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def audit(location: Path) -> tuple[dict[str, int], list[str]]:
    specification = json.loads((ROOT / "input_contract.json").read_text(encoding="utf-8"))
    datasets: dict[str, list[dict[str, str]]] = {}
    issues: list[str] = []
    for table, fields in specification["tables"].items():
        file = location / (table + ".csv")
        try:
            with file.open(encoding="utf-8", newline="") as stream:
                records = csv.DictReader(stream, strict=True)
                if records.fieldnames != fields:
                    issues.append(f"{file.name} 的表头与交付格式不符")
                    continue
                datasets[table] = []
                for line, record in enumerate(records, start=2):
                    if None in record or any(value is None for value in record.values()):
                        issues.append(f"{file.name} 第 {line} 行的字段数不符")
                    else:
                        datasets[table].append(record)
        except (OSError, UnicodeError, csv.Error) as error:
            issues.append(f"{file.name} 无法读取：{error}")
    for child, key, parent, target in specification["references"]:
        if child not in datasets or parent not in datasets:
            continue
        identifiers = {record[target] for record in datasets[parent]}
        for line, record in enumerate(datasets[child], start=2):
            if record[key] and record[key] not in identifiers:
                issues.append(f"{child}.csv 第 {line} 行的 {key} 缺少 {parent} 中的对应记录")
    return {name: len(records) for name, records in datasets.items()}, issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("location", nargs="?", type=Path, default=ROOT / "data", help="台账目录")
    counts, issues = audit(parser.parse_args().location)
    print(json.dumps({"源表行数": counts, "格式问题": issues}, ensure_ascii=False))
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
