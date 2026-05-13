import os
import requests
from fastapi import FastAPI, Request

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

app = FastAPI()
telegram_app = ApplicationBuilder().token(TOKEN).build()

# =========================
# CONSULTA SERPAPI
# =========================
def buscar_processos(nome):
    url = "https://serpapi.com/search.json"

    params = {
        "engine": "google",
        "q": f'"{nome}" processo jusbrasil',
        "hl": "pt-br",
        "gl": "br",
        "api_key": SERPAPI_KEY
    }

    try:
        r = requests.get(url, params=params, timeout=20)
        data = r.json()

        resultados = []

        for item in data.get("organic_results", [])[:10]:
            resultados.append({
                "titulo": item.get("title", "Sem título"),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", "")
            })

        return resultados

    except:
        return []

# =========================
# COMANDOS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot Brasil PRO Online\n\n"
        "Use:\n"
        "/buscar Nome Sobrenome"
    )

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = " ".join(context.args).strip()

    if not nome:
        await update.message.reply_text("Digite um nome.")
        return

    await update.message.reply_text(f"🔎 Consultando {nome}...")

    dados = buscar_processos(nome)

    if not dados:
        await update.message.reply_text("❌ Nenhum processo localizado.")
        return

    msg = f"🔎 Resultados para {nome}\n\n"

    for i, item in enumerate(dados, 1):
        msg += (
            f"{i}. 📁 {item['titulo']}\n"
            f"📝 {item['snippet']}\n"
            f"🌐 {item['link']}\n\n"
        )

    await update.message.reply_text(msg[:4000])

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("buscar", buscar))

# =========================
# STARTUP
# =========================
@app.on_event("startup")
async def startup():
    await telegram_app.initialize()

    if TOKEN and RENDER_URL:
        await telegram_app.bot.set_webhook(
            url=f"{RENDER_URL}/webhook",
            drop_pending_updates=True
        )

# =========================
# WEBHOOK
# =========================
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

@app.get("/")
def home():
    return {"status": "ConsultaBot PRO online"}
