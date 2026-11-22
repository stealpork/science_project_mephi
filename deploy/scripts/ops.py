import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("identity", "people", "projects", "publications", "media", "activity")
APPS = [*SERVICES, *(f"{name}-worker" for name in SERVICES), "nginx"]
STATEFUL = [
    "minio",
    "rabbitmq",
    "redis",
    "mailpit",
    "prometheus",
    "grafana",
    "alertmanager",
]
TABLES = {
    "identity": "users",
    "people": "profiles",
    "projects": "projects",
    "publications": "research_papers",
    "media": "files",
    "activity": "favorites",
}
RESTIC = (
    "restic/restic:0.19.0@sha256:7f44e0057b82348597568ea209360762d0b38f8e1dbc8ad859661ac1055e45f2"
)


def quote_env(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def call(args, *, output=None, data=None, capture=False, env=None, check=True):
    result = subprocess.run(
        args,
        cwd=ROOT,
        input=data,
        stdout=output if output else subprocess.PIPE if capture else None,
        check=check,
        env=env,
    )
    return result.stdout if capture else result.returncode


class Ops:
    def __init__(self, envfile, project=None, extra=None, root=ROOT):
        self.envfile = Path(envfile).resolve()
        if not self.envfile.is_file():
            raise SystemExit("Configuration file does not exist; refusing a fallback project")
        self.root = root
        self.config = dotenv_values(self.envfile)
        self.project = project or self.config.get("COMPOSE_PROJECT_NAME", "science")
        if not re.fullmatch(r"science(?:-[a-z0-9-]+)?", self.project or ""):
            raise SystemExit("Refusing an unrelated Compose project")
        self.base = [
            "docker",
            "compose",
            "--env-file",
            str(self.envfile),
            "-p",
            self.project,
            "-f",
            str(self.root / "deploy/compose.yaml"),
        ]
        for overlay in extra if isinstance(extra, (list, tuple)) else [extra] if extra else []:
            self.base += ["-f", str(overlay)]

    def compose(self, *args, **kwargs):
        return call([*self.base, *args], **kwargs)

    def rows(self):
        output = self.compose("ps", "--all", "--format", "json", capture=True).decode()
        return [json.loads(line) for line in output.splitlines() if line.strip()]

    def container(self, service):
        identifiers = self.compose("ps", "--all", "--quiet", service, capture=True).decode().split()
        if len(identifiers) != 1:
            raise SystemExit(f"Expected exactly one container: {service}")
        return json.loads(call(["docker", "inspect", identifiers[0]], capture=True))[0]

    def volume(self, service):
        container = self.container(service)
        volumes = [mount["Name"] for mount in container["Mounts"] if mount["Type"] == "volume"]
        if len(volumes) != 1 or not volumes[0].startswith(self.project + "_"):
            raise SystemExit(f"Unrecognized volume for {service}")
        return volumes[0]

    def sql(self, service, sql):
        return (
            self.compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-At",
                "-U",
                "science",
                "-d",
                service,
                "-c",
                sql,
                capture=True,
            )
            .decode()
            .strip()
        )

    def pin_images(self, names=None):
        for service in names or [*APPS, "minio"]:
            identifier = self.container(service)["Image"]
            result = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", identifier],
                capture_output=True,
            )
            if result.returncode:
                raise SystemExit(
                    f"Runtime image no longer registered for {service}; recreate this service from a known release before taking a backup"
                )
            call(
                [
                    "docker",
                    "image",
                    "tag",
                    identifier,
                    f"science-runtime-{self.project}-{service}:active",
                ]
            )

    def restic(self, repo, *args, stage=None, capture=False):
        environment = os.environ.copy()
        environment["RESTIC_PASSWORD"] = self.config["BACKUP_PASSPHRASE"]
        cmd = [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "RESTIC_PASSWORD",
            "-v",
            f"{repo}:/repository",
        ]
        if stage:
            cmd += ["-v", f"{stage}:/snapshot"]
        return call(
            [*cmd, RESTIC, "--no-cache", "-r", "/repository", *args],
            env=environment,
            capture=capture,
        )


def telemetry(directory, *, success=None, duration=None, failure=False, restore=False):
    metrics = directory / "metrics"
    metrics.mkdir(parents=True, exist_ok=True)
    state_path = metrics / "status.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if restore:
        state["last_restore_check_timestamp"] = success
        state["last_restore_duration_seconds"] = duration
    elif failure:
        state["last_attempt_success"] = 0
    else:
        state.update(
            last_success_timestamp=success,
            last_duration_seconds=duration,
            last_attempt_success=1,
        )
    state_path.write_text(json.dumps(state, indent=2))
    text = "".join(f"science_backup_{name} {value}\n" for name, value in state.items())
    temporary = metrics / "backup.prom.tmp"
    temporary.write_text(text)
    temporary.replace(metrics / "backup.prom")


