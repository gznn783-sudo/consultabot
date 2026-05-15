import os
import re
import time
import json
import asyncio
import requests

from fastapi import FastAPI, Request
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")

CODILO_KEY = os.getenv("CODILO_KEY")
CODILO_SECRET = os.getenv("CODILO_SECRET")

AUTH_URL = "https://auth.codilo.com.br/oauth/token"
AVAILABLE_URL = "https://api.consulta.codilo.com.br/v1/available"
REQUEST_URL = "https://api.consulta.codilo.com.br/v1/request"
AUTOREQUEST_URL = "https://api.consulta.codilo.com.br/v1/autorequest"

app = FastAPI()

telegram_app = (
    Application.builder()
    .token(BOT_TOKEN)
    .build()
)

TOKEN_CACHE = {
    "access_token": None,
    "expires_at": 0
}

USER_RESULTS = {}
DETAIL_CACHE = {}

PAGE_SIZE = 8

CNJ_REGEX = r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"


def get_codilo_token():
    now = time.time()

    if (
        TOKEN_CACHE["access_token"]
        and TOKEN_CACHE["expires_at"] > now
    ):
        return TOKEN_CACHE["access_token"]

    payload = {
        "grant_type": "client_credentials",
        "id": CODILO_KEY,
        "secret": CODILO_SECRET
    }

    response = requests.post(
        AUTH_URL,
        json=payload,
        headers={
            "Content-Type": "application/json"
        },
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    token = data.get("access_token")

    expires_in = int(
        float(data.get("expires_in", 3600))
    )

    TOKEN_CACHE["access_token"] = token
    TOKEN_CACHE["expires_at"] = (
        now + expires_in - 60
    )

    return token


def codilo_headers():
    return {
        "Authorization": f"Bearer {get_codilo_token()}",
        "Content-Type": "application/json",
        "accept": "*/*"
    }


def achar_cnjs_no_objeto(obj):
    return list(
        set(
            re.findall(
                CNJ_REGEX,
                str(obj)
            )
        )
    )


def extrair_ano_cnj(cnj):
    match = re.search(
        r"\.(\d{4})\.",
        cnj
    )

    return match.group(1) if match else "Sem ano"


def get_available():
    response = requests.get(
        AVAILABLE_URL,
        headers=codilo_headers(),
        timeout=60
    )

    response.raise_for_status()

    return response.json().get("data", [])


def extrair_queries_disponiveis(
    node,
    param_keys,
    ctx=None,
    saida=None
):
    if ctx is None:
        ctx = {
            "source": "courts",
            "platform": None,
            "search": None,
            "query": None
        }

    if saida is None:
        saida = []

    if isinstance(node, list):
        for item in node:
            extrair_queries_disponiveis(
                item,
                param_keys,
                ctx.copy(),
                saida
            )

        return saida

    if not isinstance(node, dict):
        return saida

    novo_ctx = ctx.copy()

    for campo in [
        "source",
        "platform",
        "search",
        "query"
    ]:
        if node.get(campo):
            novo_ctx[campo] = node.get(campo)

    params = (
        node.get("params")
        or node.get("parameters")
        or []
    )

    if isinstance(params, dict):
        params = [params]

    if (
        params
        and novo_ctx.get("platform")
        and novo_ctx.get("search")
        and novo_ctx.get("query")
    ):
        for param in params:
            key = (
                param.get("tag")
                or param.get("key")
                or param.get("name")
            )

            if key in param_keys:
                saida.append({
                    "source": novo_ctx["source"],
                    "platform": novo_ctx["platform"],
                    "search": novo_ctx["search"],
                    "query": novo_ctx["query"],
                    "param_key": key
                })

    for key, value in node.items():
        if isinstance(value, (dict, list)):
            extrair_queries_disponiveis(
                value,
                param_keys,
                novo_ctx.copy(),
                saida
            )

    return saida


def find_queries(param_keys):
    available = get_available()

    consultas = extrair_queries_disponiveis(
        available,
        param_keys
    )

    unicas = []
    vistos = set()

    for item in consultas:
        chave = (
            item["platform"],
            item["search"],
            item["query"],
            item["param_key"]
        )

        if chave not in vistos:
            vistos.add(chave)
            unicas.append(item)

    return unicas[:10]


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
        timeout=60
    )

    if response.status_code not in [200, 201]:
        raise Exception(
            f"Erro create request: {response.text}"
        )

    data = response.json()

    request_id = (
        data.get("data", {}).get("id")
        or data.get("id")
    )

    return request_id


def get_request_result(request_id):
    url = f"{REQUEST_URL}/{request_id}"

    ultimo = {}

    for tentativa in range(10):
        try:
            response = requests.get(
                url,
                headers=codilo_headers(),
                timeout=60
            )

            try:
                resultado = response.json()
            except Exception:
                resultado = {}

            ultimo = resultado

            print(
                json.dumps(
                    resultado,
                    indent=2,
                    ensure_ascii=False
                )[:30000]
            )

            status = (
                resultado.get("status")
                or resultado.get("data", {}).get("status")
                or resultado.get("requested", {}).get("status")
                or ""
            )

            status = str(status).lower()

            cnjs = achar_cnjs_no_objeto(
                resultado
            )

            if cnjs:
                return {
                    "success": True,
                    "fallback_cnjs": cnjs,
                    "raw": resultado
                }

            if status in [
                "pending",
                "processing",
                "running",
                "waiting"
            ]:
                time.sleep(8)
                continue

            time.sleep(5)

        except Exception as e:
            print("ERRO:", str(e))
            time.sleep(5)

    return {
        "success": False,
        "raw": ultimo
    }


