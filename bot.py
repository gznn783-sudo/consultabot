import os
import re
import requests
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")

app = FastAPI()
telegram_app = ApplicationBuilder().token(TOKEN).build()

# =========================
# BUSCA WEB
# =========================
def extrair_numero_processo(texto):
    padrao = r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}'
    achou = re.search(padrao, texto)
    return achou.group(0) if achou else "Não identificado"

def buscar_jusbrasil_google(nome):
    query = f'{nome} site:jusbrasil.com.br/processos'
    url = f"https://www.bing.com/search?q={quote_plus(query)}"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    resultados = []

    try:
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        blocos = soup.select("li.b_algo")

        for item in blocos[:5]:
            a = item.select_one("h2 a")
            if not a:
                continue

            titulo = a.get_text(" ", strip=True)
            link = a.get("href")

            resumo = ""
            p = item.select_one("p")
            if p:
                resumo = p.get_text(" ", strip=True)

            numero = extrair_numero_processo(titulo + " " + resumo)

            resultados.append({
                "titulo": titulo,
                "link": link,
                "numero": numero
            })

        return resultados

    except Exception as e:
        print("ERRO BUSCA:", e)
        return []

# =========================
# COMANDOS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot Brasil online!\n\n"
        "Use:\n"
        "/buscar Nome Sobrenome"
    )

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = " ".join(context.args).strip()

    if not nome:
        await update.message.reply_text("Digite um nome.\nEx: /buscar João Silva")
        return

    await update.message.reply_text(f"🔎 Consultando: {nome}")

    resultados = buscar_jusbrasil_google(nome)

    if not resultados:
        await update.message.reply_text("❌ Nenhum processo encontrado.")
        return

    resposta = f"🔎 Resultados para: {nome}\n\n"

    for i, item in enumerate(resultados, start=1):
        resposta += (
            f"{i}. 📁 Processo: {item['numero']}\n"
            f"📌 {item['titulo'][:90]}\n"
            f"🔗 {item['link']}\n\n"
        )

    await update.message.reply_text(resposta[:4000])

# =========================
# HANDLERS
# =========================
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("buscar", buscar))

# =========================
# STARTUP
# =========================
@app.on_event("startup")
async def startup():
    await telegram_app.initialize()

    if TOKEN and RENDER_URL:
        webhook_url = f"{RENDER_URL}/webhook"

        await telegram_app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True
        )

        print("✅ Webhook ativo:", webhook_url)

# =========================
# WEBHOOK
# =========================
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

# =========================
# HOME
# =========================
@app.get("/")
def home():
    return {"status": "ConsultaBot Brasil online"}
