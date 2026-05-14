import os
import re
import time
import asyncio
import requests
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")

CODILO_KEY = os.getenv("CODILO_KEY")
CODILO_SECRET = os.getenv("CODILO_SECRET")

MAX_CODILO_REQUESTS = int(os.getenv("MAX_CODILO_REQUESTS", "80"))

AUTH_URL = "https://auth.codilo.com.br/oauth/token"
AVAILABLE_URL = "https://api.consulta.codilo.com.br/v1/available"
REQUEST_URL = "https://api.consulta.codilo.com.br/v1/request"

app = FastAPI()
telegram_app = Application.builder().token(BOT_TOKEN).build()

TOKEN_CACHE = {"access_token": None, "expires_at": 0}
AVAILABLE_CACHE = {"data": None, "expires_at": 0}
USER_RESULTS = {}

CNJ_REGEX = r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"

UF_TRIBUNAIS = {
    "RS": ["tjrs", "trf4", "trt4"],
    "SC": ["tjsc", "trf4", "trt12"],
    "GO": ["tjgo", "trf1", "trt18"],
    "TO": ["tjto", "trf1"],
    "DF": ["tjdft", "trf1", "trt10"],
    "MG": ["tjmg", "trf1", "trt3"],
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

    response.raise_for_status()
    data = response.json()

    token = data.get("access_token")
    expires_in = int(float(data.get("expires_in", 3600)))

    TOKEN_CACHE["access_token"] = token
    TOKEN_CACHE["expires_at"] = now + expires_in - 60

    return token


def codilo_headers():
    return {
        "Authorization": f"Bearer {get_codilo_token()}",
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

    response.raise_for_status()

    data = response.json().get("data", [])

    AVAILABLE_CACHE["data"] = data
    AVAILABLE_CACHE["expires_at"] = now + 3600

    return data


def extrair_uf_oab(valor):
    valor = valor.strip().upper()

    match = re.search(r"--([A-Z]{2})$", valor)
    if match:
        return match.group(1)

    match = re.search(r"-\d-([A-Z]{2})$", valor)
    if match:
        return match.group(1)

    return None


def extrair_ano_cnj(cnj):
    match = re.search(r"\.(\d{4})\.", cnj)
    return match.group(1) if match else "Sem ano"


def achar_cnjs_no_objeto(obj):
    return list(set(re.findall(CNJ_REGEX, str(obj))))


def extrair_queries_disponiveis(node, param_keys, ctx=None, saida=None):
    if ctx is None:
        ctx = {"source": "courts", "platform": None, "search": None, "query": None}

    if saida is None:
        saida = []

    if isinstance(node, list):
        for item in node:
            extrair_queries_disponiveis(item, param_keys, ctx.copy(), saida)
        return saida

    if not isinstance(node, dict):
        return saida

    novo_ctx = ctx.copy()

    for campo in ["source", "platform", "search", "query"]:
        if node.get(campo):
            novo_ctx[campo] = node.get(campo)

    params = node.get("params") or node.get("parameters") or []

    if isinstance(params, dict):
        params = [params]

    if params and novo_ctx.get("platform") and novo_ctx.get("search") and novo_ctx.get("query"):
        for param in params:
            if not isinstance(param, dict):
                continue

            key = param.get("tag") or param.get("key") or param.get("name") or param.get("param")

            if key in param_keys:
                saida.append({
                    "source": novo_ctx.get("source") or "courts",
                    "platform": novo_ctx["platform"],
                    "search": novo_ctx["search"],
                    "query": novo_ctx["query"],
                    "param_key": key
                })

    for key, value in node.items():
        if key in ["params", "parameters"]:
            continue

        if isinstance(value, (dict, list)):
            extrair_queries_disponiveis(value, param_keys, novo_ctx.copy(), saida)

    return saida


def ordenar_por_uf(consultas, uf=None):
    if not uf:
        return consultas

    prioridade = UF_TRIBUNAIS.get(uf.upper(), [])

    filtradas = [
        c for c in consultas
        if str(c.get("search", "")).lower() in prioridade
    ]

    if not filtradas:
        return consultas

    return sorted(
        filtradas,
        key=lambda item: prioridade.index(str(item.get("search", "")).lower())
        if str(item.get("search", "")).lower() in prioridade else 999
    )


def find_queries(param_keys, uf=None):
    available = get_available()
    consultas = extrair_queries_disponiveis(available, param_keys)

    unicas = []
    vistos = set()

    for c in consultas:
        chave = (c["source"], c["platform"], c["search"], c["query"], c["param_key"])

        if chave not in vistos:
            vistos.add(chave)
            unicas.append(c)

    unicas = ordenar_por_uf(unicas, uf)

    return unicas[:MAX_CODILO_REQUESTS]


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

    if response.status_code not in [200, 201]:
        raise Exception(f"Create {response.status_code}: {response.text[:500]}")

    data = response.json()

    request_id = (
        data.get("data", {}).get("id")
        or data.get("requestId")
        or data.get("id")
    )

    if not request_id:
        raise Exception(f"Sem request id: {data}")

    return request_id


def get_request_result(request_id):
    url = f"{REQUEST_URL}/{request_id}"
    ultimo_texto = ""

    for _ in range(15):
        try:
            response = requests.get(
                url,
                headers=codilo_headers(),
                timeout=30
            )

            ultimo_texto = response.text

            if response.status_code in [200, 201]:
                try:
                    data = response.json()
                except Exception:
                    data = {"raw_text": response.text}

                status = (
                    data.get("requested", {}).get("status")
                    or data.get("data", {}).get("status")
                    or data.get("status")
                    or ""
                )

                status = str(status).lower()

                if status in ["success", "warning", "done", "finished", "completed"]:
                    return data

                cnjs = achar_cnjs_no_objeto(data)
                if cnjs:
                    return {"success": True, "fallback_cnjs": cnjs}

            else:
                cnjs = achar_cnjs_no_objeto(response.text)
                if cnjs:
                    return {"success": True, "fallback_cnjs": cnjs}

        except Exception:
            cnjs = achar_cnjs_no_objeto(ultimo_texto)
            if cnjs:
                return {"success": True, "fallback_cnjs": cnjs}

        time.sleep(4)

    cnjs = achar_cnjs_no_objeto(ultimo_texto)

    if cnjs:
        return {"success": True, "fallback_cnjs": cnjs}

    return {"success": False, "data": []}


def buscar_cnjs(valor, tipo):
    uf = None

    if tipo == "oab":
        param_keys = ["oab"]
        uf = extrair_uf_oab(valor)
    elif tipo == "nomeadv":
        param_keys = ["nomeadv", "nomeadvogado", "advogado"]
    elif tipo == "nomeparte":
        param_keys = ["nomeparte", "nome"]
    else:
        return {}, uf, []

    consultas = find_queries(param_keys, uf=uf)

    processos_por_ano = {}
    erros = []

    for item in consultas:
        try:
            request_id = create_request(item, valor)
            resultado = get_request_result(request_id)

            cnjs = resultado.get("fallback_cnjs", []) if isinstance(resultado, dict) else []
            if not cnjs:
                cnjs = achar_cnjs_no_objeto(resultado)

            for cnj in cnjs:
                ano = extrair_ano_cnj(cnj)

                processos_por_ano.setdefault(ano, {})
                processos_por_ano[ano][cnj] = {
                    "cnj": cnj,
                    "tribunal": item.get("search", "Não informado")
                }

        except Exception as e:
            erros.append(f"{item.get('search')}/{item.get('query')}: {str(e)[:150]}")
            continue

    return processos_por_ano, uf, erros


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot V6 Online\n\n"
        "Comandos:\n"
        "/oab 123636--RS\n"
        "/nomeadv Nome do Advogado\n"
        "/nomeparte Nome da Parte\n\n"
        "Depois da busca, escolha o ano pelos botões."
    )


async def executar_busca_com_botoes(update: Update, context: ContextTypes.DEFAULT_TYPE, tipo: str):
    valor = " ".join(context.args).strip()

    if not valor:
        await update.message.reply_text("Digite o termo da busca.")
        return

    msg = await update.message.reply_text("🔎 Buscando processos e separando por ano...")

    try:
        processos_por_ano, uf, erros = await asyncio.to_thread(buscar_cnjs, valor, tipo)
    except Exception as e:
        await msg.edit_text(f"❌ Erro na busca:\n{str(e)}")
        return

    if not processos_por_ano:
        erro_exemplo = "\n".join(erros[:6]) if erros else "Sem erro detalhado."

        await msg.edit_text(
            "❌ Nenhum processo encontrado.\n\n"
            f"UF detectada: {uf or 'Não detectada'}\n"
            f"Falhas/sem retorno: {len(erros)}\n\n"
            f"Primeiros erros:\n{erro_exemplo}"
        )
        return

    user_id = update.effective_user.id

    USER_RESULTS[user_id] = {
        "valor": valor,
        "tipo": tipo,
        "uf": uf,
        "processos_por_ano": processos_por_ano,
        "created_at": time.time()
    }

    anos = sorted(processos_por_ano.keys(), reverse=True)

    keyboard = []

    for ano in anos:
        qtd = len(processos_por_ano[ano])
        keyboard.append([
            InlineKeyboardButton(
                f"📂 Processos {ano} ({qtd})",
                callback_data=f"abrir_ano:{ano}"
            )
        ])

    total = sum(len(v) for v in processos_por_ano.values())

    texto = (
        f"✅ Busca concluída.\n\n"
        f"Resultados encontrados: {total}\n"
        f"UF detectada: {uf or 'Não aplicada'}\n\n"
        f"Escolha o ano abaixo para ver os processos:"
    )

    await msg.edit_text(
        texto,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def oab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await executar_busca_com_botoes(update, context, "oab")


async def nomeadv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await executar_busca_com_botoes(update, context, "nomeadv")


async def nomeparte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await executar_busca_com_botoes(update, context, "nomeparte")


async def callback_ano(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Abrindo pasta...")

    user_id = query.from_user.id
    data = query.data

    if not data.startswith("abrir_ano:"):
        return

    ano = data.split(":", 1)[1]

    session = USER_RESULTS.get(user_id)

    if not session:
        await query.edit_message_text("❌ Busca expirada. Faça uma nova consulta.")
        return

    processos_ano = session["processos_por_ano"].get(ano, {})

    if not processos_ano:
        await query.edit_message_text("❌ Nenhum processo encontrado para esse ano.")
        return

    itens = list(processos_ano.values())

    resposta = (
        f"📂 Processos do ano {ano}\n"
        f"Total encontrado: {len(itens)}\n\n"
    )

    for i, item in enumerate(itens, start=1):
        cnj = item["cnj"]
        tribunal = item.get("tribunal", "Não informado")

        resposta += (
            f"========== {i} ==========\n"
            f"Prezado Cliente!\n\n"
            f"Autor: Não informado\n\n"
            f"CPF: Não informado\n\n"
            f"Réu: Não informado\n\n"
            f"Assunto: Não informado\n\n"
            f"Tribunal: {tribunal} - Não informado\n\n"
            f"Nº do processo: {cnj}\n\n"
            f"Valor da causa: Não informado\n\n"
            f"Advogado: Não informado\n\n"
        )

        if len(resposta) > 3800:
            resposta += "\n⚠️ Resultado cortado pelo limite do Telegram."
            break

    await query.edit_message_text(resposta[:4000])


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("oab", oab))
telegram_app.add_handler(CommandHandler("nomeadv", nomeadv))
telegram_app.add_handler(CommandHandler("nomeparte", nomeparte))
telegram_app.add_handler(CallbackQueryHandler(callback_ano, pattern=r"^abrir_ano:"))


@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
def home():
    return {"status": "ConsultaBot V6 Online"}


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
