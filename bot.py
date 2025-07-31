import asyncio
import logging
import os
import json
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile,
)
from aiogram.filters import CommandStart
from aiohttp import FormData, ClientSession
from aiogram.client.default import DefaultBotProperties

API_TOKEN = os.getenv("BOT_TOKEN", "7687107590:AAEaIJq9MgELGgYcISF0ht6fPNiTEv4JV7k")
API_BASE = os.getenv("API_BASE", "http://localhost:8000")
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("invoicebot")

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📤 Загрузить свой шаблон")],
        [KeyboardButton(text="📄 Выбрать готовый шаблон")]
    ],
    resize_keyboard=True
)


def pretty_scenario_status(scenario: dict) -> str:
    if not scenario:
        return ""
    steps = [
        "upload", "extract_fonts", "process_pdf", "parse_fields", "replace_fonts", "save_files", "upload_minio"
    ]
    step_emojis = {
        "upload": "⬆️", "extract_fonts": "🔤", "process_pdf": "📄", "parse_fields": "🧩",
        "replace_fonts": "🖋️", "save_files": "💾", "upload_minio": "☁️"
    }
    current = scenario.get("step", "")
    status = scenario.get("status", "")
    lines = []
    found_current = False
    for step in steps:
        emoji = step_emojis.get(step, "•")
        if status == "error" and step == current:
            lines.append(f"{emoji} <b>{step}</b> — ❌ <b>Ошибка</b>")
            found_current = True
            break
        elif step == current:
            lines.append(f"{emoji} <b>{step}</b> — <b>В процессе...</b>")
            found_current = True
        elif not found_current:
            lines.append(f"{emoji} {step} — ✅")
        else:
            lines.append(f"{emoji} {step} — ⏳")
    if scenario.get("log"):
        lines.append("\n<b>Лог:</b>")
        for entry in scenario["log"]:
            msg = entry.get("error") or entry.get("message") or ""
            lines.append(f"{entry.get('time', '')} [{entry.get('step', '')}]: {msg}")
    return "\n".join(lines)


def make_user_edit_json(parsed_data: dict) -> dict:
    user_json, service_values = {}, []
    descs = parsed_data.get("Descriptions") or parsed_data.get("Description")
    if isinstance(descs, list):
        for item in descs:
            val = (item or {}).get("value")
            if val:
                service_values.append(val)
    elif isinstance(descs, dict):
        val = descs.get("value")
        if val:
            service_values.append(val)
    invoice_for = parsed_data.get("Invoice For")
    if isinstance(invoice_for, dict):
        val = invoice_for.get("value")
        if val and val not in service_values:
            service_values.append(val)
    for i, val in enumerate(service_values, 1):
        user_json[f"Service {i}"] = val
    for k, v in parsed_data.items():
        if k in ("Descriptions", "Description", "Invoice For"):
            continue
        if isinstance(v, dict) and v.get("value") not in (None, "", []):
            user_json[k] = v["value"]
    return user_json


def pretty_print_editable_fields(user_json: dict) -> str:
    return "\n".join(
        [f"{i}. {k}: {v}" for i, (k, v) in enumerate(user_json.items(), 1)]) or "Нет полей для редактирования."


def get_user_dir(tg_id: str) -> str:
    path = os.path.join(UPLOAD_DIR, tg_id)
    os.makedirs(path, exist_ok=True)
    return path


@dp.message(CommandStart())
async def start(msg: Message):
    user_id = f"tg_{msg.from_user.id}"
    full_name = msg.from_user.full_name
    async with ClientSession() as session:
        await session.post(f"{API_BASE}/api/v1/user/register", params={"tg_id": user_id, "full_name": full_name})
    await msg.answer(
        f"👋 Привет, <b>{full_name}</b>!\n"
        "Добро пожаловать в <b>InvoiceBot</b>.\n"
        "Я помогу с вашими шаблонами счетов.\n"
        "Выберите действие в меню ниже.",
        reply_markup=main_menu
    )


