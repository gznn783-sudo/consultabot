import os
import re
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL")

app = FastAPI()

telegram_app = Application.builder().token(TOKEN).build()


# =========================
# START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔎 ConsultaBot V4 PROFISSIONAL ONLINE\n\n"
        "Envie o nome completo da pessoa para consultar processos."
    )


# =========================
# CONSULTA
# =========================
async def consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = update.message.text.strip()

    msg = await update.message.reply_text("🔍 Consultando processos reais...")

    resultado = buscar_processos(nome)

    await msg.edit_text(resultado)


# =========================
# BUSCA REAL SEM PLAYWRIGHT
# =========================
def buscar_processos(nome):
    try:
        busca = nome.replace(" ", "+")

        url = f"https://www.jusbrasil.com.br/busca?q={busca}"

        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code != 200:
            return "❌ Falha ao acessar JusBrasil."

        html = response.text

        soup = BeautifulSoup(html, "html.parser")

        texto = soup.get_text(" ", strip=True)

        processos = re.findall(
            r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}',
            texto
        )

        tribunais = re.findall(
            r'TJSP|TJMG|TJRS|TJPR|TJSC|TRF1|TRF2|TRF3|TRF4|TRF5|TRT',
            texto
        )

        if not processos:
            return f"❌ Nenhum processo encontrado para {nome}"

        resposta = f"🔎 Resultados para: {nome}\n\n"

        usados = set()

        contador = 1

        for proc in processos:
            if proc in usados:
                continue

            usados.add(proc)

            tribunal = (
                tribunais[contador - 1]
                if len(tribunais) >= contador
                else "Não informado"
            )

            resposta += (
                f"{contador}️⃣ Processo:\n"
                f"{proc}\n"
                f"🏛 Tribunal: {tribunal}\n\n"
            )

            contador += 1

            if contador > 10:
                break

        return resposta

    except Exception as e:
        return f"❌ Erro na consulta: {str(e)}"


# =========================
# WEBHOOK
# =========================
@app.post("/")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


# =========================
# STATUS
# =========================
@app.get("/")
async def home():
    return {"status": "ConsultaBot V4 PROFISSIONAL ONLINE"}


# =========================
# STARTUP
# =========================
@app.on_event("startup")
async def startup():
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, consultar)
    )

    await telegram_app.initialize()

    await telegram_app.bot.set_webhook(url=RENDER_URL)


# =========================
# SHUTDOWN
# =========================
@app.on_event("shutdown")
async def shutdown():
    await telegram_app.shutdown()
