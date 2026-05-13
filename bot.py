import os
import re
import requests
from fastapi import FastAPI, Request
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")

app = FastAPI()
telegram_app = ApplicationBuilder().token(TOKEN).build()

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# =========================
# FUNÇÕES
# =========================
def limpar(txt):
    return re.sub(r"\s+", " ", txt).strip()

def numero_processo(txt):
    padrao = r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"
    achou = re.findall(padrao, txt)
    return achou[0] if achou else "Não localizado"

def tribunal(txt):
    lista = [
        "TJSP","TJRJ","TJMG","TJRS","TJPR","TJSC","TJBA","TJCE",
        "TRF1","TRF2","TRF3","TRF4","TRF5","STJ","STF","TST"
    ]
    for x in lista:
        if x.lower() in txt.lower():
            return x
    return "Não identificado"

def assunto(txt):
    lista = [
        "indenização","cobrança","trabalhista","divórcio",
        "aposentadoria","benefício","criminal","execução",
        "tributário","civil","consumidor","previdenciário"
    ]
    for x in lista:
        if x.lower() in txt.lower():
            return x.title()
    return "Não informado"

# =========================
# GOOGLE
# =========================
def buscar_google(nome):
    dados = []

    try:
        url = f"https://www.google.com/search?q={nome}+processo+jusbrasil"
        r = requests.get(url, headers=HEADERS, timeout=10)

        soup = BeautifulSoup(r.text, "html.parser")

        for item in soup.select("div.g")[:10]:
            texto = limpar(item.get_text(" ", strip=True))

            dados.append({
                "numero": numero_processo(texto),
                "tribunal": tribunal(texto),
                "assunto": assunto(texto),
                "fonte": "Google"
            })
    except:
        pass

    return dados

# =========================
# DUCKDUCKGO
# =========================
def buscar_duck(nome):
    dados = []

    try:
        url = f"https://duckduckgo.com/html/?q={nome}+processo"
        r = requests.get(url, headers=HEADERS, timeout=10)

        soup = BeautifulSoup(r.text, "html.parser")

        for item in soup.select(".result")[:10]:
            texto = limpar(item.get_text(" ", strip=True))

            dados.append({
                "numero": numero_processo(texto),
                "tribunal": tribunal(texto),
                "assunto": assunto(texto),
                "fonte": "DuckDuckGo"
            })
    except:
        pass

    return dados

def buscar_processos(nome):
    lista = buscar_google(nome) + buscar_duck(nome)

    unicos = []
    vistos = set()

    for item in lista:
        chave = item["numero"] + item["tribunal"]
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(item)

    return unicos[:10]

# =========================
# COMANDOS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot Brasil Online\n\n"
        "Use:\n"
        "/buscar Nome Sobrenome"
    )

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = " ".join(context.args).strip()

    if not nome:
        await update.message.reply_text("Digite um nome.\nEx: /buscar João Silva")
        return

    await update.message.reply_text(f"🔎 Consulta: {nome}")

    resultados = buscar_processos(nome)

    if not resultados:
        await update.message.reply_text("❌ Nenhum processo encontrado.")
        return

    msg = f"🔎 Resultados para {nome}\n\n"

    for i, p in enumerate(resultados, 1):
        msg += (
            f"{i}. 📁 Processo: {p['numero']}\n"
            f"🏛 Tribunal: {p['tribunal']}\n"
            f"⚖ Assunto: {p['assunto']}\n"
            f"🌐 Fonte: {p['fonte']}\n"
            f"------------------\n"
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

# =========================
# HOME
# =========================
@app.get("/")
def home():
    return {"status": "ConsultaBot Brasil online"}
