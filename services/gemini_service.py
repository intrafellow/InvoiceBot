import os
import json
import logging
from typing import List, Dict, Any, Optional, Union
import google.generativeai as genai

import logging_conf

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

logger = logging.getLogger("gemini_service")

logger.info("GEMINI_API_KEY loaded: %s", os.environ.get("GEMINI_API_KEY"))

# Настройка Gemini API ключа
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or "your_key"
genai.configure(api_key=GEMINI_API_KEY)

FIELDS_TO_EXTRACT = [
    "Invoice Number", "Invoice Date", "Due Date", "Client Name",
    "Company Name", "Client Address", "Client Phone", "Client Email",
    "Invoice For", "Bank Name", "Account Name", "Account Number",
    "IBAN", "SWIFT", "Account From", "Amount", "Currency",
    "Total", "Subtotal", "Descriptions", "Description"
]


def ask_gemini(
    prompt: str,
    model_name: str = "models/gemini-2.5-flash-preview-05-20",
    max_tokens: Optional[int] = None,
) -> str:
    """Отправить промпт в Gemini и вернуть сырой текст-ответ."""
    logger.info("Gemini prompt: %s", prompt[:500])
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": max_tokens} if max_tokens else None
        )
        logger.info("Gemini response received (%d chars)", len(response.text or ""))
        return response.text
    except Exception as e:
        logger.error(f"Gemini error: {e}", exc_info=True)
        return ""


def extract_json_from_gemini(text: str) -> str:
    """Вырезать JSON из ответа Gemini (если есть)."""
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end < start:
        logger.error("Gemini не вернул JSON! Текст: %s", text[:500])
        raise ValueError("Gemini не вернул JSON!")
    return text[start:end+1]


def build_parsing_prompt(blocks: List[dict]) -> str:
    """Собирает промпт для Gemini из массива текстовых блоков PDF."""
    fields_list = ', '.join(FIELDS_TO_EXTRACT)
    system_prompt = (
        "You are given an array of text blocks from a PDF invoice. "
        "Each block contains 'text', its bounding box ('bbox'), font, size, and 'page' number. "
        f"For each of the following fields [{fields_list}], "
        "find ONLY the value (not including label, key, or prefix) and return the exact bbox, font, and size for that value (not the whole line, not including any label). "
        "For fields like 'Descriptions' or 'Description' (service lines), if there are multiple, return a list of all with value/bbox/font/size/page. "
        "Return valid JSON like: "
        "{\"Description\": {\"value\": \"...\", \"bbox\": [x0, y0, x1, y1], \"font\": \"...\", \"size\": 11.0, \"page\": page_num}, ...}, if not found — set to null. Do NOT add any explanation or non-JSON text."
    )
    user_prompt = "blocks:\n" + json.dumps(blocks, ensure_ascii=False)
    return f"{system_prompt}\n{user_prompt}"


def extract_fields_with_bbox_gemini(blocks: List[dict]) -> Dict[str, Union[dict, list, None]]:
    """
    Для блока текста PDF вызывает Gemini, парсит JSON-ответ и возвращает словарь полей.
    """
    logger.info("Start extracting fields with Gemini...")
    prompt = build_parsing_prompt(blocks)
    resp_text = ask_gemini(prompt)
    resp_text = (resp_text or "").strip()
    if not resp_text:
        logger.error("Gemini вернул пустой ответ!")
        raise ValueError("Gemini вернул пустой ответ!")
    try:
        json_only = extract_json_from_gemini(resp_text)
        fields = json.loads(json_only)
        logger.info("Fields extracted from Gemini: %s", list(fields.keys()))
    except Exception:
        logger.error(f"Gemini output parse error:\n{resp_text}")
        raise
    return fields
