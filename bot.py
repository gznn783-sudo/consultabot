import os
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL")  # ex: https://consultabot.onrender.com

bot = Bot(token=TOKEN)
app = FastAPI()

telegram_app = ApplicationBuilder().token(TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🇧🇷 ConsultaBot Brasil online!")

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = " ".join(context.args)
    await update.message.reply_text(f"🔎 Consulta: {nome}")

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("buscar", buscar))

@app.on_event("startup")
async def startup():
    await telegram_app.initialize()
    await bot.set_webhook(url=f"{RENDER_URL}/webhook")

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, bot)
    await telegram_app.process_update(update)
    return {"ok": True}

@app.get("/")
def home():
    return {"status": "ConsultaBot Brasil online"}