def backup(ops, directory):
    if directory.is_relative_to(ROOT) and not directory.is_relative_to(ROOT / "backups"):
        raise SystemExit(
            "Keep backups under backups/ or outside the source checkout to avoid recursive archives"
        )
    external = [
        f"{name.upper()}_DATABASE_URL"
        for name in SERVICES
        if ops.config.get(f"{name.upper()}_DATABASE_URL")
    ]
    external += [name for name in ["RABBITMQ_URL", "REDIS_URL"] if ops.config.get(name)]
    if ops.config.get("S3_INTERNAL_ENDPOINT", "http://minio:9000") != "http://minio:9000":
        external.append("S3_INTERNAL_ENDPOINT")
    if external:
        raise SystemExit(
            "This full-stack backup supports local Compose storage only; configure native backup for external providers instead of copying unused local volumes"
        )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    repo = directory / "repository"
    repo.mkdir(exist_ok=True, mode=0o700)
    with (directory / "operations.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ops.pin_images()
        if not (repo / "config").exists():
            ops.restic(repo, "init")
        started = time.monotonic()
        rows = ops.rows()
        present = {row["Service"] for row in rows}
        running = {row["Service"] for row in rows if row["State"] == "running"}
        if (
            not {
                *APPS,
                "postgres",
                "minio",
                "rabbitmq",
                "redis",
                *([] if ops.config.get("SC_ENVIRONMENT") == "production" else ["mailpit"]),
            }
            <= present
            or "postgres" not in running
        ):
            raise SystemExit(
                "A full local stack with running PostgreSQL is required; refusing an incomplete snapshot"
            )
        paused = [name for name in [*APPS, *STATEFUL] if name in running]
        volumes_to_save = [name for name in STATEFUL if name in present]
        stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=directory))
        try:
            print(
                "Quiescing this project's writers for a consistent full-stack snapshot",
                flush=True,
            )
            ops.compose("stop", "-t", "30", *[name for name in APPS if name in running])
            ops.compose("stop", "-t", "30", *[name for name in STATEFUL if name in running])
            (stage / "config").mkdir(mode=0o700)
            shutil.copy2(ops.envfile, stage / "config/runtime.env")
            secrets = Path(ops.config["SECRET_DIRECTORY"])
            if not secrets.is_absolute():
                secrets = ROOT / "deploy" / secrets
            shutil.copytree(secrets.resolve(), stage / "config/secrets")
            metadata = {
                "format": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "project": ops.project,
                "counts": {},
                "images": {},
                "rabbitmq_hostname": ops.container("rabbitmq")["Config"]["Hostname"],
                "volumes": volumes_to_save,
            }
            for service in SERVICES:
                with (stage / f"{service}.dump").open("wb") as destination:
                    ops.compose(
                        "exec",
                        "-T",
                        "postgres",
                        "pg_dump",
                        "-U",
                        service,
                        "-d",
                        service,
                        "-Fc",
                        output=destination,
                    )
                tables = ops.sql(
                    service,
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename",
                ).splitlines()
                metadata["counts"][service] = {}
                for table in tables:
                    if not re.fullmatch(r"[a-z_]+", table):
                        raise SystemExit("Unexpected table identifier")
                    metadata["counts"][service][table] = int(
                        ops.sql(service, f'SELECT count(*) FROM "{table}"')
                    )
            for service in [*APPS, "minio"]:
                metadata["images"][service] = ops.container(service)["Image"]

            built_images = sorted(set(metadata["images"].values()))
            with (stage / "application-images.tar").open("wb") as destination:
                call(["docker", "save", *built_images], output=destination)
            with (stage / "roles.sql").open("wb") as destination:
                ops.compose(
                    "exec",
                    "-T",
                    "postgres",
                    "pg_dumpall",
                    "-U",
                    "science",
                    "--globals-only",
                    output=destination,
                )
            for service in volumes_to_save:
                with (stage / f"{service}.tar").open("wb") as destination:
                    call(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--entrypoint",
                            "tar",
                            "-v",
                            f"{ops.volume(service)}:/data:ro",
                            "postgres:17-alpine",
                            "-C",
                            "/data",
                            "-cpf",
                            "-",
                            ".",
                        ],
                        output=destination,
                    )
            with (stage / "source.tar.gz").open("wb") as destination:
                call(
                    [
                        "tar",
                        "--exclude=.git",
                        "--exclude=.env*",
                        "--exclude=.venv",
                        "--exclude=__pycache__",
                        "--exclude=.secrets",
                        "--exclude=backups",
                        "--exclude=artifacts",
                        "--exclude=test-results",
                        "--exclude=.mypy_cache",
                        "--exclude=.ruff_cache",
                        "--exclude=.pytest_cache",
                        "--exclude=.coverage*",
                        "--exclude=node_modules",
                        "--exclude=frontend/coverage",
                        "--exclude=frontend/dist",
                        "--exclude=*.tsbuildinfo",
                        "-czf",
                        "-",
                        "backend",
                        "deploy",
                        "docs",
                        "frontend",
                        "contracts",
                        ".github",
                        ".gitignore",
                        ".dockerignore",
                        "README.md",
                    ],
                    output=destination,
                )
            metadata["sha256"] = {}
            for path in stage.rglob("*"):
                if path.is_file():
                    with path.open("rb") as source:
                        metadata["sha256"][str(path.relative_to(stage))] = hashlib.file_digest(
                            source, "sha256"
                        ).hexdigest()
            (stage / "manifest.json").write_text(json.dumps(metadata, indent=2))
            ops.restic(
                repo,
                "backup",
                "--host",
                ops.project,
                "--tag",
                "full-stack",
                "/snapshot",
                stage=stage,
            )
            ops.restic(repo, "check")
            telemetry(directory, success=time.time(), duration=time.monotonic() - started)
            print(
                "Encrypted full-stack snapshot saved and repository metadata checked",
                flush=True,
            )
        except BaseException:
            telemetry(directory, failure=True)
            raise
        finally:
            try:
                if paused:
                    ops.compose("start", "--wait", "--wait-timeout", "180", *paused)
            except BaseException:
                telemetry(directory, failure=True)
                raise
            finally:
                shutil.rmtree(stage)


