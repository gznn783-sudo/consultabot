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
MAX_CODILO_REQUESTS = int(os.getenv("MAX_CODILO_REQUESTS", "15"))

AUTH_URL = "https://auth.codilo.com.br/oauth/token"
AVAILABLE_URL = "https://api.consulta.codilo.com.br/v1/available"
REQUEST_URL = "https://api.consulta.codilo.com.br/v1/request"

app = FastAPI()
telegram_app = Application.builder().token(BOT_TOKEN).build()

TOKEN_CACHE = {"access_token": None, "expires_at": 0}
AVAILABLE_CACHE = {"data": None, "expires_at": 0}

TRIBUNAIS_PERMITIDOS = {
    "tjrs", "trf4", "trt4",
    "tjsc", "trt12",
    "tjgo", "trt18", "trf1",
    "tjto",
    "tjdft", "trt10",
    "tjmg", "trt3"
}


def get_codilo_token():
    now = time.time()

    if TOKEN_CACHE["access_token"] and TOKEN_CACHE["expires_at"] > now:
        return TOKEN_CACHE["access_token"]

    payload = {
        "grant_type": "client_credentials",
        "id": CODILO_KEY,
        "secret": CODILO_SECRET
    }

    response = requests.post(
        AUTH_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30
    )

    print("CODILO AUTH STATUS:", response.status_code)
    print("CODILO AUTH RESPONSE:", response.text[:500])

    response.raise_for_status()

    data = response.json()

    access_token = data.get("access_token")
    expires_in = int(data.get("expires_in", 3600))

    if not access_token:
        raise Exception("Codilo não retornou access_token.")

    TOKEN_CACHE["access_token"] = access_token
    TOKEN_CACHE["expires_at"] = now + expires_in - 60

    return access_token