def buscar_cnjs(valor, tipo):
    if tipo == "oab":
        param_keys = ["oab"]
    elif tipo == "nomeadv":
        param_keys = [
            "nomeadv",
            "nomeadvogado",
            "advogado"
        ]
    else:
        param_keys = [
            "nomeparte",
            "nome"
        ]

    consultas = find_queries(param_keys)

    processos_por_ano = {}

    for item in consultas:
        try:
            request_id = create_request(
                item,
                valor
            )

            resultado = get_request_result(
                request_id
            )

            cnjs = resultado.get(
                "fallback_cnjs",
                []
            )

            for cnj in cnjs:
                ano = extrair_ano_cnj(cnj)

                processos_por_ano.setdefault(
                    ano,
                    {}
                )

                processos_por_ano[ano][cnj] = {
                    "cnj": cnj,
                    "tribunal": item["search"]
                }

        except Exception as e:
            print("ERRO BUSCA:", str(e))

    return processos_por_ano


def formatar_capa_minima(cnj, tribunal):
    ano = extrair_ano_cnj(cnj)

    return (
        f"Prezado Cliente!\n\n"
        f"Autor: Não retornado pela API\n\n"
        f"Réu: Não retornado pela API\n\n"
        f"Tribunal: {tribunal}\n\n"
        f"Nº processo: {cnj}\n\n"
        f"Ano: {ano}\n\n"
        f"⚠️ API não retornou capa completa."
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot Online\n\n"
        "/oab 123456--RS\n"
        "/nomeadv Nome\n"
        "/nomeparte Nome"
    )


async def executar_busca(
    update,
    context,
    tipo
):
    valor = " ".join(
        context.args
    ).strip()

    if not valor:
        await update.message.reply_text(
            "Digite um valor."
        )
        return

    msg = await update.message.reply_text(
        "🔎 Buscando processos..."
    )

    processos_por_ano = await asyncio.to_thread(
        buscar_cnjs,
        valor,
        tipo
    )

    if not processos_por_ano:
        await msg.edit_text(
            "❌ Nenhum processo encontrado."
        )
        return

    USER_RESULTS[
        update.effective_user.id
    ] = processos_por_ano

    keyboard = []

    anos = sorted(
        processos_por_ano.keys(),
        reverse=True
    )

    for ano in anos:
        qtd = len(
            processos_por_ano[ano]
        )

        keyboard.append([
            InlineKeyboardButton(
                f"📂 {ano} ({qtd})",
                callback_data=f"ano:{ano}"
            )
        ])

    total = sum(
        len(v)
        for v in processos_por_ano.values()
    )

    await msg.edit_text(
        (
            f"✅ Encontrados {total} processos.\n\n"
            f"Escolha o ano:"
        ),
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def oab(
    update,
    context
):
    await executar_busca(
        update,
        context,
        "oab"
    )


async def nomeadv(
    update,
    context
):
    await executar_busca(
        update,
        context,
        "nomeadv"
    )


async def nomeparte(
    update,
    context
):
    await executar_busca(
        update,
        context,
        "nomeparte"
    )


async def callback_ano(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    ano = query.data.split(":")[1]

    processos = USER_RESULTS.get(
        query.from_user.id,
        {}
    ).get(ano, {})

    keyboard = []

    for item in list(processos.values())[:PAGE_SIZE]:
        keyboard.append([
            InlineKeyboardButton(
                item["cnj"],
                callback_data=(
                    f"det:{item['cnj']}:{item['tribunal']}"
                )
            )
        ])

    await query.edit_message_text(
        f"📂 Processos {ano}",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def callback_detalhe(
    update,
    context
):
    query = update.callback_query

    await query.answer()

    partes = query.data.split(":")

    cnj = partes[1]
    tribunal = partes[2]

    resposta = formatar_capa_minima(
        cnj,
        tribunal
    )

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=resposta[:4000]
    )


telegram_app.add_handler(
    CommandHandler(
        "start",
        start
    )
)

telegram_app.add_handler(
    CommandHandler(
        "oab",
        oab
    )
)

telegram_app.add_handler(
    CommandHandler(
        "nomeadv",
        nomeadv
    )
)

telegram_app.add_handler(
    CommandHandler(
        "nomeparte",
        nomeparte
    )
)

telegram_app.add_handler(
    CallbackQueryHandler(
        callback_ano,
        pattern=r"^ano:"
    )
)

telegram_app.add_handler(
    CallbackQueryHandler(
        callback_detalhe,
        pattern=r"^det:"
    )
)


@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()

    update = Update.de_json(
        data,
        telegram_app.bot
    )

    await telegram_app.process_update(
        update
    )

    return {"ok": True}


@app.get("/")
def home():
    return {
        "status": "ConsultaBot Online"
    }


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
