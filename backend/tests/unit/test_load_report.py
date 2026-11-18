import importlib
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def reporting(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "deploy/scripts"))
    return importlib.import_module("load_report")


def summary():
    return {
        "metrics": {
            "http_reqs": {"count": 8400, "rate": 10},
            "http_req_duration": {"p(95)": 100, "p(99)": 200},
            "http_req_failed": {"value": 0},
            "checks": {"value": 1},
            "vus": {"max": 20},
            "unexpected_rate_limits": {"count": 0},
        }
    }


def test_completed_profile_is_reported_without_averaging_percentiles(reporting):
    result = reporting.summarize(summary(), 840, 20)
    assert result == {
        "requests": 8400,
        "errors": 0,
        "duration_seconds": 840,
        "peak_vus": 20,
        "p95_ms": 100,
        "p99_ms": 200,
        "p99_upper_bound_ms": None,
    }


@pytest.mark.parametrize(
    "metric,value",
    [
        ("http_reqs", {"count": 75, "rate": 1}),
        ("http_reqs", {"count": 0, "rate": 0}),
        ("vus", {"max": 2}),
        ("http_req_failed", {"value": 0.02}),
        ("http_req_duration", {"p(95)": 750, "p(99)": 200}),
        ("unexpected_rate_limits", {"count": 1}),
    ],
)
def test_aborted_or_failed_profile_is_not_accepted_even_with_green_k6_thresholds(
    reporting, metric, value
):
    data = summary()
    data["metrics"][metric] = value
    with pytest.raises(ValueError, match="gates"):
        reporting.summarize(data, 840, 20)


@pytest.mark.parametrize("after,completed", [(100, 0), (100, 10), (111, 10)])
def test_cached_or_partial_write_workflow_cannot_pass(reporting, after, completed):
    with pytest.raises(ValueError, match="New project rows"):
        reporting.validate_writes(100, after, completed)


def test_real_new_rows_match_completed_workflows(reporting):
    reporting.validate_writes(100, 110, 10)


def test_old_phase_results_cannot_be_mixed_with_new_manifest(reporting, tmp_path):
    target = tmp_path / "summary.json"
    target.write_text(json.dumps(summary()))
    with pytest.raises(ValueError, match="Stale"):
        reporting.read_fresh(target, target.stat().st_mtime + 1)
    assert reporting.read_fresh(target, target.stat().st_mtime - 1) == summary()


def test_legacy_export_uses_a_maximum_bound_without_fabricating_p99(reporting):
    data = summary()
    latency = data["metrics"]["http_req_duration"]
    latency.pop("p(99)")
    latency["max"] = 500
    result = reporting.summarize(data, 840, 20)
    assert result["p99_ms"] is None and result["p99_upper_bound_ms"] == 500


@pytest.mark.parametrize("maximum", [1500, None])
def test_legacy_export_is_rejected_if_its_bound_cannot_prove_the_gate(reporting, maximum):
    data = summary()
    latency = data["metrics"]["http_req_duration"]
    latency.pop("p(99)")
    if maximum is not None:
        latency["max"] = maximum
    with pytest.raises(ValueError, match="gates"):
        reporting.summarize(data, 840, 20)
