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
AUTOREQUEST_URL = "https://api.consulta.codilo.com.br/v1/autorequest"

app = FastAPI()
telegram_app = Application.builder().token(BOT_TOKEN).build()

TOKEN_CACHE = {"access_token": None, "expires_at": 0}
AVAILABLE_CACHE = {"data": None, "expires_at": 0}
USER_RESULTS = {}
AUTO_CACHE = {}

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

    if not token:
        raise Exception(f"Codilo não retornou access_token: {data}")

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

    for _ in range(12):
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
                    return {"success": True, "fallback_cnjs": cnjs, "raw": data}

            else:
                cnjs = achar_cnjs_no_objeto(response.text)
                if cnjs:
                    return {"success": True, "fallback_cnjs": cnjs, "raw_text": response.text}

        except Exception:
            cnjs = achar_cnjs_no_objeto(ultimo_texto)
            if cnjs:
                return {"success": True, "fallback_cnjs": cnjs, "raw_text": ultimo_texto}

        time.sleep(4)

    cnjs = achar_cnjs_no_objeto(ultimo_texto)

    if cnjs:
        return {"success": True, "fallback_cnjs": cnjs, "raw_text": ultimo_texto}

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


def criar_autorequest(cnj):
    payload = {
        "key": "cnj",
        "value": cnj,
        "makeDownload": False,
        "callbacks": []
    }

    response = requests.post(
        AUTOREQUEST_URL,
        headers=codilo_headers(),
        json=payload,
        timeout=30
    )

    if response.status_code not in [200, 201]:
        raise Exception(f"AutoRequest {response.status_code}: {response.text[:500]}")

    data = response.json()

    auto_id = (
        data.get("data", {}).get("id")
        or data.get("id")
    )

    if not auto_id:
        raise Exception(f"AutoRequest sem ID: {data}")

    return auto_id


def consultar_autorequest(auto_id):
    url = f"{AUTOREQUEST_URL}/{auto_id}"

    for _ in range(5):
        response = requests.get(
            url,
            headers=codilo_headers(),
            timeout=30
        )

        if response.status_code not in [200, 201]:
            raise Exception(f"Show AutoRequest {response.status_code}: {response.text[:500]}")

        data = response.json()
        requests_list = data.get("data", {}).get("requests", [])

        success_requests = [
            r for r in requests_list
            if str(r.get("status", "")).lower() == "success"
        ]

        if success_requests:
            return success_requests, requests_list

        time.sleep(6)

    return [], requests_list if "requests_list" in locals() else []


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
        or pessoa.get("label")
        or pessoa.get("document")
        or "Não informado"
    )


def extrair_pessoas(processo):
    pessoas = (
        processo.get("people")
        or processo.get("partes")
        or processo.get("persons")
        or processo.get("parties")
        or processo.get("envolvidos")
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
            str(pessoa.get("kind", "")),
        ]).lower()

        if "adv" in tipo or "lawyer" in tipo:
            advogados.append(nome)
        elif "autor" in tipo or "requerente" in tipo or "exequente" in tipo or "active" in tipo or "parte ativa" in tipo:
            autores.append(nome)
        elif "réu" in tipo or "reu" in tipo or "requerido" in tipo or "executado" in tipo or "passive" in tipo or "parte passiva" in tipo:
            reus.append(nome)

        for adv in pessoa.get("lawyers", []) or pessoa.get("advogados", []):
            advogados.append(normalizar_nome(adv))

    return {
        "autor": autores[0] if autores else "Não informado",
        "reu": reus[0] if reus else "Não informado",
        "advogado": advogados[0] if advogados else "Não informado"
    }


def extrair_lista_processos(resultado):
    data = resultado.get("data", [])

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for chave in [
            "items",
            "processes",
            "processos",
            "result",
            "results",
            "lawsuits",
            "records"
        ]:
            if isinstance(data.get(chave), list):
                return data.get(chave)

        if data.get("properties") or data.get("people") or data.get("number"):
            return [data]

    return []


