import os
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from consulta import buscar_processos_nome, ordenar_por_data

app = FastAPI()

telegram_app = None


# =========================
# COMANDOS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🇧🇷 ConsultaBot Brasil online!")


async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome_busca = " ".join(context.args)

    processos = buscar_processos_nome(nome_busca)
    processos = ordenar_por_data(processos)

    resposta = f"🔎 {nome_busca}\n\n"

    for p in processos[:10]:
        resposta += f"{p.get('numero')} - {p.get('assunto')}\n"

    await update.message.reply_text(resposta[:4000])


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
    return {"status": "ok"}


# =========================
# STARTUP (ABSOLUTAMENTE SEGURO)
# =========================
@app.on_event("startup")
async def startup():
    global telegram_app

    try:
        token = os.getenv("TOKEN")
        url = os.getenv("RENDER_URL", "").rstrip("/")

        print("TOKEN:", bool(token))
        print("URL:", url)

        if not token:
            raise Exception("TOKEN não definido")

        if not url:
            raise Exception("RENDER_URL não definido")

        telegram_app = ApplicationBuilder().token(token).build()

        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(CommandHandler("buscar", buscar))

        await telegram_app.initialize()

        await telegram_app.bot.set_webhook(
            url=f"{url}/webhook",
            drop_pending_updates=True
        )

        print("BOT ONLINE")

    except Exception as e:
        print("🔥 ERRO REAL NO STARTUP:", str(e))
        raise
