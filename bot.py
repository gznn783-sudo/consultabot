import os
import re
import asyncio
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from playwright.async_api import async_playwright

TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL")

app = FastAPI()

telegram_app = Application.builder().token(TOKEN).build()


# START
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔎 ConsultaBot V3 REAL Online\n\n"
        "Envie nome completo da pessoa para pesquisar processos."
    )


# CONSULTA
async def consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = update.message.text.strip()

    msg = await update.message.reply_text("🔍 Consultando processos...")

    resultado = await buscar_processos(nome)

    await msg.edit_text(resultado)


# PLAYWRIGHT
async def buscar_processos(nome):
    try:
        resultados = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox"]
            )

            page = await browser.new_page()

            busca = nome.replace(" ", "+")

            url = f"https://www.jusbrasil.com.br/busca?q={busca}"

            await page.goto(url, timeout=30000)

            await page.wait_for_timeout(4000)

            texto = await page.content()

            await browser.close()

        numeros = re.findall(r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}', texto)

        tribunais = re.findall(r'TJRS|TJSP|TJMG|TRF4|TRF1|TRF3|TRT', texto)

        if not numeros:
            return "❌ Nenhum processo encontrado."

        resposta = f"🔎 Resultados para {nome}\n\n"

        usados = set()

        for i, proc in enumerate(numeros[:10], start=1):
            if proc in usados:
                continue

            usados.add(proc)

            tribunal = tribunais[i-1] if len(tribunais) >= i else "Não informado"

            resposta += (
                f"{i}️⃣ Processo: {proc}\n"
                f"🏛 Tribunal: {tribunal}\n\n"
            )

        return resposta

    except Exception as e:
        return f"❌ Erro na consulta: {str(e)}"


# TELEGRAM ROUTE
@app.post("/")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
async def home():
    return {"status": "ConsultaBot V3 REAL online"}


# STARTUP
@app.on_event("startup")
async def startup():
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, consultar))

    await telegram_app.initialize()

    await telegram_app.bot.set_webhook(url=RENDER_URL)


@app.on_event("shutdown")
async def shutdown():
    await telegram_app.shutdown()
