import os
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from consulta import buscar_processos_nome, ordenar_por_data


# =========================
# CONFIG
# =========================
TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")

app = FastAPI()

telegram_app = None


# =========================
# COMANDOS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🇧🇷 ConsultaBot Brasil online!")


async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome_busca = " ".join(context.args)

    if not nome_busca:
        await update.message.reply_text("Digite um nome. Ex: /buscar João Silva")
        return

    processos = buscar_processos_nome(nome_busca)
    processos = ordenar_por_data(processos)

    if not processos:
        await update.message.reply_text("Nenhum processo encontrado.")
        return

    resposta = f"🔎 Processos de: {nome_busca}\n\n"

    for p in processos[:10]:
        resposta += (
            f"📁 Processo: {p.get('numero')}\n"
            f"🏛 Tribunal: {p.get('tribunal')}\n"
            f"⚖ Classe: {p.get('classe')}\n"
            f"📌 Assunto: {p.get('assunto')}\n"
            f"📅 Data: {p.get('dataAjuizamento') or 'Não informada'}\n"
            f"------------------------\n"
        )

    await update.message.reply_text(resposta[:4000])


async def nome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome_busca = " ".join(context.args)

    if not nome_busca:
        await update.message.reply_text("Digite um nome. Ex: /nome João Silva")
        return

    processos = buscar_processos_nome(nome_busca)
    processos = ordenar_por_data(processos)

    if not processos:
        await update.message.reply_text("Nenhum processo encontrado.")
        return

    resposta = f"🔎 Processos de: {nome_busca}\n\n"

    for p in processos[:10]:
        resposta += (
            f"📁 Processo: {p.get('numero')}\n"
            f"🏛 Tribunal: {p.get('tribunal')}\n"
            f"⚖ Classe: {p.get('classe')}\n"
            f"📌 Assunto: {p.get('assunto')}\n"
            f"📅 Data: {p.get('dataAjuizamento') or 'Não informada'}\n"
            f"------------------------\n"
        )

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


# =========================
# HOME
# =========================
@app.get("/")
def home():
    return {"status": "ConsultaBot Brasil online"}


# =========================
# STARTUP (CORRIGIDO E SEGURO)
# =========================
@app.on_event("startup")
async def startup():
    global telegram_app

    try:
        if not TOKEN:
            print("❌ TOKEN não definido no Render")
            return

        if not RENDER_URL:
            print("❌ RENDER_URL não definido no Render")
            return

        telegram_app = ApplicationBuilder().token(TOKEN).build()

        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(CommandHandler("buscar", buscar))
        telegram_app.add_handler(CommandHandler("nome", nome))

        await telegram_app.initialize()

        webhook_url = f"{RENDER_URL}/webhook"

        await telegram_app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True
        )

        print("✅ BOT ONLINE COM SUCESSO")

    except Exception as e:
        print("❌ ERRO NO STARTUP:", str(e))
