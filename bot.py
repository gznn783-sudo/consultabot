import os
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")  # 🔥 evita //webhook

app = FastAPI()

# 🔥 Application é o centro do bot (NÃO use Bot separado)
telegram_app = ApplicationBuilder().token(TOKEN).build()


# =========================
# COMANDOS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🇧🇷 ConsultaBot Brasil online!")


async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = " ".join(context.args)
    await update.message.reply_text(f"🔎 Consulta: {nome}")


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("buscar", buscar))


# =========================
# STARTUP (WEBHOOK CORRETO)
# =========================
@app.on_event("startup")
async def startup():
    await telegram_app.initialize()

    webhook_url = f"{RENDER_URL}/webhook"

    await telegram_app.bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True
    )


# =========================
# WEBHOOK ENDPOINT
# =========================
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()

    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)

    return {"ok": True}


# =========================
# HEALTH CHECK
# =========================
@app.get("/")
def home():
    return {"status": "ConsultaBot Brasil online"}
