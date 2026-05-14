import os
import re
import time
import asyncio
import requests
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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

    print("CODILO AUTH STATUS:", response.status_code)
    print("CODILO AUTH RESPONSE:", response.text[:1000])

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

    print("CODILO AVAILABLE STATUS:", response.status_code)
    print("CODILO AVAILABLE RESPONSE:", response.text[:1000])

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


def extrair_queries_disponiveis(node, param_keys, ctx=None, saida=None):
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

            key = (
                param.get("tag")
                or param.get("key")
                or param.get("name")
                or param.get("param")
            )

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

    def score(item):
        search = str(item.get("search", "")).lower()

        if search in prioridade:
            return prioridade.index(search)

        return 999

    if filtradas:
        return sorted(filtradas, key=score)

    return consultas


def find_queries(param_keys, uf=None):
    available = get_available()
    consultas = extrair_queries_disponiveis(available, param_keys)

    unicas = []
    vistos = set()

    for c in consultas:
        chave = (
            c["source"],
            c["platform"],
            c["search"],
            c["query"],
            c["param_key"]
        )

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

    print("CODILO CREATE PAYLOAD:", payload)
    print("CODILO CREATE STATUS:", response.status_code)
    print("CODILO CREATE RESPONSE:", response.text[:1000])

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

    for _ in range(15):
        response = requests.get(
            url,
            headers=codilo_headers(),
            timeout=30
        )

        print("CODILO RESULT STATUS:", response.status_code)
        print("CODILO RESULT RESPONSE:", response.text[:1000])

        if response.status_code not in [200, 201]:
            raise Exception(f"Result {response.status_code}: {response.text[:500]}")

        data = response.json()

        status = (
            data.get("requested", {}).get("status")
            or data.get("data", {}).get("status")
            or data.get("status")
            or ""
        )

        status = str(status).lower()

        if status in ["success", "warning", "done", "finished", "completed"]:
            return data

        if status in ["error", "failed", "failure"]:
            raise Exception(f"Consulta falhou: {str(data)[:500]}")

        time.sleep(6)

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
        or pessoa.get("label")
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


def achar_cnjs_no_objeto(obj):
    texto = str(obj)
    return list(set(re.findall(CNJ_REGEX, texto)))


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
        f"Advogado: {pessoas['advogado']}\n"
    )


def executar_busca(valor, tipo):
    uf = None

    if tipo == "nomeadv":
        param_keys = ["nomeadv", "nomeadvogado", "advogado"]

    elif tipo == "oab":
        param_keys = ["oab"]
        uf = extrair_uf_oab(valor)

    elif tipo == "nomeparte":
        param_keys = ["nomeparte", "nome"]

    else:
        return "❌ Tipo de busca inválido."

    try:
        consultas = find_queries(param_keys, uf=uf)
    except Exception as e:
        return f"❌ Erro ao buscar abrangência da Codilo:\n{str(e)}"

    if not consultas:
        return "❌ Nenhuma rota disponível para esse tipo de busca."

    processos_unicos = {}
    erros = []

    for item in consultas:
        try:
            request_id = create_request(item, valor)
            resultado = get_request_result(request_id)

            processos = extrair_lista_processos(resultado)

            for processo in processos:
                if not isinstance(processo, dict):
                    continue

                props = processo.get("properties") or processo

                numero = (
                    props.get("number")
                    or props.get("cnj")
                    or props.get("numero")
                    or props.get("numeroProcesso")
                    or props.get("processo")
                    or props.get("processNumber")
                )

                if not numero:
                    encontrados = achar_cnjs_no_objeto(processo)
                    numero = encontrados[0] if encontrados else None

                if numero:
                    processos_unicos[numero] = {
                        "processo": processo,
                        "tribunal": item.get("search", "Não informado")
                    }

            cnjs_texto = achar_cnjs_no_objeto(resultado)

            for cnj in cnjs_texto:
                if cnj not in processos_unicos:
                    processos_unicos[cnj] = {
                        "processo": {
                            "properties": {"number": cnj},
                            "people": []
                        },
                        "tribunal": item.get("search", "Não informado")
                    }

        except Exception as e:
            erros.append(
                f"{item.get('search')}/{item.get('query')}/{item.get('param_key')}: {str(e)[:180]}"
            )
            continue

    if not processos_unicos:
        erro_exemplo = "\n".join(erros[:8]) if erros else "Sem erro detalhado."

        return (
            "❌ Nenhum processo encontrado.\n\n"
            f"UF detectada: {uf or 'Não detectada'}\n"
            f"Consultas tentadas: {len(consultas)}\n"
            f"Falhas/sem retorno: {len(erros)}\n\n"
            f"Primeiros erros:\n{erro_exemplo}"
        )

    resposta = (
        f"🔎 Resultados encontrados: {len(processos_unicos)}\n"
        f"UF detectada: {uf or 'Não aplicada'}\n"
        f"Consultas realizadas: {len(consultas)}\n\n"
    )

    for i, (cnj, item) in enumerate(processos_unicos.items(), start=1):
        resposta += f"========== {i} ==========\n"
        resposta += formatar_processo(
            item["processo"],
            item["tribunal"],
            cnj_forcado=cnj
        )
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
        "/oab 123636--RS\n"
        "/nomeparte Nome da Parte\n"
        "/mapacodilo\n"
        "/debugcodilo"
    )


