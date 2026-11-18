import argparse
import json
from pathlib import Path


def check(report):
    if report.get("meta", {}).get("branch_coverage") is not True:
        raise ValueError("Branch instrumentation is required")
    files = report.get("files", {})
    if not files:
        raise ValueError("No measured source files")
    totals = report["totals"]
    if totals["num_statements"] <= 0 or totals["num_branches"] <= 0:
        raise ValueError("Empty statement or branch coverage")
    if totals.get("excluded_lines", 0):
        raise ValueError("Excluded executable lines are not allowed")
    gaps = []
    for name, data in files.items():
        summary = data["summary"]
        if summary["missing_lines"] or summary["missing_branches"]:
            gaps.append(
                f"{name}: {summary['missing_lines']} missing lines, "
                f"{summary['missing_branches']} missing branches"
            )
    if gaps or totals["missing_lines"] or totals["missing_branches"]:
        raise ValueError("Incomplete coverage:\n" + "\n".join(gaps))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        check(json.loads(args.report.read_text()))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise SystemExit(str(exc)) from None
    print("Coverage gate passed: 100% statements and 100% branches (zero missing).")


if __name__ == "__main__":
    main()
