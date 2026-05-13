import os
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


# =========================
# CONFIG
# =========================
TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")

if not TOKEN:
    raise Exception("TOKEN não definido")

if not RENDER_URL:
    raise Exception("RENDER_URL não definido")

app = FastAPI()

telegram_app = ApplicationBuilder().token(TOKEN).build()


# =========================
# CONSULTA CNJ (DATAJUD)
# =========================
def buscar_processos_nome(nome):
    url = "https://api-publica.datajud.cnj.jus.br/api_publica_processos/_search"

    payload = {
        "size": 20,
        "query": {
            "query_string": {
                "query": f'"{nome}"'
            }
        }
    }

    headers = {
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        data = r.json()

        processos = []

        for hit in data.get("hits", {}).get("hits", []):
            p = hit.get("_source", {})

            processos.append({
                "numero": p.get("numeroProcesso"),
                "tribunal": p.get("tribunal"),
                "classe": p.get("classeJudicial"),
                "assunto": p.get("assuntoPrincipal"),
                "partes": p.get("partes"),
                "dataAjuizamento": p.get("dataAjuizamento")
            })

        return processos

    except Exception as e:
        print("Erro DataJud:", e)
        return []


# =========================
# ORDENAÇÃO (MAIS RECENTE → MAIS ANTIGO)
# =========================
def ordenar_por_data(processos):
    def pegar_data(p):
        d = p.get("dataAjuizamento")
        try:
            if not d:
                return datetime.min
            return datetime.fromisoformat(d.replace("Z", ""))
        except:
            return datetime.min

    return sorted(processos, key=pegar_data, reverse=True)


# =========================
# COMANDOS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🇧🇷 ConsultaBot Brasil online!")


async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = " ".join(context.args)

    if not nome:
        await update.message.reply_text("Use: /buscar nome")
        return

    processos = buscar_processos_nome(nome)
    processos = ordenar_por_data(processos)

    if not processos:
        await update.message.reply_text("❌ Nenhum processo encontrado.")
        return

    resposta = f"🔎 Resultados para: {nome}\n\n"

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
        await update.message.reply_text("Use: /nome nome completo")
        return

    processos = buscar_processos_nome(nome_busca)
    processos = ordenar_por_data(processos)

    if not processos:
        await update.message.reply_text("❌ Nenhum processo encontrado.")
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
# STARTUP (WEBHOOK ESTÁVEL)
# =========================
@app.on_event("startup")
async def startup():
    try:
        await telegram_app.initialize()

        webhook_url = f"{RENDER_URL}/webhook"

        await telegram_app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True
        )

        print("✅ Bot iniciado:", webhook_url)

    except Exception as e:
        print("❌ Erro no startup:", e)


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
