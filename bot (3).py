import os
import re
import time
import asyncio
import json
import requests
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")
CODILO_KEY = os.getenv("CODILO_KEY")
CODILO_SECRET = os.getenv("CODILO_SECRET")

MAX_CODILO_REQUESTS = int(os.getenv("MAX_CODILO_REQUESTS", "100"))
REQUEST_POLL_ATTEMPTS = int(os.getenv("REQUEST_POLL_ATTEMPTS", "20"))
REQUEST_POLL_SLEEP = int(os.getenv("REQUEST_POLL_SLEEP", "3"))
AUTOREQUEST_ATTEMPTS = int(os.getenv("AUTOREQUEST_ATTEMPTS", "18"))
AUTOREQUEST_SLEEP = int(os.getenv("AUTOREQUEST_SLEEP", "10"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "8"))

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
DETAIL_CACHE = {}

CNJ_REGEX = r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"

UF_TRIBUNAIS = {
    "RS": ["tjrs", "trf4", "trt4", "tst", "stj", "stf"],
    "SC": ["tjsc", "trf4", "trt12", "tst", "stj", "stf"],
    "GO": ["tjgo", "trf1", "trt18", "tst", "stj", "stf"],
    "TO": ["tjto", "trf1", "tst", "stj", "stf"],
    "DF": ["tjdft", "trf1", "trt10", "tst", "stj", "stf"],
    "MG": ["tjmg", "trf1", "trt3", "tst", "stj", "stf"],
}


