import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from ops import ROOT, Ops, quote_env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    source, target = Ops(ROOT / ".env.test"), Ops(ROOT / ".env")
    if (
        source.config.get("SC_ENVIRONMENT") != "test"
        or target.config.get("SC_ENVIRONMENT") != "development"
    ):
        raise SystemExit(
            "This rehearsal only supports the isolated local test and development stacks"
        )
    label = uuid4().hex[:12]
    bucket = "science-offsite-drill-" + label
    endpoint = f"http://127.0.0.1:{target.config.get('S3_PORT', '19000')}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=target.config["MINIO_ROOT_USER"],
        aws_secret_access_key=target.config["MINIO_ROOT_PASSWORD"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    app_client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=target.config["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=target.config["S3_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    try:
        app_client.list_objects_v2(Bucket=bucket)
    except ClientError as exc:
        assert exc.response["ResponseMetadata"]["HTTPStatusCode"] == 403
    else:
        raise SystemExit("Application account unexpectedly has access to the backup bucket")
    directory = ROOT / "backups" / f"offsite-drill-{label}"
    directory.mkdir(mode=0o700)
    credentials = directory / "target.env"
    values = {
        "OFFSITE_REPOSITORY": f"s3:{endpoint}/{bucket}/repository",
        "OFFSITE_PASSWORD": secrets.token_urlsafe(40),
        "OFFSITE_AWS_ACCESS_KEY_ID": target.config["MINIO_ROOT_USER"],
        "OFFSITE_AWS_SECRET_ACCESS_KEY": target.config["MINIO_ROOT_PASSWORD"],
        "OFFSITE_REHEARSAL": "true",
    }
    credentials.write_text(
        "\n".join(f"{key}={quote_env(value)}" for key, value in values.items()) + "\n"
    )
    credentials.chmod(0o600)
    common = [sys.executable, str(ROOT / "deploy/scripts/offsite.py")]
    options = [
        "--env-file",
        str(source.envfile),
        "--target-env",
        str(credentials),
        "--directory",
        str(args.directory.resolve()),
    ]
    subprocess.run([*common, "init", *options], check=True)
    subprocess.run([*common, "copy", *options], check=True)
    recovered = directory / "recovered/repository"

    fetch_options = [
        "--env-file",
        str(source.envfile),
        "--target-env",
        str(credentials),
        "--directory",
        str(directory / "empty-operator-state"),
    ]
    subprocess.run([*common, "fetch", *fetch_options, "--destination", str(recovered)], check=True)
    project = "science-restore-offsite-" + label
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "deploy/scripts/ops.py"),
            "restore",
            "--env-file",
            str(source.envfile),
            "--directory",
            str(recovered.parent),
            "--project",
            project,
        ],
        check=True,
    )
    report = {
        "success": True,
        "same_host_rehearsal": True,
        "project": project,
        "recovered_directory": str(recovered.parent),
        "application_s3_access_denied": True,
        "verified_at": datetime.now(UTC).isoformat(),
    }
    (ROOT / "test-results/offsite-drill.json").write_text(json.dumps(report, indent=2))
    print("Encrypted S3 copy, independent fetch, full-stack restore and app access denial verified")
    print("Rehearsal artifacts/volumes were retained. This is NOT a copy outside this host.")


if __name__ == "__main__":
    main()
