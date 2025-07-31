from pydantic import BaseModel, Field, constr, HttpUrl, conint, validator
from typing import List, Optional, Dict, Any
from enum import Enum


class TemplateStatus(str, Enum):
    started = "started"
    uploaded = "uploaded"
    parsing = "parsing"
    gemini_processing = "gemini_processing"
    finished = "finished"
    error = "error"


class TemplateScenario(BaseModel):
    scenario_id: constr() = Field(..., description="ID сценария обработки шаблона")
    status: TemplateStatus = Field(..., description="Статус обработки шаблона")
    step: Optional[constr(min_length=1, max_length=64)] = Field(None, description="Название текущего шага обработки")
    error_message: Optional[constr(min_length=1, max_length=256)] = Field(None, description="Сообщение об ошибке, если возникла")
    log: List[Dict[str, Any]] = Field(default_factory=list, description="Лог событий обработки (step, message, time и пр.)")


class RegisterUserRequest(BaseModel):
    tg_id: constr(pattern=r"^\d{3,32}$") = Field(..., example="123456789", description="Telegram ID (только цифры, 3-32 символа)")
    full_name: constr(min_length=2, max_length=64) = Field(..., example="John Doe", description="Full name")


class TemplateUploadResponse(BaseModel):
    message: constr(min_length=3, max_length=256)
    fonts: List[constr(min_length=1, max_length=64)]
    parsed_data: Dict[str, Any]
    invoice_name: constr(min_length=1, max_length=128)
    local_pdf: constr(min_length=1, max_length=256)
    local_fonts: constr(min_length=1, max_length=256)
    local_json: constr(min_length=1, max_length=256)
    font_map: Dict[str, str]
    scenario: Optional[TemplateScenario] = Field(None, description="Сценарий и статус обработки")


class TemplateUpdateRequest(BaseModel):
    parsed_data: Dict[str, Any]


class ConfirmTemplateResponse(BaseModel):
    message: constr(min_length=3, max_length=256)
    pdf_url: HttpUrl
    updated_pdf_url: HttpUrl
    updated_pdf_name: constr(min_length=1, max_length=128)
    extracted_fonts_url: HttpUrl
    parsed_json_url: HttpUrl
    scenario: Optional[TemplateScenario] = Field(None, description="Сценарий и статус обработки")


class LatestTemplateResponse(BaseModel):
    file_path: constr(min_length=1, max_length=256)
    parsed_data: Dict[str, Any]
    scenario: Optional[TemplateScenario] = Field(None, description="Сценарий и статус обработки")


class UpdateTemplateResponse(BaseModel):
    message: constr(min_length=3, max_length=256)
    updated_pdf_url: HttpUrl
    parsed_json_url: HttpUrl
    extracted_fonts_url: HttpUrl
    fields_changed: Dict[str, str]
    fields_found: Dict[str, str]
    scenario: Optional[TemplateScenario] = Field(None, description="Сценарий и статус обработки")


class FontUploadResponse(BaseModel):
    message: constr(min_length=3, max_length=128)
    font_name: constr(min_length=1, max_length=64)


class PresignedUrlResponse(BaseModel):
    presigned_url: HttpUrl
    expires: conint(ge=10, le=86400) = Field(..., example=300)

    @validator("expires")
    def expires_must_be_valid(cls, v):
        if not (10 <= v <= 86400):
            raise ValueError("expires must be between 10 and 86400 seconds")
        return v
