import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from schemas.template import RegisterUserRequest
from services.template_service import register_user_service
from models.db import get_db


logger = logging.getLogger("user_router")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

router = APIRouter(prefix="/api/v1/user", tags=["User"])

@router.post("/register", response_model=dict)
def register_user(data: RegisterUserRequest, db: Session = Depends(get_db)):
    logger.info(f"User registration requested: tg_id={data.tg_id}, full_name={data.full_name}")
    try:
        result = register_user_service(data, db)
        logger.info(f"User registered successfully: tg_id={data.tg_id}")
        return result
    except Exception as ex:
        logger.exception(f"Failed to register user: tg_id={data.tg_id} | error={ex}")
        raise
