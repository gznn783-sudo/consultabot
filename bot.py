from __future__ import annotations

import logging
import os
import re
import tempfile
import unicodedata
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from pdf_engine import TemplateError, fill_pdf, inspect_template

load_dotenv()
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger(__name__)

# Compatível com o nome atual e com instalações antigas.
BOT_TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
    or ""
).strip()

# No Render, RENDER_EXTERNAL_URL já é fornecida automaticamente.
# WEBHOOK_URL continua disponível para URL personalizada ou execução fora do Render.
WEBHOOK_URL = (
    os.getenv("WEBHOOK_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or ""
).strip().rstrip("/")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
TEMPLATE_DIR = DATA_DIR / "templates"
OUTPUT_DIR = DATA_DIR / "outputs"
MAX_TEMPLATE_MB = int(os.getenv("MAX_TEMPLATE_MB", "20"))

if not BOT_TOKEN:
    raise RuntimeError("Defina BOT_TOKEN (ou TELEGRAM_TOKEN) nas variáveis de ambiente.")

TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Bot de preenchimento de PDF")
telegram_app = Application.builder().token(BOT_TOKEN).build()

ALIASES = {
    "quitação": "quitacao",
    "valor para quitação": "quitacao",
    "valor para quitacao": "quitacao",
    "número do processo": "processo",
    "numero do processo": "processo",
    "nome do autor": "autor",
    "comarca": "tribunal",
    "tabelião": "nome",
    "tabeliao": "nome",
    "prestador": "nome",
    "chave pix": "pix",
    "pix/cpf": "pix",
    "instituição": "banco",
    "instituicao": "banco",
    "agência": "agencia",
    "advogado responsável": "advogado",
    "advogado responsavel": "advogado",
    "data do documento": "data",
    "data da decisão": "data",
    "data da decisao": "data",
    "dia": "data",
}


def normalize_key(key: str) -> str:
    key = re.sub(r"\s+", " ", key.strip().lower())
    return ALIASES.get(key, key)


def parse_data(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    current_key: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        if ":" in line:
            key, value = line.split(":", 1)
            key = normalize_key(key)
            value = value.strip()
            if key:
                current_key = key
                data[key] = value
        elif current_key:
            # Continuation line for long fields such as assunto.
            data[current_key] = (data[current_key] + " " + line.strip()).strip()

    return {key: value for key, value in data.items() if value}


def template_path(user_id: int) -> Path:
    return TEMPLATE_DIR / f"{user_id}.pdf"


def filename_component(value: str, *, fallback: str = "DOCUMENTO") -> str:
    """Converte um valor em parte segura do nome do arquivo."""
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = re.sub(r"[^0-9A-Za-z]+", "_", ascii_value)
    ascii_value = re.sub(r"_+", "_", ascii_value).strip("_")
    return (ascii_value.upper() or fallback)[:100]


def build_output_filename(data: dict[str, str]) -> str:
    """Gera AUTOR_NUMERODOPROCESSO.pdf, sem pontuação no processo."""
    author = filename_component(data.get("autor") or data.get("nome") or "DOCUMENTO")
    process_digits = re.sub(r"\D", "", data.get("processo", ""))

    if process_digits:
        return f"{author}_{process_digits}.pdf"
    return f"{author}.pdf"


def fields_message(fields: list[str]) -> str:
    example = "\n".join(f"{field}: " for field in fields)
    return (
        "Campos encontrados no modelo:\n\n"
        + "\n".join(f"• {field}" for field in fields)
        + "\n\nEnvie /novo e depois preencha assim:\n\n"
        + example
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Envie /modelo para cadastrar o PDF do Canva.\n"
        "Depois use /novo para preencher os marcadores {{campo}}."
    )


async def modelo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    context.user_data["awaiting_template"] = True
    await update.message.reply_text(
        "Envie agora o PDF exportado do Canva.\n\n"
        "Use {{data}}, {{autor}}, {{processo}} e {{assunto}}. O formato antigo {{autor|95|8}} também funciona."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    path = template_path(update.effective_user.id)
    if not path.exists():
        await update.message.reply_text("Nenhum modelo cadastrado. Use /modelo.")
        return
    try:
        info = inspect_template(path)
        await update.message.reply_text(fields_message(info["fields"]))
    except TemplateError as exc:
        await update.message.reply_text(f"O modelo salvo não pôde ser lido: {exc}")


async def novo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    path = template_path(update.effective_user.id)
    if not path.exists():
        await update.message.reply_text("Primeiro envie seu PDF usando /modelo.")
        return

    try:
        info = inspect_template(path)
    except TemplateError as exc:
        await update.message.reply_text(f"O modelo não está pronto: {exc}")
        return

    context.user_data.clear()
    context.user_data["awaiting_data"] = True
    context.user_data["fields"] = info["fields"]
    await update.message.reply_text(fields_message(info["fields"]))


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.message.reply_text("Operação cancelada.")


async def receive_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.message.document
    if not document or document.mime_type != "application/pdf":
        return

    if not context.user_data.get("awaiting_template"):
        await update.message.reply_text("Use /modelo antes de enviar o PDF.")
        return

    if document.file_size and document.file_size > MAX_TEMPLATE_MB * 1024 * 1024:
        await update.message.reply_text(f"O PDF ultrapassa o limite de {MAX_TEMPLATE_MB} MB.")
        return

    user_id = update.effective_user.id
    destination = template_path(user_id)
    temporary = destination.with_suffix(".upload.pdf")

    try:
        tg_file = await context.bot.get_file(document.file_id)
        await tg_file.download_to_drive(custom_path=temporary)
        info = inspect_template(temporary)
        temporary.replace(destination)
        context.user_data.clear()
        await update.message.reply_text(
            "Modelo salvo. O bot removerá e reescreverá apenas os blocos que contêm marcadores, recalculando as linhas.\n\n" + fields_message(info["fields"])
        )
    except TemplateError as exc:
        temporary.unlink(missing_ok=True)
        await update.message.reply_text(f"Não consegui cadastrar o modelo: {exc}")
    except Exception as exc:
        logger.exception("Erro ao salvar modelo")
        temporary.unlink(missing_ok=True)
        await update.message.reply_text(f"Erro ao receber o PDF: {exc}")


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get("awaiting_data"):
        await update.message.reply_text("Use /novo para iniciar ou /modelo para trocar o PDF.")
        return

    path = template_path(update.effective_user.id)
    if not path.exists():
        context.user_data.clear()
        await update.message.reply_text("O modelo não foi encontrado. Envie novamente com /modelo.")
        return

    fields = list(context.user_data.get("fields", []))
    data = parse_data(update.message.text or "")
    missing = [field for field in fields if not data.get(field)]
    if missing:
        await update.message.reply_text(
            "Faltam estes campos:\n\n"
            + "\n".join(f"• {field}" for field in missing)
            + "\n\nEnvie novamente todos os dados."
        )
        return

    await update.message.reply_text("Preenchendo os blocos e recalculando as linhas...")
    output_name = build_output_filename(data)

    try:
        with tempfile.TemporaryDirectory(dir=OUTPUT_DIR) as temp_dir:
            output_path = Path(temp_dir) / output_name
            warnings = fill_pdf(path, output_path, data)
            caption = "PDF preenchido com sucesso."
            if warnings:
                caption += "\n\n" + "\n".join(warnings[:5])
            with output_path.open("rb") as pdf_file:
                await update.message.reply_document(
                    document=pdf_file,
                    filename=output_name,
                    caption=caption,
                )
        context.user_data.clear()
    except TemplateError as exc:
        await update.message.reply_text(f"Não foi possível preencher: {exc}")
    except Exception as exc:
        logger.exception("Erro ao preencher PDF")
        await update.message.reply_text(f"Erro ao gerar o PDF: {exc}")


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("modelo", modelo))
telegram_app.add_handler(CommandHandler("status", status))
telegram_app.add_handler(CommandHandler("novo", novo))
telegram_app.add_handler(CommandHandler("gerar", novo))
telegram_app.add_handler(CommandHandler("cancelar", cancelar))
telegram_app.add_handler(MessageHandler(filters.Document.PDF, receive_pdf))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text))


@app.on_event("startup")
async def startup() -> None:
    await telegram_app.initialize()
    await telegram_app.start()
    if WEBHOOK_URL:
        await telegram_app.bot.set_webhook(
            url=f"{WEBHOOK_URL}/webhook",
            allowed_updates=Update.ALL_TYPES,
        )
        logger.info("Webhook configurado: %s/webhook", WEBHOOK_URL)
    else:
        logger.warning("Nenhuma URL pública encontrada. Defina WEBHOOK_URL fora do Render; no Render, confirme que o serviço é do tipo Web Service.")


@app.on_event("shutdown")
async def shutdown() -> None:
    await telegram_app.stop()
    await telegram_app.shutdown()


@app.get("/")
async def home() -> dict:
    return {"status": "online", "service": "pdf-template-bot"}


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request) -> dict:
    payload = await request.json()
    update = Update.de_json(payload, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