def get_codilo_token():
    now = time.time()
    if TOKEN_CACHE["access_token"] and TOKEN_CACHE["expires_at"] > now:
        return TOKEN_CACHE["access_token"]

    payload = {"grant_type": "client_credentials", "id": CODILO_KEY, "secret": CODILO_SECRET}
    response = requests.post(AUTH_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
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
    return {"Authorization": f"Bearer {get_codilo_token()}", "Content-Type": "application/json", "accept": "*/*"}


def get_available():
    now = time.time()
    if AVAILABLE_CACHE["data"] and AVAILABLE_CACHE["expires_at"] > now:
        return AVAILABLE_CACHE["data"]
    response = requests.get(AVAILABLE_URL, headers=codilo_headers(), timeout=40)
    response.raise_for_status()
    data = response.json().get("data", [])
    AVAILABLE_CACHE["data"] = data
    AVAILABLE_CACHE["expires_at"] = now + 3600
    return data


def normalizar_oab(valor):
    valor = valor.strip().upper().replace("/", " ").replace("_", "-")
    valor = re.sub(r"\s+", " ", valor)
    m = re.search(r"(\d+)\s*[-–—]{2}\s*([A-Z]{2})", valor)
    if m:
        return f"{m.group(1)}--{m.group(2)}"
    m = re.search(r"(\d+)\s*[-–—]\s*([A-Z0-9])\s*[-–—]\s*([A-Z]{2})", valor)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(\d+)\s+([A-Z]{2})$", valor)
    if m:
        return f"{m.group(1)}--{m.group(2)}"
    return valor.replace(" ", "")


def extrair_uf_oab(valor):
    valor = normalizar_oab(valor)
    m = re.search(r"--([A-Z]{2})$", valor)
    if m:
        return m.group(1)
    m = re.search(r"-\w-([A-Z]{2})$", valor)
    if m:
        return m.group(1)
    return None


def extrair_ano_cnj(cnj):
    m = re.search(r"\.(\d{4})\.", cnj)
    return m.group(1) if m else "Sem ano"


def achar_cnjs_no_objeto(obj):
    return sorted(set(re.findall(CNJ_REGEX, str(obj))))


def buscar_recursivo(obj, chaves):
    if isinstance(obj, dict):
        for chave in chaves:
            if chave in obj and obj[chave] not in [None, "", [], {}]:
                return obj[chave]
        for valor in obj.values():
            achado = buscar_recursivo(valor, chaves)
            if achado not in [None, "", [], {}]:
                return achado
    elif isinstance(obj, list):
        for item in obj:
            achado = buscar_recursivo(item, chaves)
            if achado not in [None, "", [], {}]:
                return achado
    return None


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
    if node.get("court") and not novo_ctx.get("search"):
        novo_ctx["search"] = node.get("court")

    params = node.get("params") or node.get("parameters") or []
    if isinstance(params, dict):
        params = [params]

    if params and novo_ctx.get("platform") and novo_ctx.get("search") and novo_ctx.get("query"):
        for param in params:
            if not isinstance(param, dict):
                continue
            key = param.get("tag") or param.get("key") or param.get("name") or param.get("param")
            if key and str(key).lower() in param_keys:
                saida.append({
                    "source": novo_ctx.get("source") or "courts",
                    "platform": novo_ctx["platform"],
                    "search": novo_ctx["search"],
                    "query": novo_ctx["query"],
                    "param_key": str(key).lower(),
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
    if not prioridade:
        return consultas
    prioritarios = []
    restantes = []
    for c in consultas:
        search = str(c.get("search", "")).lower()
        if search in prioridade:
            prioritarios.append(c)
        else:
            restantes.append(c)
    return sorted(prioritarios, key=lambda x: prioridade.index(str(x.get("search", "")).lower()) if str(x.get("search", "")).lower() in prioridade else 999) + restantes


def find_queries(param_keys, uf=None):
    available = get_available()
    consultas = extrair_queries_disponiveis(available, [k.lower() for k in param_keys])
    unicas, vistos = [], set()
    for c in consultas:
        chave = (c["source"], c["platform"], c["search"], c["query"], c["param_key"])
        if chave not in vistos:
            vistos.add(chave)
            unicas.append(c)
    return ordenar_por_uf(unicas, uf)[:MAX_CODILO_REQUESTS]


def create_request(item, value):
    payload = {
        "source": item["source"],
        "platform": item["platform"],
        "search": item["search"],
        "query": item["query"],
        "makeDownload": False,
        "param": {"key": item["param_key"], "value": value},
        "callbacks": [],
    }
    response = requests.post(REQUEST_URL, headers=codilo_headers(), json=payload, timeout=40)
    if response.status_code not in [200, 201]:
        raise Exception(f"Create {response.status_code}: {response.text[:500]}")
    data = response.json()
    request_id = data.get("data", {}).get("id") or data.get("requestId") or data.get("id")
    if not request_id:
        raise Exception(f"Sem request id: {data}")
    return request_id


def extrair_status(data):
    if not isinstance(data, dict):
        return ""
    return str(
        data.get("status")
        or data.get("data", {}).get("status")
        or data.get("requested", {}).get("status")
        or ""
    ).lower()


def get_request_result(request_id):
    url = f"{REQUEST_URL}/{request_id}"
    ultimo_texto = ""
    ultimo_json = {}

    for tentativa in range(REQUEST_POLL_ATTEMPTS):
        try:
            response = requests.get(url, headers=codilo_headers(), timeout=60)
            ultimo_texto = response.text
            try:
                resultado = response.json()
            except Exception:
                resultado = {"raw_text": response.text}
            ultimo_json = resultado

            cnjs = achar_cnjs_no_objeto(resultado)
            if cnjs:
                resultado["fallback_cnjs"] = cnjs
                resultado["success"] = True
                return resultado

            status = extrair_status(resultado)
            if status in ["success", "warning", "done", "finished", "completed"]:
                return resultado
            if status in ["pending", "processing", "running", "waiting", ""]:
                time.sleep(REQUEST_POLL_SLEEP)
                continue
            return resultado
        except Exception as e:
            cnjs = achar_cnjs_no_objeto(ultimo_texto)
            if cnjs:
                return {"success": True, "fallback_cnjs": cnjs, "raw_text": ultimo_texto}
            ultimo_json = {"success": False, "error": str(e)}
            time.sleep(REQUEST_POLL_SLEEP)

    cnjs = achar_cnjs_no_objeto(ultimo_texto)
    if cnjs:
        return {"success": True, "fallback_cnjs": cnjs, "raw_text": ultimo_texto}
    return ultimo_json or {"success": False, "data": []}


def buscar_cnjs(valor, tipo):
    uf = None
    if tipo == "oab":
        valor = normalizar_oab(valor)
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
                processos_por_ano[ano][cnj] = {"cnj": cnj, "tribunal": item.get("search", "Não informado")}
        except Exception as e:
            erros.append(f"{item.get('search')}/{item.get('query')}: {str(e)[:180]}")
            continue
    return processos_por_ano, uf, erros


def criar_autorequest(cnj):
    payload = {"key": "cnj", "value": cnj, "makeDownload": False, "callbacks": [], "format": "allRequests"}
    response = requests.post(AUTOREQUEST_URL, headers=codilo_headers(), json=payload, timeout=40)
    if response.status_code not in [200, 201]:
        raise Exception(f"AutoRequest {response.status_code}: {response.text[:500]}")
    data = response.json()
    auto_id = data.get("data", {}).get("id") or data.get("id")
    if not auto_id:
        raise Exception(f"AutoRequest sem ID: {data}")
    return auto_id


def consultar_autorequest(auto_id):
    url = f"{AUTOREQUEST_URL}/{auto_id}"
    last_data = {}
    all_requests = []
    for _ in range(AUTOREQUEST_ATTEMPTS):
        response = requests.get(url, headers=codilo_headers(), timeout=60)
        if response.status_code not in [200, 201]:
            raise Exception(f"Show AutoRequest {response.status_code}: {response.text[:500]}")
        data = response.json()
        last_data = data
        data_obj = data.get("data", {}) if isinstance(data, dict) else {}
        all_requests = data_obj.get("requests") or data_obj.get("result") or []
        if isinstance(all_requests, dict):
            all_requests = all_requests.get("requests", []) or all_requests.get("result", []) or []
        if not isinstance(all_requests, list):
            all_requests = []

        success_requests = [r for r in all_requests if str(r.get("status", "")).lower() == "success"]
        if success_requests:
            return success_requests, all_requests, last_data

        status = str(data_obj.get("status") or data.get("status") or "").lower()
        if status in ["success", "warning", "error"] and all_requests:
            return success_requests, all_requests, last_data
        time.sleep(AUTOREQUEST_SLEEP)
    return [], all_requests, last_data


def get_any(obj, keys, default="Não informado"):
    if not isinstance(obj, dict):
        return default
    for key in keys:
        value = obj.get(key)
        if value not in [None, "", [], {}]:
            return value
    recursive = buscar_recursivo(obj, keys)
    if recursive not in [None, "", [], {}]:
        if isinstance(recursive, list):
            return ", ".join(map(str, recursive[:3])) if recursive else default
        return recursive
    return default


def normalizar_nome(pessoa):
    if isinstance(pessoa, str):
        return pessoa
    if not isinstance(pessoa, dict):
        return "Não informado"
    return pessoa.get("name") or pessoa.get("nome") or pessoa.get("value") or pessoa.get("description") or pessoa.get("label") or pessoa.get("document") or "Não informado"


def extrair_pessoas(processo):
    pessoas = processo.get("people") or processo.get("partes") or processo.get("persons") or processo.get("parties") or processo.get("envolvidos") or []
    autores, reus, advogados = [], [], []
    for pessoa in pessoas:
        if not isinstance(pessoa, dict):
            continue
        nome = normalizar_nome(pessoa)
        tipo = " ".join(str(pessoa.get(k, "")) for k in ["type", "role", "side", "qualifier", "description", "kind", "pole"]).lower()
        if "adv" in tipo or "lawyer" in tipo:
            advogados.append(nome)
        elif any(x in tipo for x in ["autor", "requerente", "exequente", "active", "parte ativa"]):
            autores.append(nome)
        elif any(x in tipo for x in ["réu", "reu", "requerido", "executado", "passive", "parte passiva"]):
            reus.append(nome)
        for adv in pessoa.get("lawyers", []) or pessoa.get("advogados", []):
            advogados.append(normalizar_nome(adv))

    if not autores:
        autor_rec = buscar_recursivo(processo, ["autor", "author", "plaintiff", "claimant", "requerente"])
        if autor_rec:
            autores.append(normalizar_nome(autor_rec) if isinstance(autor_rec, dict) else str(autor_rec))
    if not reus:
        reu_rec = buscar_recursivo(processo, ["reu", "réu", "requerido", "defendant", "executado"])
        if reu_rec:
            reus.append(normalizar_nome(reu_rec) if isinstance(reu_rec, dict) else str(reu_rec))
    if not advogados:
        adv_rec = buscar_recursivo(processo, ["advogado", "advogados", "lawyer", "lawyers"])
        if adv_rec:
            if isinstance(adv_rec, list) and adv_rec:
                advogados.append(normalizar_nome(adv_rec[0]))
            else:
                advogados.append(normalizar_nome(adv_rec) if isinstance(adv_rec, dict) else str(adv_rec))
    return {"autor": autores[0] if autores else "Não informado", "reu": reus[0] if reus else "Não informado", "advogado": advogados[0] if advogados else "Não informado"}


def extrair_lista_processos(resultado):
    if not isinstance(resultado, dict):
        return []
    for path in [resultado, resultado.get("data", {}), resultado.get("data", {}).get("data", {}) if isinstance(resultado.get("data"), dict) else {}]:
        if isinstance(path, list):
            return path
        if isinstance(path, dict):
            for chave in ["items", "processes", "processos", "result", "results", "lawsuits", "records", "lawsuit", "process", "data"]:
                val = path.get(chave)
                if isinstance(val, list):
                    return val
                if isinstance(val, dict) and (val.get("properties") or val.get("people") or val.get("number") or val.get("cnj")):
                    return [val]
            if path.get("properties") or path.get("people") or path.get("number") or path.get("cnj"):
                return [path]
    return []


def processo_tem_capa(processo):
    if not isinstance(processo, dict):
        return False
    props = processo.get("properties") or processo.get("capa") or processo.get("cover") or processo
    return bool(props.get("number") or props.get("cnj") or props.get("numero") or props.get("class") or props.get("classe") or props.get("origin") or props.get("origem") or processo.get("people"))


def formatar_processo(processo, fallback_tribunal="Não informado", cnj_forcado=None):
    props = processo.get("properties") or processo.get("capa") or processo.get("cover") or processo
    pessoas = extrair_pessoas(processo)
    numero = cnj_forcado or get_any(props, ["number", "cnj", "numero", "numeroProcesso", "processo", "processNumber"])
    tribunal = get_any(props, ["court", "tribunal", "search", "tribunalNome", "forum"], fallback_tribunal)
    origem = get_any(props, ["origin", "origem", "foro", "comarca", "vara", "courtSection", "judgingBody"], "Não informado")
    assunto = get_any(props, ["subject", "assunto", "subjects", "area", "classe", "class", "nature", "matter"], "Não informado")
    valor = get_any(props, ["value", "valor", "valorCausa", "valor_da_causa", "claimValue", "amount"], "Não informado")
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
    if cnj in DETAIL_CACHE:
        return DETAIL_CACHE[cnj], None
    if cnj in AUTO_CACHE:
        auto_id = AUTO_CACHE[cnj]["auto_id"]
    else:
        auto_id = criar_autorequest(cnj)
        AUTO_CACHE[cnj] = {"auto_id": auto_id, "created_at": time.time()}

    success_requests, all_requests, auto_raw = consultar_autorequest(auto_id)

    for req in success_requests:
        request_id = req.get("id")
        tribunal = req.get("court") or req.get("search") or "Não informado"
        if not request_id:
            continue
        resultado = get_request_result(request_id)
        processos = extrair_lista_processos(resultado)
        for proc in processos:
            if processo_tem_capa(proc):
                resposta = formatar_processo(proc, fallback_tribunal=tribunal, cnj_forcado=cnj)
                DETAIL_CACHE[cnj] = resposta
                return resposta, None

    # fallback: às vezes a capa vem no próprio autorequest
    processos = extrair_lista_processos(auto_raw)
    for proc in processos:
        if processo_tem_capa(proc):
            resposta = formatar_processo(proc, fallback_tribunal="AutoRequest", cnj_forcado=cnj)
            DETAIL_CACHE[cnj] = resposta
            return resposta, None

    status_resumo = []
    for r in all_requests[:10]:
        status_resumo.append(f"{r.get('court') or r.get('search') or '?'}: {r.get('status') or '?'}")
    resumo = "\n".join(status_resumo) if status_resumo else "Sem status interno retornado."
    resposta = (
        f"⏳ Ainda não consegui montar a capa completa.\n\n"
        f"Nº do processo:\n{cnj}\n\n"
        f"AutoRequest ID:\n{auto_id}\n\n"
        f"Status interno:\n{resumo}\n\n"
        f"Clique no botão abaixo para tentar novamente."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Tentar novamente", callback_data=f"retrydet:{cnj}")]])
    return resposta, keyboard


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot V6 Online\n\n"
        "Comandos:\n"
        "/oab 123636--RS\n"
        "/oab 123636 RS\n"
        "/nomeadv Nome do Advogado\n"
        "/nomeparte Nome da Parte\n\n"
        "Depois escolha o ano e clique em 🔎 Ver detalhes."
    )


async def executar_busca_com_botoes(update: Update, context: ContextTypes.DEFAULT_TYPE, tipo: str):
    valor = " ".join(context.args).strip()
    if tipo == "oab":
        valor = normalizar_oab(valor)
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
    USER_RESULTS[user_id] = {"valor": valor, "tipo": tipo, "uf": uf, "processos_por_ano": processos_por_ano, "created_at": time.time()}
    anos = sorted(processos_por_ano.keys(), reverse=True)
    keyboard = []
    for ano in anos:
        qtd = len(processos_por_ano[ano])
        keyboard.append([InlineKeyboardButton(f"📂 Processos {ano} ({qtd})", callback_data=f"abrir_ano:{ano}:0")])
    total = sum(len(v) for v in processos_por_ano.values())
    texto = f"✅ Busca concluída.\n\nResultados encontrados: {total}\nUF detectada: {uf or 'Não aplicada'}\n\nEscolha o ano abaixo para ver os processos:"
    await msg.edit_text(texto, reply_markup=InlineKeyboardMarkup(keyboard))


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
    parts = query.data.split(":")
    ano = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    session = USER_RESULTS.get(user_id)
    if not session:
        await query.edit_message_text("❌ Busca expirada. Faça uma nova consulta.")
        return
    processos_ano = list(session["processos_por_ano"].get(ano, {}).values())
    if not processos_ano:
        await query.edit_message_text("❌ Nenhum processo encontrado para esse ano.")
        return
    start_i = page * PAGE_SIZE
    itens = processos_ano[start_i:start_i + PAGE_SIZE]
    texto = f"📂 Processos do ano {ano}\nTotal encontrado: {len(processos_ano)}\nPágina: {page + 1}\n\nClique em apenas um processo para ver detalhes:"
    keyboard = []
    for i, item in enumerate(itens, start=start_i + 1):
        cnj = item["cnj"]
        keyboard.append([InlineKeyboardButton(f"🔎 {i}. {cnj}", callback_data=f"detalhe:{cnj}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"abrir_ano:{ano}:{page-1}"))
    if start_i + PAGE_SIZE < len(processos_ano):
        nav.append(InlineKeyboardButton("➡️ Próxima", callback_data=f"abrir_ano:{ano}:{page+1}"))
    if nav:
        keyboard.append(nav)
    await query.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(keyboard))


async def enviar_detalhe(query, context, cnj):
    await query.edit_message_text(f"🔎 Consultando detalhes do processo:\n{cnj}\n\nAguarde...")
    try:
        resposta, teclado = await asyncio.to_thread(buscar_detalhes_autorequest, cnj)
    except Exception as e:
        resposta = f"❌ Erro ao buscar detalhes.\n\nNº do processo: {cnj}\nErro: {str(e)[:500]}"
        teclado = None
    await context.bot.send_message(chat_id=query.message.chat_id, text=resposta[:4000], reply_markup=teclado)


async def callback_detalhe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Consultando detalhes...")
    cnj = query.data.split(":", 1)[1]
    await enviar_detalhe(query, context, cnj)


async def callback_retry_detalhe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Tentando novamente...")
    cnj = query.data.split(":", 1)[1]
    await enviar_detalhe(query, context, cnj)


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("oab", oab))
telegram_app.add_handler(CommandHandler("nomeadv", nomeadv))
telegram_app.add_handler(CommandHandler("nomeparte", nomeparte))
telegram_app.add_handler(CallbackQueryHandler(callback_ano, pattern=r"^abrir_ano:"))
telegram_app.add_handler(CallbackQueryHandler(callback_detalhe, pattern=r"^detalhe:"))
telegram_app.add_handler(CallbackQueryHandler(callback_retry_detalhe, pattern=r"^retrydet:"))


@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
def home():
    return {"status": "ConsultaBot V6 Codilo Online"}


@app.on_event("startup")
async def startup():
    await telegram_app.initialize()
    if RENDER_URL:
        await telegram_app.bot.set_webhook(url=f"{RENDER_URL}/webhook", drop_pending_updates=True)


@app.on_event("shutdown")
async def shutdown():
    await telegram_app.shutdown()