def codilo_headers():
    token = get_codilo_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accept": "*/*"
    }


def get_available():
    now = time.time()

    if AVAILABLE_CACHE["data"] and AVAILABLE_CACHE["expires_at"] > now:
        return AVAILABLE_CACHE["data"]

    response = requests.get(
        AVAILABLE_URL,
        headers=codilo_headers(),
        timeout=30
    )

    print("CODILO AVAILABLE STATUS:", response.status_code)
    print("CODILO AVAILABLE RESPONSE:", response.text[:500])

    response.raise_for_status()

    data = response.json().get("data", [])

    AVAILABLE_CACHE["data"] = data
    AVAILABLE_CACHE["expires_at"] = now + 3600

    return data


def find_queries(param_keys):
    available = get_available()
    results = []

    for source_item in available:
        source = source_item.get("source", "courts")

        for platform_item in source_item.get("platforms", []):
            platform = platform_item.get("platform")

            for search_item in platform_item.get("searches", []):
                search = search_item.get("search")

                if search and search.lower() not in TRIBUNAIS_PERMITIDOS:
                    continue

                for query_item in search_item.get("queries", []):
                    query = query_item.get("query")

                    for param in query_item.get("params", []):
                        tag = param.get("tag") or param.get("key")

                        if tag in param_keys:
                            results.append({
                                "source": source,
                                "platform": platform,
                                "search": search,
                                "query": query,
                                "param_key": tag
                            })

    return results[:MAX_CODILO_REQUESTS]


def create_request(item, value):
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

    response = requests.post(
        REQUEST_URL,
        headers=codilo_headers(),
        json=payload,
        timeout=30
    )

    print("CODILO CREATE STATUS:", response.status_code)
    print("CODILO CREATE RESPONSE:", response.text[:500])

    response.raise_for_status()

    data = response.json()

    return (
        data.get("requestId")
        or data.get("id")
        or data.get("data", {}).get("id")
    )


def get_request_result(request_id):
    url = f"{REQUEST_URL}/{request_id}"

    for _ in range(10):
        response = requests.get(
            url,
            headers=codilo_headers(),
            timeout=30
        )

        print("CODILO RESULT STATUS:", response.status_code)
        print("CODILO RESULT RESPONSE:", response.text[:500])

        response.raise_for_status()

        data = response.json()

        status = (
            data.get("status")
            or data.get("data", {}).get("status")
            or data.get("requested", {}).get("status")
            or ""
        )

        if status.lower() not in ["pending", "processing", "created"]:
            return data

        time.sleep(4)

    return {"success": False, "status": "timeout", "data": []}


def get_any(obj, keys, default="Não informado"):
    if not isinstance(obj, dict):
        return default

    for key in keys:
        value = obj.get(key)
        if value not in [None, "", [], {}]:
            return value

    return default


def normalizar_nome(pessoa):
    if isinstance(pessoa, str):
        return pessoa

    if not isinstance(pessoa, dict):
        return "Não informado"

    return (
        pessoa.get("name")
        or pessoa.get("nome")
        or pessoa.get("value")
        or pessoa.get("description")
        or "Não informado"
    )


def extrair_pessoas(processo):
    pessoas = (
        processo.get("people")
        or processo.get("partes")
        or processo.get("persons")
        or []
    )

    autores = []
    reus = []
    advogados = []

    for pessoa in pessoas:
        if not isinstance(pessoa, dict):
            continue

        nome = normalizar_nome(pessoa)

        tipo = " ".join([
            str(pessoa.get("type", "")),
            str(pessoa.get("role", "")),
            str(pessoa.get("side", "")),
            str(pessoa.get("qualifier", "")),
            str(pessoa.get("description", "")),
        ]).lower()

        if "adv" in tipo or "lawyer" in tipo:
            advogados.append(nome)
        elif "autor" in tipo or "requerente" in tipo or "exequente" in tipo or "active" in tipo:
            autores.append(nome)
        elif "réu" in tipo or "reu" in tipo or "requerido" in tipo or "executado" in tipo or "passive" in tipo:
            reus.append(nome)

        for adv in pessoa.get("lawyers", []) or pessoa.get("advogados", []):
            advogados.append(normalizar_nome(adv))

    return {
        "autor": autores[0] if autores else "Não informado",
        "reu": reus[0] if reus else "Não informado",
        "advogado": advogados[0] if advogados else "Não informado"
    }


def formatar_processo(processo, fallback_tribunal="Não informado"):
    props = processo.get("properties") or processo.get("capa") or processo
    pessoas = extrair_pessoas(processo)

    numero = get_any(props, ["number", "cnj", "numero", "numeroProcesso", "processo"])
    tribunal = get_any(props, ["court", "tribunal", "search"], fallback_tribunal)
    origem = get_any(props, ["origin", "origem", "foro", "comarca"], "Não informado")
    assunto = get_any(props, ["subject", "assunto", "area", "classe", "class"], "Não informado")
    valor = get_any(props, ["value", "valor", "valorCausa", "valor_da_causa"], "Não informado")

    return (
        f"Prezado Cliente!\n\n"
        f"Autor: {pessoas['autor']}\n\n"
        f"CPF: Não informado\n\n"
        f"Réu: {pessoas['reu']}\n\n"
        f"Assunto: {assunto}\n\n"
        f"Tribunal: {tribunal} - {origem}\n\n"
        f"Nº do processo: {numero}\n\n"
        f"Valor da causa: {valor}\n\n"
        f"Advogado: {pessoas['advogado']}\n"
    )


def executar_busca(valor, tipo):
    if tipo == "nomeadv":
        param_keys = ["nomeadv", "nomeadvogado"]
    elif tipo == "oab":
        param_keys = ["oab"]
    elif tipo == "nomeparte":
        param_keys = ["nomeparte", "nome"]
    else:
        return "❌ Tipo de busca inválido."

    try:
        consultas = find_queries(param_keys)
    except Exception as e:
        return f"❌ Erro ao buscar abrangência da Codilo:\n{str(e)}"

    if not consultas:
        return "❌ Nenhum tribunal filtrado disponível para esse tipo de busca."

    processos_unicos = {}
    falhas = 0

    for item in consultas:
        try:
            request_id = create_request(item, valor)

            if not request_id:
                falhas += 1
                continue

            resultado = get_request_result(request_id)

            data = resultado.get("data", [])

            if isinstance(data, dict):
                if "items" in data:
                    data = data.get("items", [])
                elif "processes" in data:
                    data = data.get("processes", [])
                elif "result" in data:
                    data = data.get("result", [])
                else:
                    data = [data]

            for processo in data:
                if not isinstance(processo, dict):
                    continue

                props = processo.get("properties") or processo

                numero = (
                    props.get("number")
                    or props.get("cnj")
                    or props.get("numero")
                    or props.get("numeroProcesso")
                    or props.get("processo")
                )

                if not numero:
                    continue

                if numero not in processos_unicos:
                    processos_unicos[numero] = {
                        "processo": processo,
                        "tribunal": item.get("search", "Não informado")
                    }

        except Exception as e:
            print("ERRO CONSULTA CODILO:", str(e))
            falhas += 1
            continue

    if not processos_unicos:
        return (
            "❌ Nenhum processo encontrado.\n\n"
            f"Consultas tentadas: {len(consultas)}\n"
            f"Falhas/sem retorno: {falhas}"
        )

    resposta = (
        f"🔎 Resultados encontrados: {len(processos_unicos)}\n"
        f"Consultas realizadas: {len(consultas)}\n\n"
    )

    for i, item in enumerate(processos_unicos.values(), start=1):
        resposta += f"========== {i} ==========\n"
        resposta += formatar_processo(item["processo"], item["tribunal"])
        resposta += "\n"

        if len(resposta) > 3800:
            resposta += "\n⚠️ Resultado cortado pelo limite do Telegram."
            break

    return resposta[:4000]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot Codilo Online\n\n"
        "Comandos:\n"
        "/nomeadv Nome do Advogado\n"
        "/oab 12345-RS\n"
        "/nomeparte Nome da Parte"
    )


async def nomeadv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valor = " ".join(context.args).strip()

    if not valor:
        await update.message.reply_text("Use: /nomeadv Nome do Advogado")
        return

    msg = await update.message.reply_text("🔎 Consultando advogado nos tribunais filtrados...")
    resultado = await asyncio.to_thread(executar_busca, valor, "nomeadv")
    await msg.edit_text(resultado)


async def oab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valor = " ".join(context.args).strip()

    if not valor:
        await update.message.reply_text("Use: /oab 12345-RS")
        return

    msg = await update.message.reply_text("🔎 Consultando OAB nos tribunais filtrados...")
    resultado = await asyncio.to_thread(executar_busca, valor, "oab")
    await msg.edit_text(resultado)


async def nomeparte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valor = " ".join(context.args).strip()

    if not valor:
        await update.message.reply_text("Use: /nomeparte Nome da Parte")
        return

    msg = await update.message.reply_text("🔎 Consultando parte nos tribunais filtrados...")
    resultado = await asyncio.to_thread(executar_busca, valor, "nomeparte")
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
