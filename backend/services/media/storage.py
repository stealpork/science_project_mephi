import asyncio
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from libs.platform.errors import APIError


def s3_client(settings, public=False):
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_public_endpoint if public else settings.s3_internal_endpoint,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
        config=Config(
            connect_timeout=3,
            read_timeout=8,
            retries={"total_max_attempts": 2, "mode": "standard"},
            signature_version="s3v4",
            s3={"addressing_style": settings.s3_addressing_style},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


async def s3_call(settings, method, **kwargs):
    try:
        return await asyncio.to_thread(getattr(s3_client(settings), method), **kwargs)
    except (BotoCoreError, ClientError) as exc:
        raise APIError(
            503, "STORAGE_UNAVAILABLE", "Файловое хранилище временно недоступно"
        ) from exc


async def initialize(settings):
    client = s3_client(settings)
    for bucket in (settings.s3_quarantine_bucket, settings.s3_files_bucket):
        try:
            await asyncio.to_thread(client.head_bucket, Bucket=bucket)
        except ClientError:
            await asyncio.to_thread(client.create_bucket, Bucket=bucket)
        await asyncio.to_thread(
            client.put_bucket_versioning,
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        if settings.s3_manage_cors:
            await asyncio.to_thread(
                client.put_bucket_cors,
                Bucket=bucket,
                CORSConfiguration={
                    "CORSRules": [
                        {
                            "AllowedOrigins": settings.allowed_origins,
                            "AllowedMethods": ["GET", "HEAD", "PUT"],
                            "AllowedHeaders": ["Content-Type", "x-amz-checksum-sha256", "Range"],
                            "ExposeHeaders": ["ETag", "x-amz-version-id"],
                            "MaxAgeSeconds": 600,
                        }
                    ]
                },
            )
    await asyncio.to_thread(
        client.put_bucket_lifecycle_configuration,
        Bucket=settings.s3_quarantine_bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "expire-quarantine",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "Expiration": {"Days": 2},
                    "NoncurrentVersionExpiration": {"NoncurrentDays": 2},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                }
            ]
        },
    )


def signed_download(settings, file, mode="inline"):
    return s3_client(settings, True).generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.s3_files_bucket,
            "Key": file.final_key,
            "ResponseContentType": file.content_type,
            "ResponseContentDisposition": f"{mode}; filename*=UTF-8''{quote(file.filename, safe='')}",
        },
        ExpiresIn=300,
    )


if __name__ == "__main__":
    from libs.platform.config import get_settings

    asyncio.run(initialize(get_settings()))
