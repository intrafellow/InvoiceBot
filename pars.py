import fitz
import os
import json
import logging
import google.generativeai as genai
from typing import Dict, Union, Optional, List
import shutil


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or "your_gemini_api"
genai.configure(api_key=GEMINI_API_KEY)

FIELDS_TO_EXTRACT = [
    "Invoice Number", "Invoice Date", "Due Date", "Client Name",
    "Company Name", "Client Address", "Client Phone", "Client Email",
    "Invoice For", "Bank Name", "Account Name", "Account Number",
    "IBAN", "SWIFT", "Account From", "Amount", "Currency",
    "Total", "Subtotal", "Descriptions", "Description"
]


FONT_MAP: Dict[str, str] = {}


def ask_gemini(prompt: str) -> str:
    try:
        model = genai.GenerativeModel("models/gemini-2.5-flash-preview-05-20")
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logging.error(f"Gemini error: {e}", exc_info=True)
        return ""


def extract_blocks_from_pdf(pdf_path: str) -> List[dict]:
    doc = fitz.open(pdf_path)
    all_blocks = []
    for page_num, page in enumerate(doc):
        d = page.get_text("dict")
        for block in d["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    all_blocks.append({
                        "page": page_num,
                        "text": span["text"].strip(),
                        "bbox": span["bbox"],
                        "font": span.get("font", ""),
                        "size": span.get("size", 11.0),
                        "flags": span.get("flags", 0)
                    })
    return all_blocks


def extract_json_from_gemini(text: str) -> str:
    """
    Возвращает только первую JSON-строку (от первой {до последней}).
    """
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end < start:
        raise ValueError("Gemini не вернул JSON!")
    return text[start:end+1]


def extract_fields_with_bbox_gemini(blocks: List[dict]) -> Dict[str, Union[dict, list, None]]:
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
    full_prompt = f"{system_prompt}\n{user_prompt}"

    resp_text = ask_gemini(full_prompt)
    resp_text = resp_text.strip()
    if not resp_text:
        raise ValueError("Gemini вернул пустой ответ!")

    try:
        json_only = extract_json_from_gemini(resp_text)
        fields = json.loads(json_only)
    except Exception:
        logging.error(f"Gemini output parse error:\n{resp_text}")
        raise
    return fields


def find_value_bbox(page, value):
    if not value:
        return None
    rects = page.search_for(str(value))
    if rects:
        return [rects[0].x0, rects[0].y0, rects[0].x1, rects[0].y1]
    return None


def get_font_file(pdf_fontname: str) -> Optional[str]:
    if pdf_fontname in FONT_MAP:
        return FONT_MAP[pdf_fontname]
    for key, path in FONT_MAP.items():
        if key.lower() in pdf_fontname.lower():
            return path
    return FONT_MAP.get("default")


def replace_fields_in_pdf_bbox(
    input_pdf: str,
    output_pdf: str,
    replacements: Dict[str, dict],
    font_map: Optional[Dict[str, str]] = None
) -> int:
    doc = fitz.open(input_pdf)
    changed_count = 0
    inserted_fonts = {}

    for field, v in replacements.items():
        old_val = v["old"]
        new_val = v["new"]
        bbox = v["bbox"]
        page_num = v["page"]
        font = v.get("font", "helv")
        size = v.get("size", 11.0)
        if not old_val or not new_val or old_val == new_val or page_num is None:
            continue
        page = doc[page_num]
        precise_bbox = find_value_bbox(page, old_val)
        if precise_bbox:
            x0, y0, x1, y1 = precise_bbox
        elif bbox:
            x0, y0, x1, y1 = bbox
        else:
            continue
        pad = 1
        rect = fitz.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad)
        page.draw_rect(rect, color=(1,1,1), fill=(1,1,1))

        fontfile = None
        fontname = font
        fm = font_map or FONT_MAP
        if fm:
            fontfile = get_font_file(font)
        if fontfile:
            if fontfile not in inserted_fonts:
                fontname = doc.insert_font(file=fontfile)
                inserted_fonts[fontfile] = fontname
            else:
                fontname = inserted_fonts[fontfile]
        else:
            fontname = "helv"

        insert_x = x0
        insert_y = y1 - 2
        page.insert_text(
            (insert_x, insert_y),
            str(new_val),
            fontsize=size,
            fontname=fontname,
            color=(0,0,0),
            overlay=True
        )
        changed_count += 1
    doc.save(output_pdf)
    doc.close()
    return changed_count


def process_invoice_and_replace(
    pdf_path: str,
    output_pdf: str,
    changes: Dict[str, str],
    font_map: Optional[Dict[str, str]] = None
) -> Dict[str, Union[int, str, dict]]:
    blocks = extract_blocks_from_pdf(pdf_path)
    fields = extract_fields_with_bbox_gemini(blocks)
    editable_fields = []
    desc_values = set()
    if "Descriptions" in fields and isinstance(fields["Descriptions"], list):
        for idx, elem in enumerate(fields["Descriptions"], 1):
            if isinstance(elem, dict) and elem.get("value"):
                editable_fields.append((f"Service {idx}", elem))
                desc_values.add(elem.get("value"))
    elif "Descriptions" in fields and isinstance(fields["Descriptions"], dict) and fields["Descriptions"].get("value"):
        editable_fields.append(("Service", fields["Descriptions"]))
        desc_values.add(fields["Descriptions"].get("value"))
    elif "Description" in fields and isinstance(fields["Description"], dict) and fields["Description"].get("value"):
        editable_fields.append(("Service", fields["Description"]))
        desc_values.add(fields["Description"].get("value"))
    elif "Description" in fields and isinstance(fields["Description"], list):
        for idx, elem in enumerate(fields["Description"], 1):
            if isinstance(elem, dict) and elem.get("value"):
                editable_fields.append((f"Service {idx}", elem))
                desc_values.add(elem.get("value"))
    for k, v in fields.items():
        if k in ("Description", "Descriptions"):
            continue
        if isinstance(v, list):
            for elem in v:
                if isinstance(elem, dict) and elem.get("value"):
                    editable_fields.append((k, elem))
        elif isinstance(v, dict) and v.get("value"):
            editable_fields.append((k, v))
    replacements = {}
    for k, v in editable_fields:
        orig_value = v.get("value")
        if k in changes:
            new_val = changes[k]
            if isinstance(new_val, dict) and "value" in new_val:
                new_val = new_val["value"]
            if orig_value and new_val != orig_value:
                replacements[k] = {
                    "old": orig_value,
                    "new": new_val,
                    "bbox": v.get("bbox"),
                    "page": v.get("page"),
                    "font": v.get("font", "helv"),
                    "size": v.get("size", 11.0)
                }
    if not replacements:
        shutil.copy(pdf_path, output_pdf)
        return {
            "changed_count": 0,
            "output_pdf": output_pdf,
            "fields_found": {k: v.get("value") for k, v in editable_fields},
            "fields_changed": {}
        }
    count = replace_fields_in_pdf_bbox(pdf_path, output_pdf, replacements, font_map)
    return {
        "changed_count": count,
        "output_pdf": output_pdf,
        "fields_found": {k: v.get("value") for k, v in editable_fields},
        "fields_changed": {k: changes[k] for k in replacements},
    }