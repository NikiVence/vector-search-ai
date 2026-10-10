"""S3 operations and image validation; no uploads happen on import."""
import os
from functools import lru_cache

import boto3
from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError

load_dotenv()

CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class UnsupportedImageError(ValueError):
    pass


class InvalidImageError(ValueError):
    pass


def validate_image(source):
    """Validate bytes, including decoding, and return the actual MIME type."""
    try:
        with Image.open(source) as image:
            content_type = CONTENT_TYPES.get(image.format)
            if content_type is None:
                raise UnsupportedImageError("Only JPEG, PNG and WEBP are supported")
            image.load()
            return content_type
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise InvalidImageError("Invalid or damaged image") from exc


@lru_cache(maxsize=1)
def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        region_name=os.environ["S3_REGION"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )


def upload_file_to_s3(file_path: str, bucket_name: str, object_name: str):
    content_type = validate_image(file_path)
    get_s3_client().upload_file(
        Filename=file_path,
        Bucket=bucket_name,
        Key=object_name,
        ExtraArgs={"ContentType": content_type, "ContentDisposition": "inline"},
    )


def generate_presigned_url(bucket_name: str, object_name: str, expiration: int = 3600):
    return get_s3_client().generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket_name, "Key": object_name},
        ExpiresIn=expiration,
    )
