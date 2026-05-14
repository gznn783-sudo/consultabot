import os
import time
import requests
import asyncio
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")
CODILO_KEY = os.getenv("CODILO_KEY")
CODILO_SECRET = os.getenv("CODILO_SECRET")
MAX_CODILO_REQUESTS = int(os.getenv("MAX_CODILO_REQUESTS", "999"))

AUTH_URL = "https://auth.codilo.com.br/oauth/token"
AVAILABLE_URL = "https://api.consulta.codilo.com.br/v1/available"
REQUEST_URL = "https://api.consulta.codilo.com.br/v1/request"

app = FastAPI()
telegram_app = Application.builder().token(BOT_TOKEN).build()

_token_cache = {"access_token": None, "expires_at": 0}
_available_cache = {"data": None, "expires_at": 0}


def get_codilo_token():
    now = time.time()

    if _token_cache["access_token"] and _token_cache["expires_at"] > now:
        return _token_cache["access_token"]

    payload = {
        "grant_type": "client_credentials",
        "id": CODILO_KEY,
        "secret": CODILO_SECRET
    }

    r = requests.post(AUTH_URL, json=payload, timeout=30)
    r.raise_for_status()

    data = r.json()
    token = data.get("access_token")
    expires_in = data.get("expires_in", 3600)

    _token_cache["access_token"] = token
    _token_cache["expires_at"] = now + expires_in - 60

    return token


