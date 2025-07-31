import logging

from fastapi import APIRouter

logger = logging.getLogger("health_router")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

router = APIRouter(prefix="/api/v1", tags=["Health"])


@router.get("/health", summary="Health check", description="Check service health")
def health_check():
    return {"status": "ok"}
