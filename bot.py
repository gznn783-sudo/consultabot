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

# IMPORTANTE:
# Esse limite controla quantos endpoints da Codilo serão consultados por busca.
# Antes, quando ficava muito baixo, o bot só consultava poucos tribunais.
MAX_CODILO_REQUESTS = int(os.getenv("MAX_CODILO_REQUESTS", "100"))

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

PAGE_SIZE = 8
CNJ_REGEX = r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"

# Apenas prioridade. NÃO é filtro exclusivo.
# O bot vai consultar esses primeiro, mas continuará consultando os demais endpoints disponíveis.
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
        "secret": CODILO_SECRET,
    }

    response = requests.post(
        AUTH_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
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


def codilo_headers(content_type=True):
    headers = {
        "Authorization": f"Bearer {get_codilo_token()}",
        "accept": "*/*",
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def get_available():
    now = time.time()
    if AVAILABLE_CACHE["data"] and AVAILABLE_CACHE["expires_at"] > now:
        return AVAILABLE_CACHE["data"]

    response = requests.get(AVAILABLE_URL, headers=codilo_headers(False), timeout=30)
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


def normalizar_oab(valor):
    valor = " ".join(str(valor).strip().upper().split())
    valor = valor.replace("—", "-").replace("–", "-")
    valor = re.sub(r"\s+", "", valor)
    return valor


def extrair_ano_cnj(cnj):
    match = re.search(r"\.(\d{4})\.", cnj)
    return match.group(1) if match else "Sem ano"


def achar_cnjs_no_objeto(obj):
    return list(set(re.findall(CNJ_REGEX, str(obj))))


def buscar_recursivo(obj, chaves):
    if isinstance(obj, dict):
        for chave in chaves:
            if chave in obj and obj[chave] not in [None, "", [], {}]:
                return obj[chave]
        for valor in obj.values():
            encontrado = buscar_recursivo(valor, chaves)
            if encontrado not in [None, "", [], {}]:
                return encontrado
    elif isinstance(obj, list):
        for item in obj:
            encontrado = buscar_recursivo(item, chaves)
            if encontrado not in [None, "", [], {}]:
                return encontrado
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

    # Algumas respostas usam court em vez de search.
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
            if key in param_keys:
                saida.append({
                    "source": novo_ctx.get("source") or "courts",
                    "platform": novo_ctx["platform"],
                    "search": novo_ctx["search"],
                    "query": novo_ctx["query"],
                    "param_key": key,
                })

    for key, value in node.items():
        if key in ["params", "parameters"]:
            continue
        if isinstance(value, (dict, list)):
            extrair_queries_disponiveis(value, param_keys, novo_ctx.copy(), saida)

    return saida


def ordenar_por_uf(consultas, uf=None):
    """
    Corrigido: prioriza os tribunais da UF, mas NÃO remove os demais.
    Antes isso filtrava demais e reduzia o total de processos encontrados.
    """
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

    def peso(item):
        search = str(item.get("search", "")).lower()
        try:
            return prioridade.index(search)
        except ValueError:
            return 999

    prioritarios = sorted(prioritarios, key=peso)
    return prioritarios + restantes


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
        "param": {"key": item["param_key"], "value": value},
        "callbacks": [],
    }

    response = requests.post(REQUEST_URL, headers=codilo_headers(), json=payload, timeout=30)
    if response.status_code not in [200, 201]:
        raise Exception(f"Create {response.status_code}: {response.text[:500]}")

    data = response.json()
    request_id = data.get("data", {}).get("id") or data.get("requestId") or data.get("id")
    if not request_id:
        raise Exception(f"Sem request id: {data}")
    return request_id


def extrair_status_resultado(resultado):
    if not isinstance(resultado, dict):
        return ""
    return str(
        resultado.get("status")
        or resultado.get("data", {}).get("status")
        or resultado.get("requested", {}).get("status")
        or ""
    ).lower()


def get_request_result(request_id, tentativas=18, espera=5):
    """
    GET /request/{requestId}
    Corrigido para aceitar formatos diferentes da Codilo:
    - data como lista
    - data.result
    - data.requests
    - success true com status pending
    - warning com CNJs no payload
    """
    import json
    url = f"{REQUEST_URL}/{request_id}"
    ultimo = {}
    ultimo_texto = ""

    for tentativa in range(tentativas):
        try:
            response = requests.get(url, headers=codilo_headers(False), timeout=60)
            ultimo_texto = response.text

            try:
                resultado = response.json()
            except Exception:
                resultado = {"raw_text": response.text}

            ultimo = resultado

            print("\n==============================")
            print("REQUEST ID:", request_id)
            print("STATUS CODE:", response.status_code)
            print("TENTATIVA:", tentativa + 1)
            print("==============================")
            try:
                print(json.dumps(resultado, indent=2, ensure_ascii=False)[:25000])
            except Exception:
                print(str(resultado)[:25000])

            cnjs = achar_cnjs_no_objeto(resultado)
            if cnjs:
                resultado["fallback_cnjs"] = cnjs
                return resultado

            status = extrair_status_resultado(resultado)
            if status in ["success", "warning", "done", "finished", "completed"]:
                return resultado

            if status in ["pending", "processing", "running", "waiting", ""]:
                time.sleep(espera)
                continue

            return resultado

        except Exception as e:
            print("ERRO get_request_result:", str(e))
            cnjs = achar_cnjs_no_objeto(ultimo_texto)
            if cnjs:
                return {"success": True, "fallback_cnjs": cnjs, "raw_text": ultimo_texto}
            time.sleep(espera)

    cnjs = achar_cnjs_no_objeto(ultimo or ultimo_texto)
    if cnjs:
        return {"success": True, "fallback_cnjs": cnjs, "raw": ultimo or ultimo_texto}

    return ultimo if isinstance(ultimo, dict) else {"success": False, "data": []}


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
            resultado = get_request_result(request_id, tentativas=10, espera=3)

            cnjs = resultado.get("fallback_cnjs", []) if isinstance(resultado, dict) else []
            if not cnjs:
                cnjs = achar_cnjs_no_objeto(resultado)

            for cnj in cnjs:
                ano = extrair_ano_cnj(cnj)
                processos_por_ano.setdefault(ano, {})
                processos_por_ano[ano][cnj] = {
                    "cnj": cnj,
                    "tribunal": item.get("search", "Não informado"),
                    "platform": item.get("platform", "Não informado"),
                    "query": item.get("query", "Não informado"),
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
        "callbacks": [],
        "format": "allRequests",
    }
    response = requests.post(AUTOREQUEST_URL, headers=codilo_headers(), json=payload, timeout=30)
    if response.status_code not in [200, 201]:
        raise Exception(f"AutoRequest {response.status_code}: {response.text[:500]}")

    data = response.json()
    auto_id = data.get("data", {}).get("id") or data.get("id")
    if not auto_id:
        raise Exception(f"AutoRequest sem ID: {data}")
    return auto_id


def consultar_autorequest(auto_id, tentativas=12, espera=8):
    url = f"{AUTOREQUEST_URL}/{auto_id}"
    ultimo_requests = []
    ultimo_data = {}

    for _ in range(tentativas):
        response = requests.get(url, headers=codilo_headers(False), timeout=60)
        if response.status_code not in [200, 201]:
            raise Exception(f"Show AutoRequest {response.status_code}: {response.text[:500]}")

        data = response.json()
        ultimo_data = data
        base = data.get("data", {}) if isinstance(data, dict) else {}
        requests_list = base.get("requests") or data.get("requests") or []
        ultimo_requests = requests_list

        success_requests = [r for r in requests_list if str(r.get("status", "")).lower() == "success"]
        pending_requests = [r for r in requests_list if str(r.get("status", "")).lower() in ["pending", "processing", "running", "waiting"]]

        if success_requests:
            return success_requests, requests_list, ultimo_data

        if not pending_requests and requests_list:
            return [], requests_list, ultimo_data

        time.sleep(espera)

    return [], ultimo_requests, ultimo_data


def get_any(obj, keys, default="Não informado"):
    if not isinstance(obj, dict):
        return default
    for key in keys:
        value = obj.get(key)
        if value not in [None, "", [], {}]:
            return value
    recursive = buscar_recursivo(obj, keys)
    if recursive not in [None, "", [], {}]:
        return recursive
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

    autores, reus, advogados = [], [], []

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
            str(pessoa.get("pole", "")),
        ]).lower()

        if "adv" in tipo or "lawyer" in tipo:
            advogados.append(nome)
        elif "autor" in tipo or "requerente" in tipo or "exequente" in tipo or "active" in tipo or "parte ativa" in tipo:
            autores.append(nome)
        elif "réu" in tipo or "reu" in tipo or "requerido" in tipo or "executado" in tipo or "passive" in tipo or "parte passiva" in tipo:
            reus.append(nome)

        for adv in pessoa.get("lawyers", []) or pessoa.get("advogados", []):
            advogados.append(normalizar_nome(adv))

    if not autores:
        autor_rec = buscar_recursivo(processo, ["autor", "author", "plaintiff", "claimant"])
        if autor_rec:
            autores.append(normalizar_nome(autor_rec) if isinstance(autor_rec, dict) else str(autor_rec))

    if not reus:
        reu_rec = buscar_recursivo(processo, ["reu", "réu", "requerido", "defendant"])
        if reu_rec:
            reus.append(normalizar_nome(reu_rec) if isinstance(reu_rec, dict) else str(reu_rec))

    if not advogados:
        adv_rec = buscar_recursivo(processo, ["advogado", "advogados", "lawyer", "lawyers"])
        if adv_rec:
            if isinstance(adv_rec, list) and adv_rec:
                advogados.append(normalizar_nome(adv_rec[0]))
            else:
                advogados.append(normalizar_nome(adv_rec) if isinstance(adv_rec, dict) else str(adv_rec))

    return {
        "autor": autores[0] if autores else "Não informado",
        "reu": reus[0] if reus else "Não informado",
        "advogado": advogados[0] if advogados else "Não informado",
    }