@dp.message(F.document)
async def handle_ttf_or_template(msg: Message):
    ext = os.path.splitext(msg.document.file_name)[1].lower()
    user_id = f"tg_{msg.from_user.id}"
    user_dir = get_user_dir(user_id)
    if ext == ".ttf":
        file = await bot.download(msg.document.file_id)
        form = FormData()
        form.add_field("ttf_file", file, filename=msg.document.file_name, content_type="font/ttf")
        async with ClientSession() as session:
            await session.post(
                f"{API_BASE}/api/v1/template/upload-font",
                data=form,
                params={"tg_id": user_id}
            )
        await msg.answer(f"🆗 Файл {msg.document.file_name} загружен!")
        return

    if ext not in (".pdf", ".docx"):
        await msg.answer("❌ Поддерживаются только PDF, DOCX или TTF файлы.")
        return

    form = FormData()
    file = await bot.download(msg.document.file_id)
    path = f"temp_{msg.document.file_id}_{msg.document.file_name}"
    with open(path, "wb") as f:
        f.write(file.read())
    form.add_field("file", open(path, "rb"), filename=msg.document.file_name, content_type=msg.document.mime_type)
    for fname in os.listdir(user_dir):
        if fname.lower().endswith(".ttf"):
            form.add_field("ttf_files", open(os.path.join(user_dir, fname), "rb"), filename=fname,
                           content_type="font/ttf")
    await msg.answer("⏳ Обработка шаблона...", reply_markup=main_menu)
    async with ClientSession() as session:
        async with session.post(f"{API_BASE}/upload-template", data=form, params={"tg_id": user_id}) as resp:
            data = await resp.json()
    if resp.status != 200:
        return await msg.answer(f"❌ {data.get('detail')}", reply_markup=main_menu)

    scenario = data.get('scenario')
    if scenario:
        text = pretty_scenario_status(scenario)
        await msg.answer(f"📈 Прогресс:\n{text}", reply_markup=main_menu)

    fonts = data.get("fonts", [])
    parsed = data.get("parsed_data", {})
    user_friendly = make_user_edit_json(parsed)
    parsed_str = pretty_print_editable_fields(user_friendly)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_parsed")],
        [InlineKeyboardButton(text="✏️ Изменить поля", callback_data="edit_parsed")]
    ])
    text = (
            f"✅ Шаблон загружен!\n"
            + (f"🧩 Шрифты: {', '.join(fonts)}\n" if fonts else "")
            + f"<pre>{parsed_str}</pre>\n"
              "Отправьте JSON вида: {\"Service 1\": \"Новое описание\", \"Total\": \"1000\"}"
    )
    await msg.answer(text, reply_markup=kb)


@dp.callback_query(lambda c: c.data == "confirm_parsed")
async def confirm_cb(cb: types.CallbackQuery):
    user_id = f"tg_{cb.from_user.id}"
    async with ClientSession() as session:
        async with session.post(f"{API_BASE}/api/v1/template/confirm-latest-template", params={"tg_id": user_id}) as resp:
            res = await resp.json()
        updated_pdf_name = res.get("updated_pdf_name") or "invoice_updated.pdf"
        async with session.get(f"{API_BASE}/api/v1/file/get-presigned-url", params={
            "tg_id": user_id,
            "filename": updated_pdf_name
        }) as presigned_resp:
            presigned = await presigned_resp.json()
            pdf_presigned_url = presigned["presigned_url"]
        async with aiohttp.ClientSession() as fsession:
            async with fsession.get(pdf_presigned_url) as f:
                pdf_bytes = await f.read()
        await cb.message.answer_document(
            BufferedInputFile(pdf_bytes, filename=updated_pdf_name),
            caption="✅ Ваш обновленный счет"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬇️ Скачать PDF (5 мин)", url=pdf_presigned_url)]
        ])
        await cb.message.answer("⬇️ Ссылка для скачивания PDF (действует 5 минут):", reply_markup=kb)

        links = []
        if res.get("extracted_fonts_url"):
            fname = res["extracted_fonts_url"].split("/")[-1]
            async with session.get(f"{API_BASE}/api/v1/file/get-presigned-url",
                                   params={"tg_id": user_id, "filename": fname}) as font_url_resp:
                font_presigned = await font_url_resp.json()
            links.append(f'🧩 <a href="{font_presigned["presigned_url"]}">Шрифты</a>')
        if res.get("parsed_json_url"):
            fname = res["parsed_json_url"].split("/")[-1]
            async with session.get(f"{API_BASE}/api/v1/file/get-presigned-url",
                                   params={"tg_id": user_id, "filename": fname}) as json_url_resp:
                json_presigned = await json_url_resp.json()
            links.append(f'🧾 <a href="{json_presigned["presigned_url"]}">JSON</a>')
        if links:
            await cb.message.answer("\n".join(links), parse_mode="HTML")


@dp.callback_query(lambda c: c.data == "edit_parsed")
async def edit_prompt(cb: types.CallbackQuery):
    user_id = f"tg_{cb.from_user.id}"
    async with ClientSession() as session:
        async with session.get(f"{API_BASE}/api/v1/template/latest-template", params={"tg_id": user_id}) as r:
            parsed = (await r.json()).get('parsed_data', {})
    user_friendly = make_user_edit_json(parsed)
    fields = ", ".join(user_friendly.keys()) or "(нет полей)"
    prompt = (
        f"✏️ Доступные поля: <b>{fields}</b>\n"
        "Отправьте JSON, например: {\"Service 1\": \"Новая услуга\"}"
    )
    await cb.message.answer(prompt, reply_markup=main_menu)


