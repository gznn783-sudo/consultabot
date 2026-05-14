import os
import re
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

# =========================================
# CONFIG
# =========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL")

app = FastAPI()

telegram_app = Application.builder().token(BOT_TOKEN).build()

# =========================================
# HEADERS ANTI BLOQUEIO
# =========================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# =========================================
# START
# =========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "🔥 CONSULTABOT V6 BLACK EDITION 🔥\n\n"
        "✅ Consulta por nome\n"
        "✅ Busca processos públicos\n"
        "✅ Sistema online\n\n"
        "Envie o nome completo da pessoa."
    )

    await update.message.reply_text(texto)

# =========================================
# CONSULTA
# =========================================

async def consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):

    nome = update.message.text.strip()

    if len(nome) < 4:
        await update.message.reply_text(
            "❌ Digite um nome válido."
        )
        return

    mensagem = await update.message.reply_text(
        "🔎 Consultando processos..."
    )

    resultado = buscar_google_jusbrasil(nome)

    await mensagem.edit_text(resultado)

# =========================================
# GOOGLE + JUSBRASIL
# =========================================

def buscar_google_jusbrasil(nome):

    try:

        busca = nome.replace(" ", "+")

        url = (
            f"https://www.google.com/search?q="
            f"site%3Ajusbrasil.com.br+{busca}"
        )

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        if response.status_code != 200:
            return "❌ Google bloqueou a consulta."

        soup = BeautifulSoup(response.text, "lxml")

        html = soup.get_text(" ")

        links = re.findall(
            r'https://www\.jusbrasil\.com\.br[^\s]+',
            response.text
        )

        numeros = re.findall(
            r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}',
            html
        )

        tribunais = re.findall(
            r'TJSP|TJRS|TJMG|TRF1|TRF3|TRF4|TRT',
            html
        )

        resposta = (
            f"🔥 CONSULTABOT V6 🔥\n\n"
            f"👤 Nome: {nome}\n\n"
        )

        usados = set()

        contador = 1

        if numeros:

            for numero in numeros[:10]:

                if numero in usados:
                    continue

                usados.add(numero)

                tribunal = (
                    tribunais[contador - 1]
                    if len(tribunais) >= contador
                    else "Não informado"
                )

                resposta += (
                    f"{contador}️⃣ Processo:\n"
                    f"{numero}\n"
                    f"🏛 Tribunal: {tribunal}\n\n"
                )

                contador += 1

        if links:

            resposta += "🔗 Links encontrados:\n\n"

            for link in links[:5]:
                resposta += f"{link}\n\n"

        if not numeros and not links:
            return (
                "❌ Nenhum resultado encontrado.\n\n"
                "⚠️ O Google/JusBrasil pode ter limitado "
                "a busca temporariamente."
            )

        return resposta[:4000]

    except Exception as e:
        return f"❌ Erro na consulta:\n{str(e)}"

# =========================================
# WEBHOOK
# =========================================

@app.post("/")
async def webhook(request: Request):

    data = await request.json()

    update = Update.de_json(
        data,
        telegram_app.bot
    )

    await telegram_app.process_update(update)

    return {"ok": True}

# =========================================
# HOME
# =========================================

@app.get("/")
async def home():
    return {
        "status": "CONSULTABOT V6 BLACK EDITION ONLINE"
    }

# =========================================
# STARTUP
# =========================================

@app.on_event("startup")
async def startup():

    telegram_app.add_handler(
        CommandHandler("start", start)
    )

    telegram_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            consultar
        )
    )

    await telegram_app.initialize()

    await telegram_app.bot.set_webhook(
        url=RENDER_URL
    )

# =========================================
# SHUTDOWN
# =========================================

@app.on_event("shutdown")
async def shutdown():
    await telegram_app.shutdown()
