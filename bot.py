# ===============================
# CONSULTABOT BRASIL V5 PROFISSIONAL
# Render + Telegram + Anti Bloqueio JusBrasil
# ===============================

import os
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import uvicorn
import asyncio
import random

# ===============================
# CONFIG
# ===============================

TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL")

app = FastAPI()

# ===============================
# TELEGRAM APP
# ===============================

telegram_app = Application.builder().token(TOKEN).build()

# ===============================
# START
# ===============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot Brasil V5 Online\n\n"
        "Envie:\n"
        "• Nome completo\n"
        "• CPF\n"
        "• Processo\n"
    )

telegram_app.add_handler(CommandHandler("start", start))

# ===============================
# USER AGENTS
# ===============================

USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
    "Mozilla/5.0 (Linux; Android 13)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
]

# ===============================
# CONSULTA JUSBRASIL
# ===============================

def consultar_jusbrasil(texto):
    url = f"https://www.jusbrasil.com.br/busca?q={texto}"

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Referer": "https://www.google.com/",
        "Accept": "text/html",
    }

    try:
        r = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        if r.status_code != 200:
            return "❌ JusBrasil bloqueou acesso."

        soup = BeautifulSoup(r.text, "html.parser")

        textos = soup.get_text("\n", strip=True)

        if "processo" in textos.lower():
            return textos[:3500]

        return "❌ Nenhum resultado encontrado."

    except Exception as e:
        return f"❌ Erro consulta: {str(e)}"

# ===============================
# MSG
# ===============================

async def receber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()

    await update.message.reply_text("🔎 Consultando...")

    resultado = await asyncio.to_thread(
        consultar_jusbrasil,
        texto
    )

    await update.message.reply_text(resultado)

telegram_app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, receber)
)

# ===============================
# WEBHOOK
# ===============================

@app.on_event("startup")
async def startup():
    await telegram_app.initialize()
    await telegram_app.start()

    if RENDER_URL:
        await telegram_app.bot.set_webhook(
            url=f"{RENDER_URL}/webhook"
        )

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()

    update = Update.de_json(
        data,
        telegram_app.bot
    )

    await telegram_app.process_update(update)

    return {"ok": True}

@app.get("/")
def home():
    return {
        "status": "ConsultaBot Brasil V5 Online"
    }

# ===============================
# MAIN
# ===============================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )
