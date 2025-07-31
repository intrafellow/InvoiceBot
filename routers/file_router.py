import logging

from fastapi import APIRouter, Query
from schemas.template import PresignedUrlResponse
from services.minio_service import get_presigned_url as minio_get_presigned_url


logger = logging.getLogger("file_router")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

router = APIRouter(prefix="/api/v1/file", tags=["File"])


@router.get("/get-presigned-url", response_model=PresignedUrlResponse)
def get_presigned_url_endpoint(
    tg_id: str = Query(..., description="Telegram ID юзера"),
    filename: str = Query(..., description="Имя файла"),
    expires: int = Query(300, description="Время жизни ссылки (сек)")
):
    url = minio_get_presigned_url(tg_id, filename, expires)
    return {"presigned_url": url, "expires": expires}