def extrair_lista_processos(resultado):
    if not isinstance(resultado, dict):
        return []

    # Callback/full response: data pode ser lista de processos.
    data = resultado.get("data", [])

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for chave in ["items", "processes", "processos", "result", "results", "lawsuits", "records", "lawsuit", "process"]:
            if isinstance(data.get(chave), list):
                return data.get(chave)

        if isinstance(data.get("result"), dict):
            return extrair_lista_processos(data.get("result"))

        if data.get("properties") or data.get("people") or data.get("number") or data.get("cnj"):
            return [data]

        # Algumas respostas têm requested/info/data dentro.
        for chave in ["payload", "response", "body"]:
            if isinstance(data.get(chave), dict):
                achou = extrair_lista_processos(data.get(chave))
                if achou:
                    return achou

    for chave in ["result", "results", "lawsuits", "processes"]:
        if isinstance(resultado.get(chave), list):
            return resultado.get(chave)
        if isinstance(resultado.get(chave), dict):
            achou = extrair_lista_processos(resultado.get(chave))
            if achou:
                return achou

    return []


def formatar_valor(valor):
    if valor in [None, "", [], {}]:
        return "Não informado"
    if isinstance(valor, (int, float)):
        return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(valor)


def formatar_processo(processo, fallback_tribunal="Não informado", cnj_forcado=None):
    props = processo.get("properties") or processo.get("capa") or processo.get("cover") or processo
    pessoas = extrair_pessoas(processo)

    numero = cnj_forcado or get_any(props, ["number", "cnj", "numero", "numeroProcesso", "processo", "processNumber"])
    tribunal = get_any(props, ["court", "tribunal", "search", "tribunalNome", "forum"], fallback_tribunal)
    origem = get_any(props, ["origin", "origem", "foro", "comarca", "vara", "courtSection", "judgingBody"], "Não informado")
    assunto = get_any(props, ["subject", "assunto", "subjects", "area", "classe", "class", "nature", "matter"], "Não informado")
    valor = formatar_valor(get_any(props, ["value", "valor", "valorCausa", "valor_da_causa", "claimValue", "amount"], "Não informado"))

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