def restore(ops, directory, target, snapshot):
    if not re.fullmatch(r"science-restore-[a-z0-9-]+", target):
        raise SystemExit("Restore target must be a new science-restore-* project")
    containers = call(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={target}",
        ],
        capture=True,
    )
    volumes = call(
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={target}",
        ],
        capture=True,
    )
    if containers.strip() or volumes.strip():
        raise SystemExit("Target already contains containers/volumes; refusing to overwrite")
    for suffix in ["postgres", "clamav", *STATEFUL]:
        result = subprocess.run(
            ["docker", "volume", "inspect", f"{target}_{suffix}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            raise SystemExit(
                "An exact target volume already exists, even without Compose labels; refusing to overwrite"
            )
    started = time.monotonic()
    stage = Path(tempfile.mkdtemp(prefix="restore-", dir=directory))
    ops.restic(
        directory / "repository",
        "restore",
        snapshot,
        "--target",
        "/snapshot",
        stage=stage,
    )
    bundle = stage / "snapshot"
    metadata = json.loads((bundle / "manifest.json").read_text())
    if metadata["format"] != 1:
        raise SystemExit("Unsupported backup format")
    for name, expected in metadata["sha256"].items():
        path = (bundle / name).resolve()
        if not path.is_relative_to(bundle.resolve()) or not path.is_file():
            raise SystemExit("Unsafe backup path")
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                raise SystemExit("Backup checksum mismatch")
    config = dotenv_values(bundle / "config/runtime.env")
    config.update(
        COMPOSE_PROJECT_NAME=target,
        APP_PORT="18082",
        S3_PORT="19002",
        MAIL_PORT="18027",
        SECRET_DIRECTORY=str(bundle / "config/secrets"),
        SC_ENVIRONMENT="test",
        PUBLIC_URL="http://localhost:18082",
        S3_PUBLIC_ENDPOINT="http://localhost:19002",
        PROMETHEUS_PORT="19091",
        GRAFANA_PORT="13001",
        ALERTMANAGER_PORT="19094",
        APP_UID=str(os.getuid() or 10001),
        APP_GID=str(os.getgid() or 10001),
        SMTP_HOST="mailpit",
        SMTP_PORT="1025",
        SMTP_USERNAME="",
        SMTP_PASSWORD="",
        SMTP_SECURITY="none",
        MAIL_FROM="restore@example.test",
        ALERT_SMTP_HOST="mailpit",
        ALERT_SMTP_PORT="1025",
        ALERT_SMTP_USERNAME="",
        ALERT_SMTP_PASSWORD="",
        ALERT_SMTP_SECURITY="none",
        ALERT_EMAIL_TO="operator@example.test",
        ALERT_MAIL_FROM="restore@example.test",
        OFFSITE_TARGET_ENV="",
    )
    if os.getuid() == 0:
        os.chown(
            bundle / "config/secrets/jwt_private.pem",
            int(config["APP_UID"]),
            int(config["APP_GID"]),
        )
    (directory / "metrics").mkdir(exist_ok=True)
    envfile = stage / "restore.env"
    envfile.write_text(
        "\n".join(
            f"{name}={quote_env(value)}" for name, value in config.items() if value is not None
        )
        + "\n"
    )
    envfile.chmod(0o600)
    override = stage / "restore-compose.json"

    hostname = metadata["rabbitmq_hostname"]
    override.write_text(
        json.dumps(
            {
                "services": {
                    **{
                        name: {"image": identifier}
                        for name, identifier in metadata["images"].items()
                    },
                    "backup-metrics": {
                        "volumes": [
                            {
                                "type": "bind",
                                "source": str(directory / "metrics"),
                                "target": "/textfiles",
                                "read_only": True,
                                "bind": {"create_host_path": False},
                            }
                        ]
                    },
                    "rabbitmq": {
                        "hostname": hostname,
                        "environment": {"RABBITMQ_NODENAME": f"rabbit@{hostname}"},
                    },
                }
            }
        )
    )
    source_root = stage / "source"
    source_root.mkdir()
    with tarfile.open(bundle / "source.tar.gz", "r:gz") as archive:
        archive.extractall(source_root, filter="data")
    restored = Ops(envfile, target, override, root=source_root)
    if "alertmanager" in metadata["volumes"]:
        from alerting import render

        render(restored)
    with (bundle / "application-images.tar").open("rb") as source:
        subprocess.run(["docker", "load", "--quiet"], stdin=source, check=True)
    for service in metadata["volumes"]:
        volume = f"{target}_{service}"
        call(
            [
                "docker",
                "volume",
                "create",
                "--label",
                f"com.docker.compose.project={target}",
                "--label",
                f"com.docker.compose.volume={service}",
                volume,
            ],
            capture=True,
        )
        with (bundle / f"{service}.tar").open("rb") as source:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--entrypoint",
                    "tar",
                    "-v",
                    f"{volume}:/data",
                    "postgres:17-alpine",
                    "-C",
                    "/data",
                    "-xpf",
                    "-",
                ],
                stdin=source,
                check=True,
            )
    restored.compose("up", "-d", "--wait", "--wait-timeout", "120", "postgres")
    for service in SERVICES:
        with (bundle / f"{service}.dump").open("rb") as source:
            subprocess.run(
                [
                    *restored.base,
                    "exec",
                    "-T",
                    "postgres",
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "-U",
                    service,
                    "-d",
                    service,
                ],
                stdin=source,
                check=True,
            )
        for table, expected in metadata["counts"][service].items():
            actual = int(restored.sql(service, f'SELECT count(*) FROM "{table}"'))
            if actual != expected:
                raise SystemExit(f"Restored count mismatch: {service}/{table}")
    restored.compose(
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "180",
        "redis",
        "rabbitmq",
        "minio",
        "mailpit",
        "clamav",
        *APPS,
    )
    for service in SERVICES:
        restored.compose("exec", "-T", service, "python", "-m", "libs.platform.migrate", "check")
    response = httpx.get("http://localhost:18082/api/v1/projects", timeout=15)
    response.raise_for_status()
    restored.compose(
        "exec",
        "-T",
        "media",
        "python",
        "-",
        data=b"""import asyncio,base64,hashlib
from sqlalchemy import select
from libs.platform.config import get_settings
from libs.platform.database import Database
from services.media.models import File
from services.media.storage import s3_call
async def main():
    settings=get_settings()
    db=Database(settings)
    async with db.sessions() as session:
        rows=(await session.scalars(select(File).where(File.status=="ready"))).all()
        for row in rows:
            result=await s3_call(settings,"get_object",Bucket=settings.s3_files_bucket,Key=row.final_key)
            body=result["Body"]
            try: data=body.read(settings.max_pdf_bytes+1)
            finally: body.close()
            if row.purpose!="avatar":
                assert len(data)==row.size_bytes
                assert base64.b64encode(hashlib.sha256(data).digest()).decode()==row.checksum_sha256
    await db.engine.dispose()
    print("Restored S3 ready-file contents checked")
asyncio.run(main())
""",
    )
    telemetry(
        directory,
        success=time.time(),
        duration=time.monotonic() - started,
        restore=True,
    )
    print(
        f"Restore verified: counts of six databases, migration drift, service health, HTTP catalog. Configuration: {envfile}"
    )
    print(
        "The isolated restored stack and decrypted files were retained for inspection. Stop it with compose using restore.env AND restore-compose.json; no data was deleted."
    )
    print(f"Compose base: {source_root / 'deploy/compose.yaml'}")


