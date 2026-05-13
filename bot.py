import os
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# 🔥 consulta jurídica
from consulta import buscar_processos_nome, ordenar_por_data


# =========================
# CONFIG
# =========================
TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")

app = FastAPI()

telegram_app = ApplicationBuilder().token(TOKEN).build()


# =========================
# START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🇧🇷 ConsultaBot Brasil online!")


# =========================
# BUSCAR (SIMPLIFICADO)
# =========================
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
        resposta += f"""
📁 Processo: {p.get('numero')}
🏛 Tribunal: {p.get('tribunal')}
⚖ Classe: {p.get('classe')}
📌 Assunto: {p.get('assunto')}
📅 Data: {p.get('dataAjuizamento') or 'Não informada'}
------------------------
"""

    await update.message.reply_text(resposta[:4000])


# =========================
# NOME (VERSÃO MAIS LIMPA E PADRÃO JUSBRASIL)
# =========================
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
# HANDLERS
# =========================
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("buscar", buscar))
telegram_app.add_handler(CommandHandler("nome", nome))


# =========================
# STARTUP (WEBHOOK)
# =========================
@app.on_event("startup")
async def startup():
    try:
        await telegram_app.initialize()

        token = os.getenv("TOKEN")
        url = os.getenv("RENDER_URL", "").rstrip("/")

        if not token:
            print("❌ TOKEN não definido")
            return

        if not url:
            print("❌ RENDER_URL não definido")
            return

        webhook_url = f"{url}/webhook"

        await telegram_app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True
        )

        print("✅ Bot iniciado com sucesso")

    except Exception as e:
        print("❌ ERRO NO STARTUP:", e)


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
# HEALTH CHECK
# =========================
@app.get("/")
def home():
    return {"status": "ConsultaBot Brasil online"}
