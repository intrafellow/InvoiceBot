import os
import json
import zipfile
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from minio import Minio
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request, Query
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from fastapi.responses import JSONResponse


DATABASE_URL = "sqlite:///./tests.db"
UPLOAD_DIR = "uploads"
MAX_TEMPLATE_SIZE_MB = 10

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "invoices")

os.makedirs(UPLOAD_DIR, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    tg_id = Column(String, unique=True)
    full_name = Column(String)
    registered_at = Column(DateTime, default=datetime.utcnow)
    templates = relationship("Template", back_populates="user")


class Template(Base):
    __tablename__ = "templates"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    file_path = Column(String)
    ttf_list = Column(JSON)
    parsed_data = Column(JSON, nullable=True)
    is_active = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow)
    invoice_name = Column(String)
    font_map = Column(JSON, nullable=True)
    user = relationship("User", back_populates="templates")


Base.metadata.create_all(bind=engine)

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def normalize_font_name(name: str) -> str:
    return name.split("+")[-1] if "+" in name else name


def extract_fonts_from_pdf(file_path: str) -> List[str]:
    import fitz
    doc = fitz.open(file_path)
    fonts = set()
    for page in doc:
        for font in page.get_fonts():
            fonts.add(normalize_font_name(font[3]))
    return list(fonts)