def status(ops, directory, as_json):
    rows = ops.rows()
    expected = {
        *APPS,
        "postgres",
        "redis",
        "rabbitmq",
        "minio",
        "clamav",
        *([] if ops.config.get("SC_ENVIRONMENT") == "production" else ["mailpit"]),
    }
    states = {
        row["Service"]: {"state": row["State"], "health": row.get("Health", "")}
        for row in rows
        if row["Service"] in expected
    }
    healthy = all(
        name in states
        and states[name]["state"] == "running"
        and states[name]["health"] in {"", "healthy"}
        for name in expected
    )
    backup_status = directory / "metrics/status.json"
    saved = json.loads(backup_status.read_text()) if backup_status.exists() else {}
    backup_age = (
        time.time() - saved["last_success_timestamp"]
        if saved.get("last_success_timestamp")
        else None
    )
    backup_attention = (
        saved.get("last_attempt_success") != 1 or backup_age is None or backup_age > 3600
    )
    offsite_status = directory / "metrics/offsite.json"
    offsite = json.loads(offsite_status.read_text()) if offsite_status.exists() else {}
    offsite_attention = bool(ops.config.get("OFFSITE_TARGET_ENV")) and (
        not offsite.get("success")
        or (
            not offsite.get("rehearsal", False)
            and time.time() - offsite.get("verified_at", 0) > 3600
        )
    )
    report = {
        "healthy": healthy,
        "services": states,
        "backup": saved,
        "backup_attention_required": backup_attention,
        "offsite": offsite,
        "offsite_attention_required": offsite_attention,
        "backup_age_seconds": backup_age,
        "operationally_ready": healthy and not backup_attention and not offsite_attention,
    }
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        for name, state in sorted(states.items()):
            print(f"{name:24} {state['state']:12} {state['health']}")
        print(
            "Backup age:",
            round(report["backup_age_seconds"])
            if report["backup_age_seconds"] is not None
            else "NO VERIFIED SNAPSHOT",
            "seconds",
        )
        print("Backup status:", "ATTENTION REQUIRED" if backup_attention else "verified")
        if ops.config.get("OFFSITE_TARGET_ENV"):
            print(
                "Offsite copy:",
                "ATTENTION REQUIRED" if offsite_attention else "verified",
            )
    return 0 if report["operationally_ready"] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "backup",
            "restore",
            "status",
            "snapshots",
            "check",
            "pin-broker",
            "pin-images",
        ],
    )
    parser.add_argument("--env-file", default=os.environ.get("SC_ENV_FILE", ROOT / ".env"))
    parser.add_argument("--directory", type=Path, default=ROOT / "backups")
    parser.add_argument("--project")
    parser.add_argument("--service", choices=[*APPS, "minio"])
    parser.add_argument("--snapshot", default="latest")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    ops = Ops(args.env_file)
    directory = args.directory.resolve()
    if args.action == "pin-images":
        ops.pin_images([args.service] if args.service else None)
    elif args.action == "pin-broker":
        if not ops.config.get("RABBITMQ_NODENAME"):
            hostname = ops.container("rabbitmq")["Config"]["Hostname"]
            with ops.envfile.open("a") as destination:
                destination.write(
                    f"\nRABBITMQ_HOSTNAME='{hostname}'\nRABBITMQ_NODENAME='rabbit@{hostname}'\n"
                )
            print("Preserved existing RabbitMQ node identity across container recreation")
    elif args.action == "backup":
        backup(ops, directory)
        if ops.config.get("OFFSITE_TARGET_ENV"):
            target = Path(ops.config["OFFSITE_TARGET_ENV"])
            if not target.is_absolute():
                target = ROOT / target
            call(
                [
                    sys.executable,
                    str(ROOT / "deploy/scripts/offsite.py"),
                    "copy",
                    "--env-file",
                    str(ops.envfile),
                    "--target-env",
                    str(target),
                    "--directory",
                    str(directory),
                ]
            )
    elif args.action == "restore":
        restore(ops, directory, args.project or "", args.snapshot)
    elif args.action == "status":
        raise SystemExit(status(ops, directory, args.json))
    else:
        ops.restic(
            directory / "repository",
            args.action,
            *(["--read-data"] if args.action == "check" else []),
        )


if __name__ == "__main__":
    main()