def resumo_status(all_requests):
    linhas = []
    for r in all_requests[:12]:
        tribunal = r.get("court") or r.get("search") or r.get("platform") or "?"
        status = r.get("status") or "?"
        linhas.append(f"{tribunal}: {status}")
    return "\n".join(linhas) if linhas else "Sem status interno retornado."


def buscar_detalhes_autorequest(cnj, forcar_novo=False):
    if cnj in DETAIL_CACHE and not forcar_novo:
        return DETAIL_CACHE[cnj], None

    if cnj in AUTO_CACHE and not forcar_novo:
        auto_id = AUTO_CACHE[cnj]["auto_id"]
    else:
        auto_id = criar_autorequest(cnj)
        AUTO_CACHE[cnj] = {"auto_id": auto_id, "created_at": time.time()}

    success_requests, all_requests, auto_raw = consultar_autorequest(auto_id)

    ultimo_erro = None

    # Primeiro tenta usar dados completos que podem vir direto no autorequest.
    processos_auto = extrair_lista_processos(auto_raw)
    if processos_auto:
        resposta = formatar_processo(processos_auto[0], cnj_forcado=cnj)
        DETAIL_CACHE[cnj] = resposta
        return resposta, None

    for req in success_requests:
        request_id = req.get("id")
        tribunal = req.get("court") or req.get("search") or req.get("platform") or "Não informado"
        if not request_id:
            continue

        try:
            resultado = get_request_result(request_id, tentativas=14, espera=5)
            processos = extrair_lista_processos(resultado)
            if processos:
                resposta = formatar_processo(processos[0], fallback_tribunal=tribunal, cnj_forcado=cnj)
                DETAIL_CACHE[cnj] = resposta
                return resposta, None
            ultimo_erro = "Requisição success, mas sem dados de capa."
        except Exception as e:
            ultimo_erro = str(e)
            continue

    texto = (
        f"⏳ Ainda não consegui montar a capa completa.\n\n"
        f"Nº do processo:\n{cnj}\n\n"
        f"AutoRequest ID:\n{auto_id}\n\n"
        f"Status interno:\n{resumo_status(all_requests)}\n\n"
        f"Erro: {ultimo_erro or 'Sem dados completos retornados até agora.'}"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔁 Tentar novamente", callback_data=f"retrydetalhe:{cnj}")
    ]])

    return texto, keyboard


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
    if tipo == "oab":
        valor = normalizar_oab(valor)

    if not valor:
        await update.message.reply_text("Digite o termo da busca.")
        return

    msg = await update.message.reply_text(
        "🔎 Consulta iniciada.\n\n"
        "Estou aguardando retorno da Codilo.\n"
        "TJRS/TJSC/TRF4 podem demorar um pouco."
    )

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
        "created_at": time.time(),
    }

    anos = sorted(processos_por_ano.keys(), reverse=True)
    keyboard = []
    for ano in anos:
        qtd = len(processos_por_ano[ano])
        keyboard.append([InlineKeyboardButton(f"📂 Processos {ano} ({qtd})", callback_data=f"abrir_ano:{ano}:0")])

    total = sum(len(v) for v in processos_por_ano.values())
    texto = (
        f"✅ Busca concluída.\n\n"
        f"Resultados encontrados: {total}\n"
        f"UF detectada: {uf or 'Não aplicada'}\n\n"
        f"Escolha o ano abaixo para ver os processos:"
    )

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
    data = query.data
    partes = data.split(":")
    ano = partes[1]
    pagina = int(partes[2]) if len(partes) > 2 else 0

    session = USER_RESULTS.get(user_id)
    if not session:
        await query.edit_message_text("❌ Busca expirada. Faça uma nova consulta.")
        return

    processos_ano = list(session["processos_por_ano"].get(ano, {}).values())
    if not processos_ano:
        await query.edit_message_text("❌ Nenhum processo encontrado para esse ano.")
        return

    inicio = pagina * PAGE_SIZE
    fim = inicio + PAGE_SIZE
    itens = processos_ano[inicio:fim]

    texto = (
        f"📂 Processos do ano {ano}\n"
        f"Total encontrado: {len(processos_ano)}\n"
        f"Exibindo: {inicio + 1}-{min(fim, len(processos_ano))}\n\n"
        "Clique em apenas um processo para ver detalhes:"
    )

    keyboard = []
    for i, item in enumerate(itens, start=inicio + 1):
        cnj = item["cnj"]
        tribunal = item.get("tribunal", "")
        keyboard.append([InlineKeyboardButton(f"🔎 {i}. {cnj} {tribunal}", callback_data=f"detalhe:{cnj}")])

    nav = []
    if pagina > 0:
        nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"abrir_ano:{ano}:{pagina - 1}"))
    if fim < len(processos_ano):
        nav.append(InlineKeyboardButton("➡️ Próxima", callback_data=f"abrir_ano:{ano}:{pagina + 1}"))
    if nav:
        keyboard.append(nav)

    await query.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(keyboard))