async def mapacodilo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        available = get_available()

        tags_alvo = [
            "oab",
            "nomeadv",
            "nomeadvogado",
            "advogado",
            "nomeparte",
            "nome",
            "doc",
            "cpf",
            "cnpj",
            "cnj",
            "numero",
            "numeroProcesso",
            "processo"
        ]

        consultas = extrair_queries_disponiveis(available, tags_alvo)

        if not consultas:
            await update.message.reply_text("❌ Nenhuma combinação encontrada no /available.")
            return

        linhas = []

        for c in consultas:
            linhas.append(
                f"{c['search']}/{c['query']}/{c['param_key']} | platform={c['platform']} | source={c['source']}"
            )

        texto = "📌 Combinações disponíveis:\n\n" + "\n".join(linhas[:100])

        await update.message.reply_text(texto[:4000])

    except Exception as e:
        await update.message.reply_text(f"❌ Erro no mapa Codilo:\n{str(e)}")


async def debugcodilo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        token = get_codilo_token()
        available = get_available()

        nomeadv_q = find_queries(["nomeadv", "nomeadvogado", "advogado"])
        oab_q = find_queries(["oab"])
        oab_rs_q = find_queries(["oab"], uf="RS")
        parte_q = find_queries(["nomeparte", "nome"])
        cnj_q = find_queries(["cnj", "numero", "numeroProcesso", "processo"])

        await update.message.reply_text(
            "✅ Codilo conectada.\n\n"
            f"Token: {'OK' if token else 'Falhou'}\n"
            f"Abrangência recebida: {len(available)} itens\n"
            f"Rotas nomeadv: {len(nomeadv_q)}\n"
            f"Rotas OAB geral: {len(oab_q)}\n"
            f"Rotas OAB RS: {len(oab_rs_q)}\n"
            f"Rotas nomeparte: {len(parte_q)}\n"
            f"Rotas CNJ: {len(cnj_q)}"
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Debug Codilo erro:\n{str(e)}")


async def nomeadv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valor = " ".join(context.args).strip()

    if not valor:
        await update.message.reply_text("Use: /nomeadv Nome do Advogado")
        return

    msg = await update.message.reply_text("🔎 Consultando advogado nas rotas disponíveis...")
    resultado = await asyncio.to_thread(executar_busca, valor, "nomeadv")
    await msg.edit_text(resultado)


async def oab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valor = " ".join(context.args).strip()

    if not valor:
        await update.message.reply_text("Use: /oab 123636--RS")
        return

    msg = await update.message.reply_text("🔎 Consultando OAB com filtro de UF...")
    resultado = await asyncio.to_thread(executar_busca, valor, "oab")
    await msg.edit_text(resultado)


async def nomeparte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valor = " ".join(context.args).strip()

    if not valor:
        await update.message.reply_text("Use: /nomeparte Nome da Parte")
        return

    msg = await update.message.reply_text("🔎 Consultando parte nas rotas disponíveis...")
    resultado = await asyncio.to_thread(executar_busca, valor, "nomeparte")
    await msg.edit_text(resultado)


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("mapacodilo", mapacodilo))
telegram_app.add_handler(CommandHandler("debugcodilo", debugcodilo))
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
