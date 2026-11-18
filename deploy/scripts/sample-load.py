import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from ops import APPS, Ops

ROOT = Path(__file__).resolve().parents[2]


def output(*arguments):
    return subprocess.check_output(["docker", *arguments], text=True, timeout=20)


def main():
    ops = Ops(ROOT / ".env.test")
    if ops.project != "science-test" or ops.config.get("SC_ENVIRONMENT") != "test":
        raise SystemExit("Resource sampling is restricted to the isolated test project")
    destination = ROOT / "test-results/soak-resources.jsonl"
    destination.parent.mkdir(exist_ok=True)
    host = output("info", "--format", "{{.NCPU}} {{.MemTotal}}").split()
    manifest = {
        "at": datetime.now(UTC).isoformat(),
        "host": {"cpus": int(host[0]), "memory_bytes": int(host[1])},
        "project": ops.project,
        "images": {name: ops.container(name)["Image"] for name in APPS},
        "dataset": {
            "users_total": int(ops.sql("identity", "SELECT count(*) FROM users")),
            "users_active": int(
                ops.sql("identity", "SELECT count(*) FROM users WHERE status='active'")
            ),
            "projects_total": int(ops.sql("projects", "SELECT count(*) FROM projects")),
            "papers_total": int(ops.sql("publications", "SELECT count(*) FROM research_papers")),
        },
        "profiles": ["distributed-stress", "distributed-soak"],
        "coverage_instrumentation": False,
        "shared_host": True,
    }
    (ROOT / "test-results/acceptance-manifest.json").write_text(json.dumps(manifest, indent=2))
    started = time.monotonic()
    last_generator = started
    with destination.open("w") as report:
        while time.monotonic() - started < 4000:
            generators = output(
                "ps",
                "--filter",
                "network=science-test_application",
                "--filter",
                "ancestor=grafana/k6:1.0.0",
                "--format",
                "{{.ID}}",
            ).split()
            if not generators:
                if time.monotonic() - last_generator > 30:
                    break
                time.sleep(1)
                continue
            last_generator = time.monotonic()
            identifiers = output(
                "ps",
                "--filter",
                "label=com.docker.compose.project=science-test",
                "--format",
                "{{.ID}}",
            ).split()
            if identifiers:
                stats = output("stats", "--no-stream", "--format", "{{json .}}", *identifiers)
                data = {
                    "at": datetime.now(UTC).isoformat(),
                    "generators": len(generators),
                    "containers": [json.loads(line) for line in stats.splitlines()],
                }
                report.write(json.dumps(data) + "\n")
                report.flush()
            time.sleep(15)
    print("Isolated load resource sampling finished")


if __name__ == "__main__":
    main()