def formatar_processo(processo, fallback_tribunal="Não informado", cnj_forcado=None):
    props = processo.get("properties") or processo.get("capa") or processo.get("cover") or processo
    pessoas = extrair_pessoas(processo)

    numero = cnj_forcado or get_any(
        props,
        ["number", "cnj", "numero", "numeroProcesso", "processo", "processNumber"]
    )

    tribunal = get_any(
        props,
        ["court", "tribunal", "search", "tribunalNome"],
        fallback_tribunal
    )

    origem = get_any(
        props,
        ["origin", "origem", "foro", "comarca", "vara"],
        "Não informado"
    )

    assunto = get_any(
        props,
        ["subject", "assunto", "area", "classe", "class", "nature"],
        "Não informado"
    )

    valor = get_any(
        props,
        ["value", "valor", "valorCausa", "valor_da_causa", "claimValue"],
        "Não informado"
    )

    return (
        f"Prezado Cliente!\n\n"
        f"Autor: {pessoas['autor']}\n\n"
        f"CPF: Não informado\n\n"
        f"Réu: {pessoas['reu']}\n\n"
        f"Assunto: {assunto}\n\n"
        f"Tribunal: {tribunal} - {origem}\n\n"
        f"Nº do processo: {numero}\n\n"
        f"Valor da causa: {valor}\n\n"
        f"Advogado: {pessoas['advogado']}"
    )


def buscar_detalhes_autorequest(cnj):
    if cnj in AUTO_CACHE:
        auto_id = AUTO_CACHE[cnj]["auto_id"]
    else:
        auto_id = criar_autorequest(cnj)
        AUTO_CACHE[cnj] = {
            "auto_id": auto_id,
            "created_at": time.time()
        }

    success_requests, all_requests = consultar_autorequest(auto_id)

    if not success_requests:
        status_resumo = []

        for r in all_requests[:5]:
            status_resumo.append(
                f"{r.get('court') or r.get('search') or '?'}: {r.get('status') or '?'}"
            )

        resumo = "\n".join(status_resumo) if status_resumo else "Sem status interno retornado."

        return (
            f"⏳ A Codilo ainda não finalizou os dados completos.\n\n"
            f"Nº do processo: {cnj}\n"
            f"AutoRequest ID: {auto_id}\n\n"
            f"Status interno:\n{resumo}\n\n"
            f"Clique em 🔎 Ver detalhes novamente em 1 a 3 minutos.\n"
            f"Não criei nova consulta para esse CNJ."
        )

    ultimo_erro = None

    for req in success_requests:
        request_id = req.get("id")
        tribunal = req.get("court") or req.get("search") or "Não informado"

        if not request_id:
            continue

        try:
            resultado = get_request_result(request_id)
            processos = extrair_lista_processos(resultado)

            if processos:
                return formatar_processo(
                    processos[0],
                    fallback_tribunal=tribunal,
                    cnj_forcado=cnj
                )

            ultimo_erro = "Requisição success, mas sem dados de capa."

        except Exception as e:
            ultimo_erro = str(e)
            continue

    return (
        f"❌ Consulta finalizada, mas não retornou capa completa.\n\n"
        f"Nº do processo: {cnj}\n"
        f"AutoRequest ID: {auto_id}\n"
        f"Erro: {ultimo_erro or 'Não informado'}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot V6 Online\n\n"
        "Comandos:\n"
        "/oab 123636--RS\n"
        "/nomeadv Nome do Advogado\n"
        "/nomeparte Nome da Parte\n\n"
        "Depois escolha o ano e clique em 🔎 Ver detalhes."
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

    texto = (
        f"📂 Processos do ano {ano}\n"
        f"Total encontrado: {len(itens)}\n\n"
        "Clique em 🔎 Ver detalhes para consultar a capa completa:"
    )

    keyboard = []

    for i, item in enumerate(itens, start=1):
        cnj = item["cnj"]
        tribunal = item.get("tribunal", "Não informado")

        keyboard.append([
            InlineKeyboardButton(
                f"🔎 {i}. {cnj} | {tribunal}",
                callback_data=f"detalhe:{cnj}"
            )
        ])

    await query.edit_message_text(
        texto,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def callback_detalhe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Consultando detalhes...")

    data = query.data

    if not data.startswith("detalhe:"):
        return

    cnj = data.split(":", 1)[1]

    await query.edit_message_text(
        f"🔎 Consultando detalhes do processo:\n{cnj}\n\nAguarde..."
    )

    try:
        resposta = await asyncio.to_thread(buscar_detalhes_autorequest, cnj)
    except Exception as e:
        resposta = (
            f"❌ Erro ao buscar detalhes.\n\n"
            f"Nº do processo: {cnj}\n"
            f"Erro: {str(e)[:500]}"
        )

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=resposta[:4000]
    )


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("oab", oab))
telegram_app.add_handler(CommandHandler("nomeadv", nomeadv))
telegram_app.add_handler(CommandHandler("nomeparte", nomeparte))
telegram_app.add_handler(CallbackQueryHandler(callback_ano, pattern=r"^abrir_ano:"))
telegram_app.add_handler(CallbackQueryHandler(callback_detalhe, pattern=r"^detalhe:"))


@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
def home():
    return {"status": "ConsultaBot V6 AutoRequest Online"}


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
