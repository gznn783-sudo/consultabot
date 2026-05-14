import os
import re
import requests
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

app = FastAPI()
telegram_app = Application.builder().token(BOT_TOKEN).build()


def extrair_numero(texto):
    achou = re.findall(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", texto)
    return achou[0] if achou else "Não localizado"


def detectar_tribunal(texto):
    tribunais = ["TJRS", "TJSP", "TJMG", "TJRJ", "TJPR", "TJSC", "TRF1", "TRF2", "TRF3", "TRF4", "TRF5", "TRT", "STJ", "STF"]
    for t in tribunais:
        if t.lower() in texto.lower():
            return t
    return "Não informado"


def buscar_processos(nome):
    if not SERPAPI_KEY:
        return "❌ SERPAPI_KEY não configurada no Render."

    url = "https://serpapi.com/search.json"

    params = {
        "engine": "google",
        "q": f'"{nome}" processo OR processos site:jusbrasil.com.br',
        "hl": "pt-br",
        "gl": "br",
        "api_key": SERPAPI_KEY
    }

    try:
        r = requests.get(url, params=params, timeout=20)
        data = r.json()

        resultados = data.get("organic_results", [])

        if not resultados:
            return "❌ Nenhum resultado encontrado pela SerpAPI."

        resposta = f"🔎 Resultados para: {nome}\n\n"

        for i, item in enumerate(resultados[:10], start=1):
            titulo = item.get("title", "Sem título")
            snippet = item.get("snippet", "")
            link = item.get("link", "")

            texto = f"{titulo} {snippet}"
            numero = extrair_numero(texto)
            tribunal = detectar_tribunal(texto)

            resposta += (
                f"{i}️⃣ 📁 Processo: {numero}\n"
                f"🏛 Tribunal: {tribunal}\n"
                f"📌 {titulo[:120]}\n"
                f"📝 {snippet[:250]}\n"
                f"🌐 {link}\n\n"
            )

        return resposta[:4000]

    except Exception as e:
        return f"❌ Erro na SerpAPI: {str(e)}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 ConsultaBot V7 Online\n\n"
        "Use:\n"
        "/buscar Nome Completo\n\n"
        "Ou envie apenas o nome."
    )


async def consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        nome = " ".join(context.args).strip()
    else:
        nome = update.message.text.strip()

    msg = await update.message.reply_text("🔎 Consultando...")

    resultado = buscar_processos(nome)

    await msg.edit_text(resultado)


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("buscar", consultar))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, consultar))


@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
def home():
    return {"status": "ConsultaBot V7 Online"}


@app.on_event("startup")
async def startup():
    await telegram_app.initialize()

    await telegram_app.bot.set_webhook(
        url=f"{RENDER_URL}/webhook",
        drop_pending_updates=True
    )


@app.on_event("shutdown")
async def shutdown():
    await telegram_app.shutdown()
