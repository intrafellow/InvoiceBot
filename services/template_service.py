import os
from datetime import datetime
from minio.error import S3Error
from fastapi import HTTPException
from sqlalchemy.orm import Session
from schemas.template import (
    RegisterUserRequest, TemplateUploadResponse, TemplateUpdateRequest, ConfirmTemplateResponse,
    LatestTemplateResponse, UpdateTemplateResponse, FontUploadResponse, TemplateScenario, TemplateStatus
)
from models.db import User, Template
from utils.pdf import (
    extract_fonts_from_pdf, extract_fonts_from_docx,
    save_extracted_fonts_list, save_parsed_data_json, extract_blocks_from_pdf, process_invoice_and_replace
)
from utils.font_map import build_font_map
from services.minio_service import minio_upload, minio_client, MINIO_BUCKET
from services.gemini_service import extract_fields_with_bbox_gemini

import logging_conf
logger = logging_conf.logger.getChild("template_service")

UPLOAD_DIR = "uploads"
MAX_TEMPLATE_SIZE_MB = 10


def register_user_service(data: RegisterUserRequest, db: Session):
    logger.info(f"Регистрация пользователя {data.tg_id} ({data.full_name})")
    if db.query(User).filter_by(tg_id=data.tg_id).first():
        logger.warning(f"User with tg_id={data.tg_id} already exists")
        raise HTTPException(400, "User exists")
    user = User(tg_id=data.tg_id, full_name=data.full_name)
    db.add(user)
    db.commit()
    logger.info(f"Пользователь {data.tg_id} успешно зарегистрирован")
    return {"message": "User registered"}


def upload_template_service(tg_id, file, ttf_files, db: Session):
    scenario_id = f"{tg_id}_{datetime.utcnow().isoformat()}"
    logger.info(f"Upload template для {tg_id}: {file.filename}")
    user = db.query(User).filter_by(tg_id=tg_id).first()
    if not user:
        logger.warning(f"User {tg_id} not found при загрузке шаблона")
        raise HTTPException(404, "User not found")
    user_dir = os.path.join(UPLOAD_DIR, tg_id)
    os.makedirs(user_dir, exist_ok=True)
    invoice_name, ext = os.path.splitext(file.filename)
    ext = ext.lower()
    if ext not in (".pdf", ".docx"):
        logger.warning(f"Недопустимый формат: {ext}")
        raise HTTPException(400, "Only PDF and DOCX supported")
    file_path = os.path.join(user_dir, f"{invoice_name}{ext}")
    file.file.seek(0)
    size_mb = file.file.seek(0, os.SEEK_END) / (1024*1024)
    file.file.seek(0)
    if size_mb > MAX_TEMPLATE_SIZE_MB:
        logger.warning(f"Файл слишком большой: {size_mb} MB")
        raise HTTPException(400, f"File too large >{MAX_TEMPLATE_SIZE_MB} MB")
    with open(file_path, "wb") as f:
        import shutil
        shutil.copyfileobj(file.file, f)
    logger.info(f"Файл шаблона сохранен: {file_path}")

    font_map = {}
    if ttf_files:
        for ttf_file in ttf_files:
            ttf_name = ttf_file.filename
            font_base = os.path.splitext(ttf_name)[0]
            ttf_path = os.path.join(user_dir, ttf_name)
            with open(ttf_path, "wb") as f:
                shutil.copyfileobj(ttf_file.file, f)
            font_map[font_base] = ttf_path
        if "default" not in font_map and font_map:
            font_map["default"] = list(font_map.values())[0]
        logger.info(f"Загружено TTF: {list(font_map.keys())}")
    else:
        font_map = build_font_map(user_dir)
        logger.info("Font map построен автоматически")

    extracted_fonts = set()
    if ext == ".pdf":
        extracted_fonts.update(extract_fonts_from_pdf(file_path))
    elif ext == ".docx":
        extracted_fonts.update(extract_fonts_from_docx(file_path))
    logger.info(f"Извлечены шрифты: {list(extracted_fonts)}")

    blocks = extract_blocks_from_pdf(file_path)
    parsed_data = extract_fields_with_bbox_gemini(blocks)
    logger.info(f"Парсинг Gemini выполнен, найдено полей: {len(parsed_data) if parsed_data else 0}")

    fonts_txt = save_extracted_fonts_list(user_dir, invoice_name, list(extracted_fonts))
    parsed_json = save_parsed_data_json(user_dir, invoice_name, parsed_data)

    db_template = Template(
        user_id=user.id,
        file_path=file_path,
        ttf_list=list(extracted_fonts),
        parsed_data=parsed_data,
        font_map=font_map,
        updated_at=datetime.utcnow(),
        invoice_name=invoice_name
    )
    db.add(db_template)
    db.commit()
    logger.info(f"Template DB object создан (user {tg_id})")

    scenario = TemplateScenario(
        scenario_id=scenario_id,
        status=TemplateStatus.parsing,
        step="save_files",
        log=[]
    )

    return TemplateUploadResponse(
        message="Template uploaded locally. Confirm to upload to MinIO.",
        fonts=list(extracted_fonts),
        parsed_data=parsed_data,
        invoice_name=invoice_name,
        local_pdf=file_path,
        local_fonts=fonts_txt,
        local_json=parsed_json,
        font_map=font_map,
        scenario=scenario
    )


