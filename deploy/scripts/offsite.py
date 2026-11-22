import argparse
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from ops import RESTIC, ROOT, Ops


def remote_settings(ops, path):
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise SystemExit("Offsite credentials must be an existing private file (mode 0600)")
    values = dotenv_values(path)
    required = [
        "OFFSITE_REPOSITORY",
        "OFFSITE_PASSWORD",
        "OFFSITE_AWS_ACCESS_KEY_ID",
        "OFFSITE_AWS_SECRET_ACCESS_KEY",
    ]
    if any(not values.get(key) for key in required) or len(values["OFFSITE_PASSWORD"]) < 24:
        raise SystemExit(
            "Set offsite repository, separate S3 credentials and a strong repository password"
        )
    repository = values["OFFSITE_REPOSITORY"]
    if not repository.startswith("s3:"):
        raise SystemExit("This adapter supports explicit S3 repositories only")
    endpoint = urlsplit(repository[3:])
    rehearsal = values.get("OFFSITE_REHEARSAL", "false").lower() == "true"
    if (
        endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or not endpoint.hostname
        or not endpoint.path.strip("/")
    ):
        raise SystemExit("Do not embed credentials/query tokens in repository URLs")
    if rehearsal and ops.config.get("SC_ENVIRONMENT") == "production":
        raise SystemExit("Production offsite cannot use a local rehearsal")
    if endpoint.scheme != "https" and not (
        rehearsal
        and endpoint.scheme == "http"
        and endpoint.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise SystemExit(
            "Offsite requires HTTPS; plaintext loopback is allowed only for an explicit rehearsal"
        )
    application = urlsplit(
        ops.config.get("S3_PUBLIC_ENDPOINT")
        or f"http://localhost:{ops.config.get('S3_PORT', '19000')}"
    )

    def endpoint_identity(url):
        host = "loopback" if url.hostname in {"localhost", "127.0.0.1", "::1"} else url.hostname
        return host, url.port or (443 if url.scheme == "https" else 80)

    if endpoint_identity(endpoint) == endpoint_identity(application):
        raise SystemExit(
            "Backup destination cannot be the application's S3 instance (recursive volume backup)"
        )
    if values["OFFSITE_AWS_ACCESS_KEY_ID"] == ops.config.get("S3_ACCESS_KEY_ID", "science-app"):
        raise SystemExit("Use separate offsite credentials, never the application's S3 account")
    return values, rehearsal


def execute(ops, values, command, *, source=None, destination=None, fetch=False):
    environment = os.environ.copy()
    environment.update(
        AWS_ACCESS_KEY_ID=values["OFFSITE_AWS_ACCESS_KEY_ID"],
        AWS_SECRET_ACCESS_KEY=values["OFFSITE_AWS_SECRET_ACCESS_KEY"],
        AWS_DEFAULT_REGION=values.get("OFFSITE_AWS_REGION", "us-east-1"),
        RESTIC_REPOSITORY="/recovered" if fetch else values["OFFSITE_REPOSITORY"],
        RESTIC_PASSWORD=ops.config["BACKUP_PASSPHRASE"] if fetch else values["OFFSITE_PASSWORD"],
        RESTIC_FROM_PASSWORD=values["OFFSITE_PASSWORD"]
        if fetch
        else ops.config["BACKUP_PASSPHRASE"],
    )
    arguments = [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]
    for name in [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
        "RESTIC_REPOSITORY",
        "RESTIC_PASSWORD",
        "RESTIC_FROM_PASSWORD",
    ]:
        arguments += ["-e", name]
    if source:
        arguments += ["-v", f"{source}:/source"]
    if destination:
        arguments += ["-v", f"{destination}:/recovered"]
    result = subprocess.run(
        [*arguments, RESTIC, "--no-cache", *command], env=environment, capture_output=True
    )
    if result.returncode:
        raise SystemExit(
            f"Restic {command[0]} failed with exit code {result.returncode}; no credentials were printed"
        )
    return result.stdout


def copy_telemetry(directory, *, success, rehearsal):
    metrics = directory / "metrics"
    metrics.mkdir(parents=True, exist_ok=True)
    target = metrics / "offsite.json"
    state = json.loads(target.read_text()) if target.exists() else {}
    state.update(success=success, rehearsal=rehearsal, attempted_at=time.time())
    if success:
        state["verified_at"] = state["attempted_at"]
    temporary = metrics / "offsite.json.tmp"
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(target)
    values = {
        "configured": 1,
        "rehearsal": int(rehearsal),
        "last_attempt_success": int(success),
        "last_success_timestamp": state.get("verified_at", 0),
    }
    temporary = metrics / "offsite.prom.tmp"
    temporary.write_text(
        "".join(f"science_offsite_{key} {value}\n" for key, value in values.items())
    )
    temporary.replace(metrics / "offsite.prom")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "copy", "check", "fetch"])
    parser.add_argument("--env-file", default=ROOT / ".env")
    parser.add_argument("--target-env", required=True)
    parser.add_argument("--directory", type=Path, default=ROOT / "backups")
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    ops = Ops(args.env_file)
    values, rehearsal = remote_settings(ops, args.target_env)
    directory = args.directory.resolve()
    if directory.is_relative_to(ROOT) and not directory.is_relative_to(ROOT / "backups"):
        raise SystemExit("Keep operator state under backups/ or outside the checkout")
    if args.action in {"init", "copy"} and not (directory / "repository/config").is_file():
        raise SystemExit("An existing local encrypted backup repository is required")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "operations.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "init":
            execute(
                ops,
                values,
                ["init", "--from-repo", "/source", "--copy-chunker-params"],
                source=directory / "repository",
            )
        elif args.action == "copy":
            try:
                execute(
                    ops, values, ["copy", "--from-repo", "/source"], source=directory / "repository"
                )
                execute(ops, values, ["check", "--read-data"])
            except BaseException:
                copy_telemetry(directory, success=False, rehearsal=rehearsal)
                raise
            copy_telemetry(directory, success=True, rehearsal=rehearsal)
        elif args.action == "fetch":
            if not args.destination:
                raise SystemExit(
                    "Set a NEW --destination directory; working backups are never overwritten"
                )
            destination = args.destination.resolve()
            if (
                destination.exists()
                or destination.is_relative_to(directory / "repository")
                or (
                    destination.is_relative_to(ROOT)
                    and not destination.is_relative_to(ROOT / "backups")
                )
            ):
                raise SystemExit("Recovery destination must be new and outside source folders")
            destination.mkdir(parents=True, mode=0o700)
            execute(
                ops,
                values,
                ["init", "--from-repo", values["OFFSITE_REPOSITORY"], "--copy-chunker-params"],
                destination=destination,
                fetch=True,
            )
            execute(
                ops,
                values,
                ["copy", "--from-repo", values["OFFSITE_REPOSITORY"]],
                destination=destination,
                fetch=True,
            )
            execute(ops, values, ["check", "--read-data"], destination=destination, fetch=True)
        else:
            execute(ops, values, ["check", "--read-data"])
    print(f"Encrypted S3 {args.action} verified; rehearsal={rehearsal}. No snapshots were deleted.")


if __name__ == "__main__":
    main()
