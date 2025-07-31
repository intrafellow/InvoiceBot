import os
from datetime import timedelta
from minio import Minio
from fastapi import HTTPException
import logging_conf

logger = logging_conf.logger.getChild("minio_service")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "invoices")

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)


def minio_upload(local_path: str, object_name: str, content_type: str = "application/octet-stream") -> str:
    """Загрузка файла в MinIO и возврат публичного URL (если бакет публичный)"""
    logger.info(f"Uploading {local_path} as {object_name} [{content_type}] в бакет {MINIO_BUCKET}")
    try:
        minio_client.fput_object(
            MINIO_BUCKET,
            object_name,
            local_path,
            content_type=content_type
        )
        logger.info(f"Файл {object_name} успешно загружен в MinIO")
    except Exception as e:
        logger.error(f"MinIO upload error for {object_name}: {e}", exc_info=True)
        raise HTTPException(500, detail=f"MinIO upload error: {e}")
    return f"http://{MINIO_ENDPOINT}/{MINIO_BUCKET}/{object_name}"


def get_presigned_url(
    tg_id: str,
    filename: str,
    expires: int = 300
) -> str:
    """Генерация presigned-ссылки на объект в MinIO"""
    object_name = f"{tg_id}/{filename}"
    logger.info(f"Генерирую presigned URL для {object_name}, expires={expires}")
    try:
        url = minio_client.presigned_get_object(
            MINIO_BUCKET,
            object_name,
            expires=timedelta(seconds=expires)
        )
        logger.info(f"Presigned URL успешно сгенерирован для {object_name}")
    except Exception as e:
        logger.error(f"Ошибка генерации presigned URL для {object_name}: {e}", exc_info=True)
        raise HTTPException(500, detail=f"Ошибка генерации ссылки: {e}")
    return url