def extract_fonts_from_docx(file_path: str) -> List[str]:
    fonts = set()
    try:
        with zipfile.ZipFile(file_path, 'r') as docx:
            if 'word/document.xml' in docx.namelist():
                xml_content = docx.read('word/document.xml')
                root = ET.fromstring(xml_content)
                for rFonts in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rFonts"):
                    for attr in ["ascii", "hAnsi", "cs", "eastAsia"]:
                        val = rFonts.attrib.get(
                            f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{attr}")
                        if val:
                            fonts.add(val)
    except Exception:
        pass
    return list(fonts)


def save_extracted_fonts_list(dirpath: str, invoice_name: str, fonts: List[str]) -> str:
    path = os.path.join(dirpath, f"{invoice_name}_extracted_fonts.txt")
    with open(path, "w", encoding="utf-8") as f:
        for font in fonts:
            f.write(f"{font}\n")
    return path


def save_parsed_data_json(dirpath: str, invoice_name: str, data: dict) -> str:
    path = os.path.join(dirpath, f"{invoice_name}_parsed_fields.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path

def minio_upload(local_path: str, object_name: str, content_type: str = "application/octet-stream") -> str:
    minio_client.fput_object(
        MINIO_BUCKET,
        object_name,
        local_path,
        content_type=content_type
    )
    return f"http://{MINIO_ENDPOINT}/{MINIO_BUCKET}/{object_name}"


from pars1 import process_invoice_and_replace


def build_font_map(user_dir: str) -> Dict[str, str]:
    font_map = {}
    for fname in os.listdir(user_dir):
        if fname.lower().endswith((".ttf", ".otf")):
            font_name = os.path.splitext(fname)[0]
            font_map[font_name] = os.path.join(user_dir, fname)
    if "default" not in font_map and font_map:
        font_map["default"] = list(font_map.values())[0]
    return font_map


@app.post("/register")
def register_user(tg_id: str, full_name: str, db: Session = Depends(get_db)):
    if db.query(User).filter_by(tg_id=tg_id).first():
        raise HTTPException(400, "User exists")
    user = User(tg_id=tg_id, full_name=full_name)
    db.add(user); db.commit()
    return {"message": "User registered"}


@app.post("/upload-template")
def upload_template(
    tg_id: str,
    file: UploadFile = File(...),
    ttf_files: Optional[List[UploadFile]] = File(None),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter_by(tg_id=tg_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    user_dir = os.path.join(UPLOAD_DIR, tg_id)
    os.makedirs(user_dir, exist_ok=True)
    invoice_name, ext = os.path.splitext(file.filename)
    ext = ext.lower()
    if ext not in (".pdf", ".docx"):
        raise HTTPException(400, "Only PDF and DOCX")
    file_path = os.path.join(user_dir, f"{invoice_name}{ext}")
    file.file.seek(0)
    size_mb = file.file.seek(0, os.SEEK_END) / (1024*1024)
    file.file.seek(0)
    if size_mb > MAX_TEMPLATE_SIZE_MB:
        raise HTTPException(400, f"File too large >{MAX_TEMPLATE_SIZE_MB} MB")
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

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
    else:
        font_map = build_font_map(user_dir)

    extracted_fonts = set()
    if ext == ".pdf":
        extracted_fonts.update(extract_fonts_from_pdf(file_path))
    elif ext == ".docx":
        extracted_fonts.update(extract_fonts_from_docx(file_path))

    from pars1 import extract_blocks_from_pdf, extract_fields_with_bbox_gemini

    blocks = extract_blocks_from_pdf(file_path)
    parsed_data = extract_fields_with_bbox_gemini(blocks)
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
    db.add(db_template); db.commit()

    return {
        "message": "Template uploaded locally. Confirm to upload to MinIO.",
        "fonts": list(extracted_fonts),
        "parsed_data": parsed_data,
        "invoice_name": invoice_name,
        "local_pdf": file_path,
        "local_fonts": fonts_txt,
        "local_json": parsed_json,
        "font_map": font_map
    }


@app.post("/confirm-latest-template")
def confirm_latest_template(tg_id: str, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(tg_id=tg_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    template = db.query(Template).filter_by(user_id=user.id).order_by(Template.updated_at.desc()).first()
    if not template:
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
        font_map=font_map
    )

    url_pdf = minio_upload(pdf_path, f"{tg_id}/{invoice_name}{ext}", "application/pdf")
    url_fonts = minio_upload(fonts_txt, f"{tg_id}/{invoice_name}_extracted_fonts.txt", "text/plain")
    url_json = minio_upload(parsed_json, f"{tg_id}/{invoice_name}_parsed_fields.json", "application/json")
    url_updated_pdf = minio_upload(updated_pdf, f"{tg_id}/{updated_pdf_name}", "application/pdf")

    template.is_active = 1
    template.updated_at = datetime.utcnow()
    db.commit()

    return {
        "message": f"✅ Шаблон подтвержден.",
        "pdf_url": url_pdf,
        "updated_pdf_url": url_updated_pdf,
        "updated_pdf_name": updated_pdf_name,
        "extracted_fonts_url": url_fonts,
        "parsed_json_url": url_json
    }


@app.get("/latest-template")
def latest_template(tg_id: str = Query(...), db: Session = Depends(get_db)):
    user = db.query(User).filter_by(tg_id=tg_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    template = db.query(Template).filter_by(user_id=user.id).order_by(Template.updated_at.desc()).first()
    if not template:
        raise HTTPException(404, "Template not found")
    return {"file_path": template.file_path, "parsed_data": template.parsed_data or {}}


@app.post("/update-latest-template")
async def update_latest_template(
    tg_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    payload = await request.json()
    parsed_in = payload.get("parsed_data") if isinstance(payload.get("parsed_data"), dict) else payload
    if not isinstance(parsed_in, dict):
        raise HTTPException(400, "Invalid payload: expected JSON object for parsed_data or root payload")
    user = db.query(User).filter_by(tg_id=tg_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    template = db.query(Template).filter_by(user_id=user.id).order_by(Template.updated_at.desc()).first()
    if not template:
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
        font_map=font_map
    )
    template.parsed_data = parsed_in
    template.updated_at = datetime.utcnow()
    db.commit()
    fonts_txt = os.path.join(user_dir, f"{invoice_name}_extracted_fonts.txt")
    parsed_json = save_parsed_data_json(user_dir, invoice_name, parsed_in)
    url_updated_pdf = minio_upload(updated_pdf, f"{tg_id}/{invoice_name}_updated.pdf", "application/pdf")
    url_json = minio_upload(parsed_json, f"{tg_id}/{invoice_name}_parsed_fields.json", "application/json")
    url_fonts = minio_upload(fonts_txt, f"{tg_id}/{invoice_name}_extracted_fonts.txt", "text/plain")
    return {
        "message": "Template updated",
        "updated_pdf_url": url_updated_pdf,
        "parsed_json_url": url_json,
        "extracted_fonts_url": url_fonts,
        "fields_changed": result.get("fields_changed", {}),
        "fields_found": result.get("fields_found", {})
    }


@app.exception_handler(Exception)
async def all_exception_handler(request, exc):
    import traceback
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "trace": traceback.format_exc()}
    )


@app.post("/upload-font")
def upload_font(
    tg_id: str,
    ttf_file: UploadFile = File(...),
):
    user_dir = os.path.join(UPLOAD_DIR, tg_id)
    os.makedirs(user_dir, exist_ok=True)
    ttf_name = ttf_file.filename
    ttf_path = os.path.join(user_dir, ttf_name)
    with open(ttf_path, "wb") as f:
        shutil.copyfileobj(ttf_file.file, f)
    minio_client.fput_object(
        MINIO_BUCKET,
        f"{tg_id}/{ttf_name}",
        ttf_path,
        content_type="font/ttf"
    )
    return {"message": "Font uploaded", "font_name": ttf_name}


from fastapi import Query


@app.get("/get-presigned-url")
def get_presigned_url(
    tg_id: str = Query(..., description="Telegram ID юзера (например, tg_123456)"),
    filename: str = Query(..., description="Имя файла в бакете (например, Relaway.ttf или invoice_updated.pdf)"),
    expires: int = Query(300, description="Сколько секунд будет жить ссылка (по умолчанию 5 минут)")
):
    """
    Генерирует presigned URL для скачивания любого файла из приватного бакета MinIO.
    """
    object_name = f"{tg_id}/{filename}"
    try:
        url = minio_client.presigned_get_object(MINIO_BUCKET, object_name, expires=timedelta(seconds=expires))
    except Exception as e:
        raise HTTPException(500, detail=f"Ошибка генерации ссылки: {e}")
    return {"presigned_url": url, "expires": expires}