async def callback_detalhe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Consultando detalhes...")

    cnj = query.data.split(":", 1)[1]
    await query.edit_message_text(f"🔎 Consultando detalhes do processo:\n{cnj}\n\nAguarde...")

    try:
        resposta, keyboard = await asyncio.to_thread(buscar_detalhes_autorequest, cnj, False)
    except Exception as e:
        resposta = f"❌ Erro ao buscar detalhes.\n\nNº do processo: {cnj}\nErro: {str(e)[:500]}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Tentar novamente", callback_data=f"retrydetalhe:{cnj}")]])

    await context.bot.send_message(chat_id=query.message.chat_id, text=resposta[:4000], reply_markup=keyboard)


async def callback_retry_detalhe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Tentando novamente...")

    cnj = query.data.split(":", 1)[1]
    await query.edit_message_text(f"🔎 Consultando novamente:\n{cnj}\n\nAguarde...")

    try:
        resposta, keyboard = await asyncio.to_thread(buscar_detalhes_autorequest, cnj, True)
    except Exception as e:
        resposta = f"❌ Erro ao tentar novamente.\n\nNº do processo: {cnj}\nErro: {str(e)[:500]}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Tentar novamente", callback_data=f"retrydetalhe:{cnj}")]])

    await context.bot.send_message(chat_id=query.message.chat_id, text=resposta[:4000], reply_markup=keyboard)


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("oab", oab))
telegram_app.add_handler(CommandHandler("nomeadv", nomeadv))
telegram_app.add_handler(CommandHandler("nomeparte", nomeparte))
telegram_app.add_handler(CallbackQueryHandler(callback_ano, pattern=r"^abrir_ano:"))
telegram_app.add_handler(CallbackQueryHandler(callback_detalhe, pattern=r"^detalhe:"))
telegram_app.add_handler(CallbackQueryHandler(callback_retry_detalhe, pattern=r"^retrydetalhe:"))


@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
def home():
    return {"status": "ConsultaBot V6 corrigido online"}


@app.on_event("startup")
async def startup():
    await telegram_app.initialize()
    if RENDER_URL:
        await telegram_app.bot.set_webhook(url=f"{RENDER_URL}/webhook", drop_pending_updates=True)


@app.on_event("shutdown")
async def shutdown():
    await telegram_app.shutdown()
