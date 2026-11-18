import copy
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "deploy/scripts"))
    return importlib.import_module("check_coverage")


def complete():
    totals = {
        "num_statements": 10000,
        "num_branches": 10000,
        "missing_lines": 0,
        "missing_branches": 0,
    }
    return {
        "meta": {"branch_coverage": True},
        "totals": totals,
        "files": {"services/example.py": {"summary": copy.copy(totals)}},
    }


def test_zero_missing_is_required(gate):
    gate.check(complete())


@pytest.mark.parametrize("field", ["missing_lines", "missing_branches"])
@pytest.mark.parametrize("location", ["file", "totals"])
def test_even_one_missing_arc_or_line_fails_despite_rounding_to_100(gate, field, location):
    report = complete()
    target = (
        report["totals"]
        if location == "totals"
        else report["files"]["services/example.py"]["summary"]
    )
    target[field] = 1
    with pytest.raises(ValueError, match="Incomplete"):
        gate.check(report)


@pytest.mark.parametrize("change", ["no-branches", "no-files", "no-statements", "no-arcs"])
def test_uninstrumented_or_empty_report_fails_closed(gate, change):
    report = complete()
    if change == "no-branches":
        report["meta"]["branch_coverage"] = False
    elif change == "no-files":
        report["files"] = {}
    elif change == "no-statements":
        report["totals"]["num_statements"] = 0
    else:
        report["totals"]["num_branches"] = 0
    with pytest.raises(ValueError):
        gate.check(report)


def test_excluding_executable_lines_cannot_make_the_gate_green(gate):
    report = complete()
    report["totals"]["excluded_lines"] = 1
    with pytest.raises(ValueError, match="Excluded"):
        gate.check(report)
