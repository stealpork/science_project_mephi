import argparse
import json
from datetime import UTC, datetime

from ops import APPS, ROOT, Ops


def summarize(data, minimum_seconds, peak_vus, p95=750, p99=1500):
    metrics = data["metrics"]
    requests = metrics["http_reqs"]
    duration = requests["count"] / requests["rate"] if requests["rate"] > 0 else 0
    latency = metrics["http_req_duration"]
    p99_value = latency.get("p(99)")

    p99_bound = p99_value if p99_value is not None else latency.get("max", float("inf"))
    if not (
        requests["count"] > 0
        and duration >= minimum_seconds - 1
        and metrics["vus"]["max"] >= peak_vus
        and metrics["http_req_failed"]["value"] < 0.01
        and metrics["checks"]["value"] > 0.99
        and latency["p(95)"] < p95
        and p99_bound < p99
        and metrics.get("unexpected_rate_limits", {}).get("count", 0) == 0
    ):
        raise ValueError(
            "Load gates failed, requested concurrency was not reached, or run was aborted"
        )
    return {
        "requests": requests["count"],
        "errors": round(requests["count"] * metrics["http_req_failed"]["value"]),
        "duration_seconds": round(duration, 2),
        "peak_vus": metrics["vus"]["max"],
        "p95_ms": round(latency["p(95)"], 2),
        "p99_ms": round(p99_value, 2) if p99_value is not None else None,
        "p99_upper_bound_ms": round(p99_bound, 2) if p99_value is None else None,
    }


def read_fresh(path, after):
    if path.stat().st_mtime <= after:
        raise ValueError("Stale load summary predates the current acceptance manifest")
    return json.loads(path.read_text())


def validate_writes(before, after, completed):
    if completed <= 0 or after - before != completed:
        raise ValueError("New project rows must equal completed fresh workflows; do not reset data")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-writes", action="store_true")
    args = parser.parse_args()
    directory = ROOT / "test-results"
    manifest = json.loads((directory / "acceptance-manifest.json").read_text())
    started = datetime.fromisoformat(manifest["at"]).timestamp()
    report = {"manifest": manifest, "success": True, "verified_at": datetime.now(UTC).isoformat()}
    for profile, duration in [("stress", 840), ("soak", 1800)]:
        clients = [
            summarize(
                read_fresh(directory / f"distributed-{profile}-{number}.json", started),
                duration,
                20,
            )
            for number in range(1, 6)
        ]
        report[profile] = {
            "clients": clients,
            "requests": sum(client["requests"] for client in clients),
            "errors": sum(client["errors"] for client in clients),
            "client_p95_range_ms": [
                min(c["p95_ms"] for c in clients),
                max(c["p95_ms"] for c in clients),
            ],
        }
    ops = Ops(ROOT / ".env.test")
    if ops.project != "science-test" or ops.config.get("SC_ENVIRONMENT") != "test":
        raise ValueError("Acceptance verification is restricted to science-test")
    for service in APPS:
        if ops.container(service)["Image"] != manifest["images"][service]:
            raise ValueError("Runtime image changed during acceptance")
    if args.verify_writes:
        data = read_fresh(directory / "load-workflow.json", started)
        workflow = summarize(data, 120, 5, p95=1000, p99=2000)
        completed = int(data["metrics"]["completed_workflows"]["count"])
        actual = int(ops.sql("projects", "SELECT count(*) FROM projects"))
        validate_writes(manifest["dataset"]["projects_total"], actual, completed)
        workflow.update(
            completed_workflows=completed,
            new_project_rows=actual - manifest["dataset"]["projects_total"],
        )
        report["workflow"] = workflow
    (directory / "load-acceptance.json").write_text(json.dumps(report, indent=2))
    print("Full-duration load gates, fresh summaries and unchanged runtime images verified")
    if args.verify_writes:
        print("Actual new project rows match successful workflows; no data was reset")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError, ZeroDivisionError) as exc:
        raise SystemExit(f"Load acceptance not complete: {exc}") from None