def codilo_headers():
    token = get_codilo_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accept": "*/*"
    }


def get_available():
    now = time.time()

    if _available_cache["data"] and _available_cache["expires_at"] > now:
        return _available_cache["data"]

    r = requests.get(AVAILABLE_URL, headers=codilo_headers(), timeout=30)
    r.raise_for_status()

    data = r.json().get("data", [])

    _available_cache["data"] = data
    _available_cache["expires_at"] = now + 3600

    return data


def find_available_queries(param_keys):
    available = get_available()
    results = []

    for source_item in available:
        source = source_item.get("source", "courts")

        for platform_item in source_item.get("platforms", []):
            platform = platform_item.get("platform")

            for search_item in platform_item.get("searches", []):
                search = search_item.get("search")

                for query_item in search_item.get("queries", []):
                    query = query_item.get("query")

                    for param in query_item.get("params", []):
                        tag = param.get("tag")

                        if tag in param_keys:
                            results.append({
                                "source": source,
                                "platform": platform,
                                "search": search,
                                "query": query,
                                "param_key": tag
                            })

    return results


def create_codilo_request(item, value):
    payload = {
        "source": item["source"],
        "platform": item["platform"],
        "search": item["search"],
        "query": item["query"],
        "makeDownload": False,
        "param": {
            "key": item["param_key"],
            "value": value
        },
        "callbacks": []
    }

    r = requests.post(REQUEST_URL, json=payload, headers=codilo_headers(), timeout=30)
    r.raise_for_status()

    data = r.json()

    return (
        data.get("requestId")
        or data.get("id")
        or data.get("requested", {}).get("id")
        or data.get("data", {}).get("id")
    )


def get_request_result(request_id):
    url = f"{REQUEST_URL}/{request_id}"

    for _ in range(8):
        r = requests.get(url, headers=codilo_headers(), timeout=30)
        r.raise_for_status()

        data = r.json()
        status = (
            data.get("requested", {}).get("status")
            or data.get("status")
            or ""
        )

        if status in ["success", "warning", "error"]:
            return data

        time.sleep(4)

    return {"success": False, "status": "timeout", "data": []}


def get_any(obj, keys, default="Não informado"):
    for key in keys:
        value = obj.get(key)
        if value not in [None, "", [], {}]:
            return value
    return default


def normalizar_nome_pessoa(pessoa):
    return (
        pessoa.get("name")
        or pessoa.get("nome")
        or pessoa.get("value")
        or pessoa.get("description")
        or "Não informado"
    )


def extrair_pessoas(processo):
    pessoas = processo.get("people", []) or processo.get("partes", []) or []

    autores = []
    reus = []
    advogados = []

    for pessoa in pessoas:
        if not isinstance(pessoa, dict):
            continue

        nome = normalizar_nome_pessoa(pessoa)

        tipo = " ".join([
            str(pessoa.get("type", "")),
            str(pessoa.get("role", "")),
            str(pessoa.get("side", "")),
            str(pessoa.get("qualifier", "")),
            str(pessoa.get("description", "")),
        ]).lower()

        if "adv" in tipo or "advogado" in tipo or "lawyer" in tipo:
            advogados.append(nome)
        elif "autor" in tipo or "requerente" in tipo or "exequente" in tipo or "active" in tipo:
            autores.append(nome)
        elif "réu" in tipo or "reu" in tipo or "requerido" in tipo or "executado" in tipo or "passive" in tipo:
            reus.append(nome)

        for advogado in pessoa.get("lawyers", []) or pessoa.get("advogados", []):
            if isinstance(advogado, dict):
                advogados.append(normalizar_nome_pessoa(advogado))
            elif isinstance(advogado, str):
                advogados.append(advogado)

    return {
        "autor": autores[0] if autores else "Não informado",
        "reu": reus[0] if reus else "Não informado",
        "advogado": advogados[0] if advogados else "Não informado"
    }


def formatar_processo_cliente(processo, info=None):
    props = processo.get("properties", {}) or {}

    pessoas = extrair_pessoas(processo)

    numero = get_any(props, ["number", "cnj", "numero", "processo"])
    tribunal = (
        get_any(props, ["court", "tribunal"], "")
        or (info or {}).get("search")
        or "Não informado"
    )

    origem = get_any(props, ["origin", "origem", "foro", "comarca"], "")
    classe = get_any(props, ["class", "classe"], "")
    assunto = get_any(props, ["subject", "assunto", "area"], classe)
    valor = get_any(props, ["value", "valor", "valorCausa"], "Não informado")

    return (
        f"Prezado Cliente!\n\n"
        f"Autor: {pessoas['autor']}\n\n"
        f"CPF: Não informado\n\n"
        f"Réu: {pessoas['reu']}\n\n"
        f"Assunto: {assunto}\n\n"
        f"Tribunal: {tribunal} - {origem if origem else 'Não informado'}\n\n"
        f"Nº do processo: {numero}\n\n"
        f"Valor da causa: {valor}\n\n"
        f"Advogado: {pessoas['advogado']}\n"
    )


def executar_busca_codilo(valor, tipo):
    if tipo == "nomeadv":
        param_keys = ["nomeadv", "nomeadvogado"]
    elif tipo == "oab":
        param_keys = ["oab"]
    elif tipo == "nomeparte":
        param_keys = ["nomeparte", "nome"]
    else:
        return "❌ Tipo de busca inválido."

    disponiveis = find_available_queries(param_keys)

    if not disponiveis:
        return "❌ Nenhum tribunal disponível para esse tipo de busca."

    disponiveis = disponiveis[:MAX_CODILO_REQUESTS]

    processos_unicos = {}
    erros = 0

    for item in disponiveis:
        try:
            request_id = create_codilo_request(item, valor)

            if not request_id:
                erros += 1
                continue

            resultado = get_request_result(request_id)

            info = resultado.get("info", {})
            data = resultado.get("data", [])

            for processo in data:
                props = processo.get("properties", {}) or {}
                numero = (
                    props.get("number")
                    or props.get("cnj")
                    or props.get("numero")
                    or props.get("processo")
                )

                if not numero:
                    continue

                if numero not in processos_unicos:
                    processos_unicos[numero] = {
                        "processo": processo,
                        "info": info
                    }

        except Exception:
            erros += 1
            continue

    if not processos_unicos:
        return (
            f"❌ Nenhum processo encontrado.\n\n"
            f"Consultas tentadas: {len(disponiveis)}\n"
            f"Falhas/indisponíveis: {erros}"
        )

    resposta = (
        f"🔎 Resultados encontrados: {len(processos_unicos)}\n"
        f"Consultas realizadas: {len(disponiveis)}\n\n"
    )

    for i, item in enumerate(processos_unicos.values(), start=1):
        resposta += f"========== {i} ==========\n"
        resposta += formatar_processo_cliente(item["processo"], item["info"])
        resposta += "\n"

        if len(resposta) > 3800:
            resposta += "\n⚠️ Resultado cortado por limite do Telegram."
            break

    return resposta[:4000]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot Codilo Online\n\n"
        "Comandos:\n"
        "/nomeadv Nome do Advogado\n"
        "/oab 12345-RS\n"
        "/nomeparte Nome da Parte\n\n"
        "Exemplo:\n"
        "/nomeadv Pauline Raphaela Simao Gomes Taveira"
    )


async def nomeadv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valor = " ".join(context.args).strip()

    if not valor:
        await update.message.reply_text("Use: /nomeadv Nome do Advogado")
        return

    msg = await update.message.reply_text("🔎 Consultando advogado em todos os tribunais disponíveis...")

    resultado = await asyncio.to_thread(executar_busca_codilo, valor, "nomeadv")

    await msg.edit_text(resultado)


async def oab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valor = " ".join(context.args).strip()

    if not valor:
        await update.message.reply_text("Use: /oab 12345-RS")
        return

    msg = await update.message.reply_text("🔎 Consultando OAB em todos os tribunais disponíveis...")

    resultado = await asyncio.to_thread(executar_busca_codilo, valor, "oab")

    await msg.edit_text(resultado)


async def nomeparte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valor = " ".join(context.args).strip()

    if not valor:
        await update.message.reply_text("Use: /nomeparte Nome da Parte")
        return

    msg = await update.message.reply_text("🔎 Consultando parte em todos os tribunais disponíveis...")

    resultado = await asyncio.to_thread(executar_busca_codilo, valor, "nomeparte")

    await msg.edit_text(resultado)


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("nomeadv", nomeadv))
telegram_app.add_handler(CommandHandler("oab", oab))
telegram_app.add_handler(CommandHandler("nomeparte", nomeparte))


@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
def home():
    return {"status": "ConsultaBot Codilo Online"}


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