@dp.message(lambda m: m.text and m.text.strip().startswith("{") and m.text.strip().endswith("}"))
async def handle_json_edit(msg: Message):
    try:
        new_data = json.loads(msg.text)
    except json.JSONDecodeError:
        return
    user_id = f"tg_{msg.from_user.id}"
    async with ClientSession() as session:
        async with session.get(f"{API_BASE}/api/v1/template/latest-template", params={"tg_id": user_id}) as r:
            old = (await r.json()).get('parsed_data', {})
        user_friendly_old = make_user_edit_json(old)
        merged = {**user_friendly_old, **new_data}
        async with session.post(
                f"{API_BASE}api/v1/template/update-latest-template", params={"tg_id": user_id}, json={"parsed_data": merged}
        ) as r2:
            if r2.status == 200:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_parsed")],
                    [InlineKeyboardButton(text="✏️ Изменить ещё", callback_data="edit_parsed")]
                ])
                await msg.answer(
                    f"✅ Обновлено:\n<pre>{json.dumps(merged, ensure_ascii=False, indent=2)}</pre>",
                    reply_markup=kb
                )
            else:
                await msg.answer("❌ Ошибка при обновлении.", reply_markup=main_menu)


@dp.message(lambda m: m.text == "📤 Загрузить свой шаблон")
@dp.callback_query(lambda c: c.data == "upload_own")
async def upload_prompt(event: types.Message | types.CallbackQuery):
    target = event.message if isinstance(event, types.CallbackQuery) else event
    prompt = (
        "✏️ Пришлите ваш файл-шаблон (.pdf или .docx).\n"
        "Прикрепите .ttf-файлы отдельными сообщениями до или после."
    )
    await target.answer(prompt, reply_markup=main_menu)


@dp.message(lambda m: m.text == "📄 Выбрать готовый шаблон")
@dp.callback_query(lambda c: c.data == "choose_template")
async def choose_prompt(event: types.Message | types.CallbackQuery):
    target = event.message if isinstance(event, types.CallbackQuery) else event
    user_id = f"tg_{target.from_user.id}"
    async with ClientSession() as session:
        async with session.get(f"{API_BASE}/api/v1/template/templates", params={"tg_id": user_id}) as resp:
            data = await resp.json()
    templates = data.get("templates", [])
    if not templates:
        return await target.answer("⚠️ Нет готовых шаблонов.", reply_markup=main_menu)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tpl['template_name'], callback_data=f"select:{i}")]
        for i, tpl in enumerate(templates)
    ])
    await target.answer("📑 Выберите шаблон:", reply_markup=kb)


@dp.callback_query(lambda c: c.data.startswith("select:"))
async def handle_select(cb: types.CallbackQuery):
    user_id = f"tg_{cb.from_user.id}"
    idx = int(cb.data.split(':')[1])
    async with ClientSession() as session:
        async with session.get(f"{API_BASE}/api/v1/template/templates", params={"tg_id": user_id}) as resp:
            data = await resp.json()
    lst = data.get("templates", [])
    if idx < 0 or idx >= len(lst):
        return await cb.message.answer("❌ Неверный выбор.", reply_markup=main_menu)
    name = lst[idx]['template_name']
    async with ClientSession() as session:
        async with session.post(f"{API_BASE}/api/v1/template/select-template",
                                params={"tg_id": user_id, "template_name": name}) as resp:
            data = await resp.json()
    if resp.status != 200:
        return await cb.message.answer(f"❌ {data.get('detail')}", reply_markup=main_menu)
    fonts = data.get("fonts", [])
    parsed = data.get("parsed_data", {})
    user_friendly = make_user_edit_json(parsed)
    parsed_str = pretty_print_editable_fields(user_friendly)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_parsed")],
        [InlineKeyboardButton(text="✏️ Изменить поля", callback_data="edit_parsed")]
    ])
    text = (
            f"✅ Шаблон: <b>{name}</b>\n"
            + (f"🧩 Шрифты: {', '.join(fonts)}\n" if fonts else "")
            + f"<pre>{parsed_str}</pre>\n"
              "Отправьте JSON вида: {\"Service 1\": \"Новое описание\", \"Total\": \"1000\"}"
    )
    await cb.message.answer(text, reply_markup=kb)


if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