def confirm_latest_template_service(tg_id, db: Session):
    logger.info(f"Confirm template для {tg_id}")
    user = db.query(User).filter_by(tg_id=tg_id).first()
    if not user:
        logger.warning(f"User {tg_id} not found для confirm")
        raise HTTPException(404, "User not found")
    template = db.query(Template).filter_by(user_id=user.id).order_by(Template.updated_at.desc()).first()
    if not template:
        logger.warning(f"Template для {tg_id} не найден")
        raise HTTPException(404, "Template not found")
    invoice_name = template.invoice_name
    user_dir = os.path.dirname(template.file_path)
    ext = os.path.splitext(template.file_path)[1].lower()
    pdf_path = os.path.join(user_dir, f"{invoice_name}{ext}")
    fonts_txt = os.path.join(user_dir, f"{invoice_name}_extracted_fonts.txt")
    parsed_json = os.path.join(user_dir, f"{invoice_name}_parsed_fields.json")
    updated_pdf_name = f"{invoice_name}_updated.pdf"
    updated_pdf = os.path.join(user_dir, updated_pdf_name)
    font_map = template.font_map or build_font_map(user_dir)

    result = process_invoice_and_replace(
        pdf_path=pdf_path,
        output_pdf=updated_pdf,
        changes=template.parsed_data or {},
        font_map=font_map,
        extract_fields_with_bbox_gemini=extract_fields_with_bbox_gemini
    )
    logger.info(f"PDF обработан для {tg_id}, изменено: {result.get('changed_count', 0)} полей")

    url_pdf = minio_upload(pdf_path, f"{tg_id}/{invoice_name}{ext}", "application/pdf")
    url_fonts = minio_upload(fonts_txt, f"{tg_id}/{invoice_name}_extracted_fonts.txt", "text/plain")
    url_json = minio_upload(parsed_json, f"{tg_id}/{invoice_name}_parsed_fields.json", "application/json")
    url_updated_pdf = minio_upload(updated_pdf, f"{tg_id}/{updated_pdf_name}", "application/pdf")

    template.is_active = 1
    template.updated_at = datetime.utcnow()
    db.commit()
    logger.info(f"Шаблон {invoice_name} загружен в Minio и отмечен как активный")

    scenario = TemplateScenario(
        scenario_id=template.parsed_data.get("scenario_id", ""),
        status=TemplateStatus.finished,
        step="upload_minio",
        log=[]
    )

    return ConfirmTemplateResponse(
        message="✅ Шаблон подтвержден.",
        pdf_url=url_pdf,
        updated_pdf_url=url_updated_pdf,
        updated_pdf_name=updated_pdf_name,
        extracted_fonts_url=url_fonts,
        parsed_json_url=url_json,
        scenario=scenario
    )


