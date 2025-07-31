import logging

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Request, Query
from sqlalchemy.orm import Session
from schemas.template import (
    TemplateUploadResponse, TemplateUpdateRequest, ConfirmTemplateResponse,
    LatestTemplateResponse, UpdateTemplateResponse, FontUploadResponse
)
from services.template_service import (
    upload_template_service, confirm_latest_template_service,
    latest_template_service, update_latest_template_service, upload_font_service
)
from models.db import get_db


logger = logging.getLogger("template_router")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

router = APIRouter(prefix="/api/v1/template", tags=["Template"])


@router.post("/upload-template", response_model=TemplateUploadResponse)
def upload_template(
    tg_id: str = Query(...),
    file: UploadFile = File(...),
    ttf_files: list[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    logger.info(f"User {tg_id} started upload_template. File: {file.filename}, TTFs: {[ttf.filename for ttf in ttf_files or []]}")
    try:
        resp = upload_template_service(tg_id, file, ttf_files, db)
        logger.info(f"User {tg_id} uploaded template successfully.")
        return resp
    except Exception as e:
        logger.exception(f"User {tg_id} failed to upload template: {e}")
        raise


@router.post("/confirm-latest-template", response_model=ConfirmTemplateResponse)
def confirm_latest_template(tg_id: str = Query(...), db: Session = Depends(get_db)):
    logger.info(f"User {tg_id} confirming latest template.")
    try:
        resp = confirm_latest_template_service(tg_id, db)
        logger.info(f"User {tg_id} confirmed template successfully.")
        return resp
    except Exception as e:
        logger.exception(f"User {tg_id} failed to confirm template: {e}")
        raise


@router.get("/latest-template", response_model=LatestTemplateResponse)
def latest_template(tg_id: str = Query(...), db: Session = Depends(get_db)):
    logger.info(f"User {tg_id} requesting latest template.")
    try:
        resp = latest_template_service(tg_id, db)
        logger.info(f"User {tg_id} got latest template successfully.")
        return resp
    except Exception as e:
        logger.exception(f"User {tg_id} failed to get latest template: {e}")
        raise


@router.post("/update-latest-template", response_model=UpdateTemplateResponse)
async def update_latest_template(
    request: Request,
    tg_id: str = Query(...),
    db: Session = Depends(get_db)
):
    logger.info(f"User {tg_id} started update_latest_template.")
    payload = await request.json()
    try:
        resp = update_latest_template_service(tg_id, payload, db)
        logger.info(f"User {tg_id} updated template successfully.")
        return resp
    except Exception as e:
        logger.exception(f"User {tg_id} failed to update template: {e}")
        raise


@router.post("/upload-font", response_model=FontUploadResponse)
def upload_font(
    tg_id: str = Query(...),
    ttf_file: UploadFile = File(...)
):
    logger.info(f"User {tg_id} uploading font: {ttf_file.filename}")
    try:
        resp = upload_font_service(tg_id, ttf_file)
        logger.info(f"User {tg_id} uploaded font {ttf_file.filename} successfully.")
        return resp
    except Exception as e:
        logger.exception(f"User {tg_id} failed to upload font: {e}")
        raise

from services.template_service import get_templates_service, select_template_service


@router.get("/templates", tags=["Template"])
def get_templates(db: Session = Depends(get_db)):
    """Список готовых шаблонов"""
    return get_templates_service(db)


@router.post("/select-template", tags=["Template"])
def select_template(
    tg_id: str = Query(...),
    template_name: str = Query(...),
    db: Session = Depends(get_db)
):
    """Выбрать готовый шаблон и загрузить себе"""
    return select_template_service(tg_id, template_name, db)

