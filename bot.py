import os
import requests
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL")

CODILO_KEY = os.getenv("CODILO_KEY")
CODILO_SECRET = os.getenv("CODILO_SECRET")

MAX_CODILO_REQUESTS = int(os.getenv("MAX_CODILO_REQUESTS", "15"))

app = FastAPI()

telegram_app = Application.builder().token(BOT_TOKEN).build()

# =========================================================
# TRIBUNAIS FILTRADOS
# =========================================================

TRIBUNAIS = [
    "tjrs",
    "trf4",
    "trt4",

    "tjsc",
    "trt12",

    "tjgo",
    "trt18",
    "trf1",

    "tjto",

    "tjdft",
    "trt10",

    "tjmg",
    "trt3"
]

# =========================================================
# TOKEN CODILO
# =========================================================

def gerar_token():

    url = "https://api.capturaweb.com.br/oauth/token"

    payload = {
        "grant_type": "client_credentials",
        "client_id": CODILO_KEY,
        "client_secret": CODILO_SECRET
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = requests.post(
        url,
        data=payload,
        headers=headers,
        timeout=30
    )

    data = response.json()

    return data.get("access_token")

# =========================================================
# CONSULTA ADVOGADO
# =========================================================

def consultar_advogado(nome_advogado):

    token = gerar_token()

    if not token:
        return ["❌ Erro ao autenticar na Codilo."]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    resultados = []

    contador = 0

    for tribunal in TRIBUNAIS:

        if contador >= MAX_CODILO_REQUESTS:
            break

        contador += 1

        try:

            url = "https://api.capturaweb.com.br/api/consultas/processos"

            payload = {
                "nome": nome_advogado,
                "tribunal": tribunal
            }

            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=40
            )

            if response.status_code != 200:
                continue

            data = response.json()

            processos = data.get("processos", [])

            for p in processos[:5]:

                numero = p.get("numero_processo", "Não informado")
                assunto = p.get("assunto", "Não informado")
                tribunal_nome = p.get("tribunal", tribunal.upper())

                autor = p.get("autor", "Não informado")
                reu = p.get("reu", "Não informado")
                advogado = p.get("advogado", nome_advogado)

                texto = f"""
📌 *PROCESSO ENCONTRADO*

*Autor:* {autor}

*Réu:* {reu}

*Assunto:* {assunto}

*Tribunal:* {tribunal_nome}

*Nº do processo:* `{numero}`

*Advogado:* {advogado}
"""

                resultados.append(texto)

        except:
            continue

    if not resultados:
        return ["❌ Nenhum processo encontrado."]

    return resultados

# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    texto = """
🤖 *CONSULTABOT V6 BLACK EDITION*

Comandos:

/nomeadv Nome do advogado
"""

    await update.message.reply_text(
        texto,
        parse_mode="Markdown"
    )

# =========================================================
# CONSULTA ADVOGADO
# =========================================================

async def nomeadv(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:

        await update.message.reply_text(
            "❌ Digite o nome do advogado."
        )

        return

    nome = " ".join(context.args)

    await update.message.reply_text(
        "🔎 Consultando advogado nos tribunais filtrados..."
    )

    resultados = consultar_advogado(nome)

    resposta = "\n━━━━━━━━━━━━━━━\n".join(resultados[:20])

    await update.message.reply_text(
        resposta,
        parse_mode="Markdown"
    )

# =========================================================
# WEBHOOK
# =========================================================

@app.post("/")
async def webhook(req: Request):

    data = await req.json()

    update = Update.de_json(data, telegram_app.bot)

    await telegram_app.process_update(update)

    return {"status": "ok"}

# =========================================================
# HEALTHCHECK
# =========================================================

@app.get("/")
async def home():
    return {"status": "ConsultaBot Brasil online"}

# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
async def startup():

    await telegram_app.initialize()

    webhook_url = f"{RENDER_URL}/"

    await telegram_app.bot.set_webhook(webhook_url)

# =========================================================
# SHUTDOWN
# =========================================================

@app.on_event("shutdown")
async def shutdown():

    await telegram_app.shutdown()

# =========================================================
# HANDLERS
# =========================================================

telegram_app.add_handler(
    CommandHandler("start", start)
)

telegram_app.add_handler(
    CommandHandler("nomeadv", nomeadv)
)