def latest_template_service(tg_id, db: Session):
    logger.info(f"Получение последнего шаблона для {tg_id}")
    user = db.query(User).filter_by(tg_id=tg_id).first()
    if not user:
        logger.warning(f"User {tg_id} не найден при latest_template")
        raise HTTPException(404, "User not found")
    template = db.query(Template).filter_by(user_id=user.id).order_by(Template.updated_at.desc()).first()
    if not template:
        logger.warning(f"Template для {tg_id} не найден при latest_template")
        raise HTTPException(404, "Template not found")
    scenario_id = template.parsed_data.get("scenario_id", "")
    scenario = TemplateScenario(
        scenario_id=scenario_id,
        status=TemplateStatus.finished,
        step="finished",
        log=[]
    )
    logger.info(f"Возврат информации о последнем шаблоне для {tg_id}")
    return LatestTemplateResponse(
        file_path=template.file_path,
        parsed_data=template.parsed_data or {},
        scenario=scenario
    )


def update_latest_template_service(tg_id, payload, db: Session):
    logger.info(f"Update шаблона для {tg_id}")
    parsed_in = payload.get("parsed_data") if isinstance(payload.get("parsed_data"), dict) else payload
    if not isinstance(parsed_in, dict):
        logger.warning("Некорректный payload для обновления шаблона")
        raise HTTPException(400, "Invalid payload: expected JSON object for parsed_data or root payload")
    user = db.query(User).filter_by(tg_id=tg_id).first()
    if not user:
        logger.warning(f"User {tg_id} не найден при update")
        raise HTTPException(404, "User not found")
    template = db.query(Template).filter_by(user_id=user.id).order_by(Template.updated_at.desc()).first()
    if not template:
        logger.warning(f"Template для {tg_id} не найден при update")
        raise HTTPException(404, "Template not found")
    invoice_name = template.invoice_name
    user_dir = os.path.dirname(template.file_path)
    ext = os.path.splitext(template.file_path)[1].lower()
    pdf_path = os.path.join(user_dir, f"{invoice_name}{ext}")
    updated_pdf = os.path.join(user_dir, f"{invoice_name}_updated.pdf")
    font_map = template.font_map or build_font_map(user_dir)
    result = process_invoice_and_replace(
        pdf_path=pdf_path,
        output_pdf=updated_pdf,
        changes=parsed_in,
        font_map=font_map,
        extract_fields_with_bbox_gemini=extract_fields_with_bbox_gemini
    )
    template.parsed_data = parsed_in
    template.updated_at = datetime.utcnow()
    db.commit()
    fonts_txt = os.path.join(user_dir, f"{invoice_name}_extracted_fonts.txt")
    parsed_json = save_parsed_data_json(user_dir, invoice_name, parsed_in)
    url_updated_pdf = minio_upload(updated_pdf, f"{tg_id}/{invoice_name}_updated.pdf", "application/pdf")
    url_json = minio_upload(parsed_json, f"{tg_id}/{invoice_name}_parsed_fields.json", "application/json")
    url_fonts = minio_upload(fonts_txt, f"{tg_id}/{invoice_name}_extracted_fonts.txt", "text/plain")

    logger.info(f"Шаблон {invoice_name} обновлен для {tg_id}")

    scenario = TemplateScenario(
        scenario_id=template.parsed_data.get("scenario_id", ""),
        status=TemplateStatus.finished,
        step="upload_minio",
        log=[]
    )

    return UpdateTemplateResponse(
        message="Template updated",
        updated_pdf_url=url_updated_pdf,
        parsed_json_url=url_json,
        extracted_fonts_url=url_fonts,
        fields_changed=result.get("fields_changed", {}),
        fields_found=result.get("fields_found", {}),
        scenario=scenario
    )


def upload_font_service(tg_id, ttf_file):
    logger.info(f"Загрузка шрифта для {tg_id}: {ttf_file.filename}")
    user_dir = os.path.join(UPLOAD_DIR, tg_id)
    os.makedirs(user_dir, exist_ok=True)
    ttf_name = ttf_file.filename
    ttf_path = os.path.join(user_dir, ttf_name)
    with open(ttf_path, "wb") as f:
        import shutil
        shutil.copyfileobj(ttf_file.file, f)
    from minio import Minio
    from services.minio_service import MINIO_BUCKET, minio_client
    minio_client.fput_object(
        MINIO_BUCKET,
        f"{tg_id}/{ttf_name}",
        ttf_path,
        content_type="font/ttf"
    )
    logger.info(f"Шрифт {ttf_name} успешно загружен для {tg_id}")
    return FontUploadResponse(message="Font uploaded", font_name=ttf_name)


TEMPLATES_PREFIX = "templates/"


def get_templates_service(db: Session):
    logger.info("Получение списка шаблонов из Minio")
    try:
        templates = []
        for obj in minio_client.list_objects(MINIO_BUCKET, prefix=TEMPLATES_PREFIX, recursive=True):
            if obj.object_name.endswith(".pdf") or obj.object_name.endswith(".docx"):
                templates.append({
                    "template_name": os.path.basename(obj.object_name),
                    "object_name": obj.object_name
                })
        logger.info(f"Найдено {len(templates)} шаблонов")
        return {"templates": templates}
    except S3Error as e:
        logger.error(f"Ошибка получения шаблонов из MinIO: {e}")
        raise HTTPException(500, f"MinIO error: {e}")


def select_template_service(tg_id: str, template_name: str, db: Session):
    logger.info(f"Пользователь {tg_id} выбирает шаблон {template_name} из общих")
    user = db.query(User).filter_by(tg_id=tg_id).first()
    if not user:
        logger.warning(f"User {tg_id} not found при выборе шаблона")
        raise HTTPException(404, "User not found")
    user_dir = os.path.join(UPLOAD_DIR, tg_id)
    os.makedirs(user_dir, exist_ok=True)
    src_object = f"{TEMPLATES_PREFIX}{template_name}"
    ext = os.path.splitext(template_name)[1].lower()
    if ext not in (".pdf", ".docx"):
        logger.warning(f"Недопустимый формат шаблона: {ext}")
        raise HTTPException(400, "Только PDF или DOCX шаблоны поддерживаются")
    dst_path = os.path.join(user_dir, template_name)
    try:
        minio_client.fget_object(MINIO_BUCKET, src_object, dst_path)
        logger.info(f"Шаблон {template_name} скачан в {dst_path}")
    except S3Error as e:
        logger.error(f"Ошибка скачивания шаблона {template_name}: {e}")
        raise HTTPException(500, f"MinIO download error: {e}")

    font_map = build_font_map(user_dir)
    extracted_fonts = set()
    if ext == ".pdf":
        extracted_fonts.update(extract_fonts_from_pdf(dst_path))
    elif ext == ".docx":
        extracted_fonts.update(extract_fonts_from_docx(dst_path))
    logger.info(f"Шрифты из шаблона {template_name} извлечены: {list(extracted_fonts)}")

    blocks = extract_blocks_from_pdf(dst_path)
    parsed_data = extract_fields_with_bbox_gemini(blocks)
    fonts_txt = save_extracted_fonts_list(user_dir, os.path.splitext(template_name)[0], list(extracted_fonts))
    parsed_json = save_parsed_data_json(user_dir, os.path.splitext(template_name)[0], parsed_data)

    db_template = Template(
        user_id=user.id,
        file_path=dst_path,
        ttf_list=list(extracted_fonts),
        parsed_data=parsed_data,
        font_map=font_map,
        updated_at=datetime.utcnow(),
        invoice_name=os.path.splitext(template_name)[0]
    )
    db.add(db_template)
    db.commit()
    logger.info(f"Template DB object создан по шаблону {template_name} для {tg_id}")

    scenario = TemplateScenario(
        scenario_id=f"{tg_id}_{datetime.utcnow().isoformat()}",
        status=TemplateStatus.uploaded,
        step="template_selected",
        log=[]
    )

    return {
        "message": "Template selected and uploaded.",
        "fonts": list(extracted_fonts),
        "parsed_data": parsed_data,
        "invoice_name": os.path.splitext(template_name)[0],
        "local_pdf": dst_path,
        "local_fonts": fonts_txt,
        "local_json": parsed_json,
        "font_map": font_map,
        "scenario": scenario
    }
