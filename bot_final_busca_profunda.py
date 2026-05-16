import os
import re
import time
import json
import asyncio
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ============================================================
# CONFIGURAÇÕES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")

CODILO_KEY = os.getenv("CODILO_KEY")
CODILO_SECRET = os.getenv("CODILO_SECRET")

MAX_CODILO_REQUESTS = int(os.getenv("MAX_CODILO_REQUESTS", "50"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "8"))

# Evita o bot ficar preso esperando tribunal em pending por muitos minutos.
# Pode ajustar no Render se quiser:
# CODILO_MAX_POLL_SECONDS=150
# CODILO_POLL_INTERVAL=8
# CODILO_CONCURRENCY=6
CODILO_MAX_POLL_SECONDS = int(os.getenv("CODILO_MAX_POLL_SECONDS", "150"))
CODILO_POLL_INTERVAL = int(os.getenv("CODILO_POLL_INTERVAL", "8"))
CODILO_CONCURRENCY = int(os.getenv("CODILO_CONCURRENCY", "6"))

# Busca profunda no histórico/fallback da Codilo.
# Atenção: /request é histórico de requests; por isso filtramos por OAB exata para não misturar consultas antigas.
CODILO_FALLBACK_PAGES = int(os.getenv("CODILO_FALLBACK_PAGES", "6"))
CODILO_FALLBACK_LIMIT = int(os.getenv("CODILO_FALLBACK_LIMIT", "100"))

AUTH_URL = "https://auth.codilo.com.br/oauth/token"
AVAILABLE_URL = "https://api.consulta.codilo.com.br/v1/available"
REQUEST_URL = "https://api.consulta.codilo.com.br/v1/request"
AUTOREQUEST_URL = "https://api.consulta.codilo.com.br/v1/autorequest"

CNJ_REGEX = r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"

app = FastAPI()
telegram_app = Application.builder().token(BOT_TOKEN).build()

TOKEN_CACHE = {"access_token": None, "expires_at": 0}
AVAILABLE_CACHE = {"data": None, "expires_at": 0}

USER_RESULTS = {}
DETAIL_CACHE = {}
BUSCAS_ATIVAS = set()
UPDATES_PROCESSADOS = set()


# Mapa de busca regional por OAB.
# /oab normal: TJ da UF + TRF/TRT da região + STJ/CNJ.
# /oabnacional: usa todas as rotas disponíveis que aceitam OAB.
UF_TJ = {
    "AC": ["tjac"], "AL": ["tjal"], "AP": ["tjap"], "AM": ["tjam"],
    "BA": ["tjba"], "CE": ["tjce"], "DF": ["tjdft"], "ES": ["tjes"],
    "GO": ["tjgo"], "MA": ["tjma"], "MT": ["tjmt"], "MS": ["tjms"],
    "MG": ["tjmg"], "PA": ["tjpa"], "PB": ["tjpb"], "PR": ["tjpr", "tjpr-turmarecursal"],
    "PE": ["tjpe"], "PI": ["tjpi"], "RJ": ["tjrj"], "RN": ["tjrn"],
    "RS": ["tjrs"], "RO": ["tjro"], "RR": ["tjrr"], "SC": ["tjsc"],
    "SP": ["tjsp"], "SE": ["tjse"], "TO": ["tjto"],
}

UF_TRF = {
    "AC": ["trf1"], "AM": ["trf1"], "AP": ["trf1"], "BA": ["trf1"],
    "DF": ["trf1"], "GO": ["trf1"], "MA": ["trf1"], "MT": ["trf1"],
    "PA": ["trf1"], "PI": ["trf1"], "RO": ["trf1"], "RR": ["trf1"],
    "TO": ["trf1"],

    "ES": ["trf2"], "RJ": ["trf2"],

    "MS": ["trf3"], "SP": ["trf3"],

    "PR": ["trf4", "trf4-jfpr"],
    "RS": ["trf4", "trf4-jfrs"],
    "SC": ["trf4", "trf4-jfsc"],

    "AL": ["trf5"], "CE": ["trf5"], "PB": ["trf5"], "PE": ["trf5"],
    "RN": ["trf5"], "SE": ["trf5"],

    "MG": ["trf6"],
}

UF_TRT = {
    "RJ": ["trt1"],
    "SP": ["trt2", "trt15"],
    "MG": ["trt3"],
    "RS": ["trt4"],
    "BA": ["trt5"],
    "PE": ["trt6"],
    "CE": ["trt7"],
    "PA": ["trt8"], "AP": ["trt8"],
    "PR": ["trt9"],
    "DF": ["trt10"], "TO": ["trt10"],
    "AM": ["trt11"], "RR": ["trt11"],
    "SC": ["trt12"],
    "PB": ["trt13"],
    "RO": ["trt14"], "AC": ["trt14"],
    "MA": ["trt16"],
    "ES": ["trt17"],
    "GO": ["trt18"],
    "AL": ["trt19"],
    "SE": ["trt20"],
    "RN": ["trt21"],
    "PI": ["trt22"],
    "MT": ["trt23"],
    "MS": ["trt24"],
}

def searches_regionais_por_uf(uf):
    uf = (uf or "").upper().strip()
    buscas = []
    buscas.extend(UF_TJ.get(uf, []))
    buscas.extend(UF_TRF.get(uf, []))
    buscas.extend(UF_TRT.get(uf, []))
    buscas.extend(["stj", "cnj"])

    # remove duplicados mantendo ordem
    final = []
    for item in buscas:
        item = str(item).lower().strip()
        if item and item not in final:
            final.append(item)
    return final

def detectar_modo_nacional_texto(texto):
    texto = (texto or "").lower()
    return "nacional" in texto or "brasil" in texto or "todos" in texto


UF_TRIBUNAIS = {}


# ============================================================
# AUTENTICAÇÃO CODILO
# ============================================================

def get_codilo_token():
    now = time.time()

    if TOKEN_CACHE["access_token"] and TOKEN_CACHE["expires_at"] > now:
        return TOKEN_CACHE["access_token"]

    if not CODILO_KEY or not CODILO_SECRET:
        raise Exception("CODILO_KEY ou CODILO_SECRET não configurados no Render.")

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


def codilo_headers():
    return {
        "Authorization": f"Bearer {get_codilo_token()}",
        "Content-Type": "application/json",
        "accept": "*/*",
    }


# ============================================================
# UTILITÁRIOS
# ============================================================

def log_json(titulo, obj, limite=50000):
    try:
        print(f"\n=========== {titulo} ===========")
        print(json.dumps(obj, indent=2, ensure_ascii=False)[:limite])
        print(f"=========== FIM {titulo} ===========\n")
    except Exception as e:
        print(f"ERRO LOG {titulo}: {e}")


def achar_cnjs_no_objeto(obj):
    return list(set(re.findall(CNJ_REGEX, str(obj))))


def extrair_ano_cnj(cnj):
    match = re.search(r"\.(\d{4})\.", cnj or "")
    return match.group(1) if match else "Sem ano"


def detectar_tribunal_pelo_cnj(cnj):
    """Detecta tribunal pelo segmento do CNJ para não rotular TJ como TRF errado."""
    cnj = cnj or ""
    if ".4.04." in cnj:
        return "trf4"
    if ".8.21." in cnj:
        return "tjrs"
    if ".8.24." in cnj:
        return "tjsc"
    if ".8.16." in cnj:
        return "tjpr"
    if ".8.26." in cnj:
        return "tjsp"
    if ".8.13." in cnj:
        return "tjmg"
    if ".8.19." in cnj:
        return "tjrj"
    if ".5.04." in cnj:
        return "trt4"
    if ".5.12." in cnj:
        return "trt12"
    if ".5.09." in cnj:
        return "trt9"
    return None



CNJ_ESTADUAL_CODES = {
    "AC": "8.01", "AL": "8.02", "AP": "8.03", "AM": "8.04", "BA": "8.05",
    "CE": "8.06", "DF": "8.07", "ES": "8.08", "GO": "8.09", "MA": "8.10",
    "MT": "8.11", "MS": "8.12", "MG": "8.13", "PA": "8.14", "PB": "8.15",
    "PR": "8.16", "PE": "8.17", "PI": "8.18", "RJ": "8.19", "RN": "8.20",
    "RS": "8.21", "RO": "8.22", "RR": "8.23", "SC": "8.24", "SP": "8.26",
    "SE": "8.25", "TO": "8.27",
}

CNJ_TRF_CODES = {
    "trf1": "4.01",
    "trf2": "4.02",
    "trf3": "4.03",
    "trf4": "4.04",
    "trf5": "4.05",
    "trf6": "4.06",
}

CNJ_TRT_CODES = {
    "trt1": "5.01", "trt2": "5.02", "trt3": "5.03", "trt4": "5.04",
    "trt5": "5.05", "trt6": "5.06", "trt7": "5.07", "trt8": "5.08",
    "trt9": "5.09", "trt10": "5.10", "trt11": "5.11", "trt12": "5.12",
    "trt13": "5.13", "trt14": "5.14", "trt15": "5.15", "trt16": "5.16",
    "trt17": "5.17", "trt18": "5.18", "trt19": "5.19", "trt20": "5.20",
    "trt21": "5.21", "trt22": "5.22", "trt23": "5.23", "trt24": "5.24",
}

def cnj_tem_codigo(cnj, codigo):
    return f".{codigo}." in (cnj or "")

def cnj_permitido_para_busca(cnj, uf=None, nacional=False):
    """
    Filtro final anti-mistura.
    /oab regional aceita:
      - TJ da UF da OAB
      - TRF/TRT regionais da UF da OAB
      - STJ/CNJ/superiores, quando vierem
    /oabnacional aceita tudo.
    """
    cnj = cnj or ""

    if nacional:
        return True

    uf = (uf or "").upper().strip()
    if not uf:
        return True

    # CNJ dos superiores normalmente é .0.00. / .0.01. etc.; manter para não perder STJ/CNJ.
    if ".0." in cnj:
        return True

    cod_estadual = CNJ_ESTADUAL_CODES.get(uf)
    if cod_estadual and cnj_tem_codigo(cnj, cod_estadual):
        return True

    for trf in UF_TRF.get(uf, []):
        codigo = CNJ_TRF_CODES.get(trf)
        if codigo and cnj_tem_codigo(cnj, codigo):
            return True

    for trt in UF_TRT.get(uf, []):
        codigo = CNJ_TRT_CODES.get(trt)
        if codigo and cnj_tem_codigo(cnj, codigo):
            return True

    return False

def adicionar_cnjs_no_resultado(processos_por_ano, cnjs, item, uf=None, nacional=False):
    adicionados = 0
    rejeitados = 0

    for cnj in cnjs:
        if not cnj_permitido_para_busca(cnj, uf=uf, nacional=nacional):
            rejeitados += 1
            continue

        ano = extrair_ano_cnj(cnj)
        processos_por_ano.setdefault(ano, {})

        tribunal_detectado = detectar_tribunal_pelo_cnj(cnj) or item.get("search", "Não informado")

        if cnj not in processos_por_ano[ano]:
            adicionados += 1

        processos_por_ano[ano][cnj] = {
            "cnj": cnj,
            "tribunal": tribunal_detectado,
        }

    return adicionados, rejeitados


def extrair_uf_oab(valor):
    valor = (valor or "").strip().upper()

    match = re.search(r"--([A-Z]{2})$", valor)
    if match:
        return match.group(1)

    match = re.search(r"-[A-Z0-9]+-([A-Z]{2})$", valor)
    if match:
        return match.group(1)

    match = re.search(r"\b([A-Z]{2})$", valor)
    if match:
        return match.group(1)

    return None


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


def limpar_texto(valor):
    if valor in [None, "", [], {}]:
        return "Não informado"

    if isinstance(valor, list):
        partes = []
        for item in valor:
            if isinstance(item, dict):
                partes.append(
                    str(
                        item.get("name")
                        or item.get("nome")
                        or item.get("value")
                        or item.get("description")
                        or item
                    )
                )
            else:
                partes.append(str(item))
        return ", ".join([p for p in partes if p.strip()]) or "Não informado"

    if isinstance(valor, dict):
        return str(
            valor.get("name")
            or valor.get("nome")
            or valor.get("value")
            or valor.get("description")
            or valor
        )

    return str(valor)


# ============================================================
# AVAILABLE
# ============================================================

def get_available():
    now = time.time()

    if AVAILABLE_CACHE["data"] and AVAILABLE_CACHE["expires_at"] > now:
        return AVAILABLE_CACHE["data"]

    response = requests.get(
        AVAILABLE_URL,
        headers=codilo_headers(),
        timeout=60,
    )

    response.raise_for_status()
    data = response.json().get("data", [])

    AVAILABLE_CACHE["data"] = data
    AVAILABLE_CACHE["expires_at"] = now + 3600

    return data


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

            key = (
                param.get("tag")
                or param.get("key")
                or param.get("name")
                or param.get("param")
            )

            if key in param_keys:
                saida.append(
                    {
                        "source": novo_ctx.get("source") or "courts",
                        "platform": novo_ctx["platform"],
                        "search": novo_ctx["search"],
                        "query": novo_ctx["query"],
                        "param_key": key,
                    }
                )

    for key, value in node.items():
        if key in ["params", "parameters"]:
            continue

        if isinstance(value, (dict, list)):
            extrair_queries_disponiveis(value, param_keys, novo_ctx.copy(), saida)

    return saida


def ordenar_por_uf(consultas, uf=None, nacional=False):
    """
    /oab normal:
      Consulta TJ da UF + TRF/TRT da região + STJ/CNJ.
      Ex.: RS => tjrs, trf4, trf4-jfrs, trt4, stj, cnj.

    /oabnacional:
      Não filtra por UF; usa todas as rotas disponíveis que aceitam OAB.
    """
    if nacional:
        print("\n=========== ROTAS MODO NACIONAL ===========")
        print("UF base:", uf)
        print("Total de rotas:", len(consultas))
        print("=========== FIM ROTAS MODO NACIONAL ===========\n")
        return consultas

    if not uf:
        return consultas

    permitidas = searches_regionais_por_uf(uf)
    if not permitidas:
        return consultas

    resultado = []
    for search_prioritario in permitidas:
        for c in consultas:
            search = str(c.get("search", "")).lower().strip()
            if search == search_prioritario and c not in resultado:
                resultado.append(c)

    print("\n=========== ROTAS REGIONAIS FILTRADAS ===========")
    print("UF:", uf)
    print("Permitidas:", ", ".join(permitidas))
    for r in resultado:
        print(f"{r.get('search')}/{r.get('query')}/{r.get('param_key')} | platform={r.get('platform')} | source={r.get('source')}")
    print("=========== FIM ROTAS REGIONAIS FILTRADAS ===========\n")

    return resultado



def find_queries(param_keys, uf=None, nacional=False):
    available = get_available()
    consultas = extrair_queries_disponiveis(available, param_keys)

    unicas = []
    vistos = set()

    for c in consultas:
        chave = (c["source"], c["platform"], c["search"], c["query"], c["param_key"])

        if chave not in vistos:
            vistos.add(chave)
            unicas.append(c)

    unicas = ordenar_por_uf(unicas, uf=uf, nacional=nacional)

    print("\n=========== ROTAS DISPONÍVEIS SELECIONADAS ===========")
    print("UF:", uf or "Não detectada")
    print("Modo nacional:", nacional)
    print("MAX_CODILO_REQUESTS:", MAX_CODILO_REQUESTS)
    for c in unicas[:MAX_CODILO_REQUESTS]:
        print(f"{c.get('search')}/{c.get('query')}/{c.get('param_key')} | platform={c.get('platform')} | source={c.get('source')}")
    print("=========== FIM ROTAS SELECIONADAS ===========\n")

    return unicas[:MAX_CODILO_REQUESTS]


def find_queries_por_tribunal(param_keys, tribunal):
    available = get_available()
    consultas = extrair_queries_disponiveis(available, param_keys)

    tribunal = str(tribunal or "").lower()

    filtradas = [
        c for c in consultas
        if str(c.get("search", "")).lower() == tribunal
    ]

    unicas = []
    vistos = set()

    for c in filtradas:
        chave = (c["source"], c["platform"], c["search"], c["query"], c["param_key"])

        if chave not in vistos:
            vistos.add(chave)
            unicas.append(c)

    return unicas[:4]


# ============================================================
# CODILO REQUEST
# ============================================================

def create_request(item, value):
    payload = {
        "source": item["source"],
        "platform": item["platform"],
        "search": item["search"],
        "query": item["query"],
        "makeDownload": False,
        "param": {
            "key": item["param_key"],
            "value": value,
        },
        "callbacks": [],
    }

    response = requests.post(
        REQUEST_URL,
        headers=codilo_headers(),
        json=payload,
        timeout=60,
    )

    if response.status_code not in [200, 201]:
        raise Exception(f"Create {response.status_code}: {response.text[:500]}")

    data = response.json()
    log_json("CREATE REQUEST", data, 12000)

    request_id = (
        data.get("data", {}).get("id")
        or data.get("requestId")
        or data.get("id")
    )

    if not request_id:
        raise Exception(f"Sem request id: {data}")

    return request_id


def get_status_request(resultado):
    if not isinstance(resultado, dict):
        return ""

    requested = resultado.get("requested", {})
    data = resultado.get("data", {})

    status = ""

    if isinstance(requested, dict):
        status = requested.get("status") or ""

    if not status and isinstance(data, dict):
        status = data.get("status") or ""

    if not status:
        status = resultado.get("status") or ""

    return str(status).lower()


def get_request_result(request_id, max_tentativas=12, tempo_espera=10, max_seconds=None):
    """
    Consulta GET /v1/request/{id} até finalizar, mas sem travar o bot.

    Se a Codilo/tribunal ficar em pending por muito tempo, retorna:
      {"success": False, "pending_timeout": True, ...}

    Assim a busca principal continua com as outras rotas e entrega resultado parcial.
    """
    url = f"{REQUEST_URL}/{request_id}"
    ultimo_json = {}
    inicio = time.time()

    if max_seconds is None:
        max_seconds = CODILO_MAX_POLL_SECONDS

    tentativa = 0

    while True:
        tentativa += 1

        if time.time() - inicio > max_seconds:
            cnjs = achar_cnjs_no_objeto(ultimo_json)
            return {
                "success": bool(cnjs),
                "pending_timeout": True,
                "request_id": request_id,
                "fallback_cnjs": cnjs,
                "raw": ultimo_json,
            }

        if tentativa > max_tentativas:
            cnjs = achar_cnjs_no_objeto(ultimo_json)
            return {
                "success": bool(cnjs),
                "pending_timeout": True,
                "request_id": request_id,
                "fallback_cnjs": cnjs,
                "raw": ultimo_json,
            }

        try:
            response = requests.get(
                url,
                headers=codilo_headers(),
                timeout=60,
            )

            try:
                resultado = response.json()
            except Exception:
                resultado = {}

            ultimo_json = resultado

            print("\n==============================")
            print("GET REQUEST ID:", request_id)
            print("Tentativa:", tentativa, "/", max_tentativas)
            print("Tempo limite:", max_seconds, "segundos")
            print("STATUS CODE:", response.status_code)
            print("==============================")
            log_json("GET REQUEST RESULT", resultado)

            status = get_status_request(resultado)
            processos = extrair_lista_processos(resultado)
            cnjs = achar_cnjs_no_objeto(resultado)

            if processos and status != "pending":
                return {
                    "success": True,
                    "request_id": request_id,
                    "fallback_cnjs": cnjs,
                    "raw": resultado,
                }

            if cnjs and status != "pending":
                return {
                    "success": True,
                    "request_id": request_id,
                    "fallback_cnjs": cnjs,
                    "raw": resultado,
                }

            if status in ["success", "completed", "finished", "done", "warning"]:
                return {
                    "success": True,
                    "request_id": request_id,
                    "fallback_cnjs": cnjs,
                    "raw": resultado,
                }

            if status in ["error", "failed", "failure"]:
                return {
                    "success": False,
                    "request_id": request_id,
                    "raw": resultado,
                }

            if status in ["pending", "processing", "running", "waiting", "created"]:
                time.sleep(tempo_espera)
                continue

            time.sleep(tempo_espera)

        except Exception as e:
            print("ERRO get_request_result:", str(e))
            time.sleep(tempo_espera)


def listar_requests_fallback(item, valor=None):
    """
    Fallback profundo no histórico /v1/request.
    Importante: filtra pela OAB/nome exato para não misturar resultados antigos de outras consultas.
    """
    todos = []

    try:
        for page in range(1, CODILO_FALLBACK_PAGES + 1):
            params = {
                "source": item["source"],
                "platform": item["platform"],
                "search": item["search"],
                "page": page,
                "limit": CODILO_FALLBACK_LIMIT,
                "success": "true",
                "warning": "true",
            }

            response = requests.get(
                REQUEST_URL,
                headers=codilo_headers(),
                params=params,
                timeout=60,
            )

            try:
                data = response.json()
            except Exception:
                data = {}

            log_json("LIST REQUEST FALLBACK PAGE", {"page": page, "params": params, "response": data}, 6000)

            result = data.get("data", {}).get("result", [])
            if not isinstance(result, list) or not result:
                break

            for req in result:
                if valor is not None:
                    param = req.get("param", {}) if isinstance(req, dict) else {}
                    if str(param.get("key", "")).lower() != str(item.get("param_key", "")).lower():
                        continue
                    if str(param.get("value", "")).strip().upper() != str(valor).strip().upper():
                        continue

                todos.append(req)

            total = data.get("data", {}).get("total")
            if total is not None and len(todos) >= int(total):
                break

        return {"success": True, "data": {"result": todos, "total": len(todos)}}

    except Exception as e:
        print("ERRO listar_requests_fallback:", str(e))
        return {}



# ============================================================
# EXTRAÇÃO DE PROCESSOS / CAPA
# ============================================================

def extrair_lista_processos(resultado):
    if not isinstance(resultado, dict):
        return []

    data = resultado.get("data", [])

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for chave in [
            "result",
            "results",
            "items",
            "processes",
            "processos",
            "lawsuits",
            "records",
            "lawsuit",
            "process",
            "data",
        ]:
            valor = data.get(chave)

            if isinstance(valor, list):
                return valor

            if isinstance(valor, dict):
                return [valor]

        if data.get("cover") or data.get("properties") or data.get("people") or data.get("steps"):
            return [data]

    for chave in [
        "result",
        "results",
        "items",
        "processes",
        "processos",
        "lawsuits",
        "records",
    ]:
        valor = resultado.get(chave)

        if isinstance(valor, list):
            return valor

        if isinstance(valor, dict):
            return [valor]

    if resultado.get("cover") or resultado.get("properties") or resultado.get("people") or resultado.get("steps"):
        return [resultado]

    return []


def get_cover_value(processo, nome):
    cover = processo.get("cover", [])

    if not isinstance(cover, list):
        return None

    for item in cover:
        if not isinstance(item, dict):
            continue

        desc = str(item.get("description", "")).lower()
        valor = item.get("value")

        if nome.lower() in desc and valor not in [None, "", [], {}]:
            return valor

    return None


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

        tipo = " ".join(
            [
                str(pessoa.get("type", "")),
                str(pessoa.get("role", "")),
                str(pessoa.get("side", "")),
                str(pessoa.get("qualifier", "")),
                str(pessoa.get("description", "")),
                str(pessoa.get("kind", "")),
                str(pessoa.get("pole", "")),
            ]
        ).lower()

        if "adv" in tipo or "lawyer" in tipo:
            advogados.append(nome)
        elif (
            "autor" in tipo
            or "requerente" in tipo
            or "exequente" in tipo
            or "active" in tipo
            or "agravante" in tipo
            or "apelante" in tipo
        ):
            autores.append(nome)
        elif (
            "réu" in tipo
            or "reu" in tipo
            or "requerido" in tipo
            or "executado" in tipo
            or "passive" in tipo
            or "agravado" in tipo
            or "apelado" in tipo
        ):
            reus.append(nome)

        for adv in pessoa.get("lawyers", []) or pessoa.get("advogados", []):
            advogados.append(normalizar_nome(adv))

    if not autores:
        autor_rec = buscar_recursivo(processo, ["autor", "author", "plaintiff", "claimant"])
        if autor_rec:
            autores.append(limpar_texto(autor_rec))

    if not reus:
        reu_rec = buscar_recursivo(processo, ["reu", "réu", "requerido", "defendant"])
        if reu_rec:
            reus.append(limpar_texto(reu_rec))

    if not advogados:
        adv_rec = buscar_recursivo(processo, ["advogado", "advogados", "lawyer", "lawyers"])
        if adv_rec:
            advogados.append(limpar_texto(adv_rec))

    return {
        "autor": autores[0] if autores else "Não informado",
        "reu": reus[0] if reus else "Não informado",
        "advogado": ", ".join(list(dict.fromkeys(advogados))) if advogados else "Não informado",
    }


def formatar_processo(processo, fallback_tribunal="Não informado", cnj_forcado=None):
    props = processo.get("properties") or {}

    pessoas = extrair_pessoas(processo)

    numero = (
        cnj_forcado
        or props.get("cnj")
        or props.get("number")
        or props.get("numero")
        or props.get("numeroProcesso")
        or get_cover_value(processo, "Processo")
        or "Não informado"
    )

    origem = (
        props.get("origin")
        or props.get("origem")
        or get_cover_value(processo, "Órgão Julgador")
        or get_cover_value(processo, "Competência")
        or "Não informado"
    )

    assunto = (
        props.get("subjects")
        or props.get("subject")
        or props.get("assunto")
        or get_cover_value(processo, "Assuntos")
        or props.get("class")
        or "Não informado"
    )

    valor = (
        props.get("value")
        or props.get("valor")
        or props.get("valorCausa")
        or get_cover_value(processo, "Valor da causa")
        or "Não informado"
    )

    classe = (
        props.get("class")
        or props.get("classe")
        or get_cover_value(processo, "Classe")
        or "Não informado"
    )

    status = (
        props.get("status")
        or get_cover_value(processo, "Situação")
        or "Não informado"
    )

    data_autuacao = (
        props.get("startAt")
        or get_cover_value(processo, "Data de autuação")
        or "Não informado"
    )

    return (
        f"Prezado Cliente!\n\n"
        f"Autor: {pessoas['autor']}\n\n"
        f"CPF: Não informado\n\n"
        f"Réu: {pessoas['reu']}\n\n"
        f"Classe: {classe}\n\n"
        f"Assunto: {assunto}\n\n"
        f"Tribunal: {fallback_tribunal} - {origem}\n\n"
        f"Nº do processo: {numero}\n\n"
        f"Data de autuação: {data_autuacao}\n\n"
        f"Situação: {status}\n\n"
        f"Valor da causa: {valor}\n\n"
        f"Advogado: {pessoas['advogado']}"
    )


def formatar_capa_minima(cnj, tribunal="Não informado"):
    ano = extrair_ano_cnj(cnj)

    return (
        f"Prezado Cliente!\n\n"
        f"Autor: Não retornado pela API\n\n"
        f"CPF: Não informado\n\n"
        f"Réu: Não retornado pela API\n\n"
        f"Classe: Não retornado pela API\n\n"
        f"Assunto: Não retornado pela API\n\n"
        f"Tribunal: {tribunal}\n\n"
        f"Nº do processo: {cnj}\n\n"
        f"Ano do processo: {ano}\n\n"
        f"Valor da causa: Não retornado pela API\n\n"
        f"Advogado: Não retornado pela API\n\n"
        f"⚠️ A Codilo retornou o CNJ, mas não entregou a capa completa neste retorno."
    )


# ============================================================
# BUSCAS PRINCIPAIS
# ============================================================

def processar_rota_busca(item, valor):
    """
    Cria uma requisição Codilo para uma rota e espera por tempo limitado.
    Também faz fallback paginado no histórico, filtrando pela consulta exata.
    """
    request_id = create_request(item, valor)

    resultado = get_request_result(
        request_id,
        max_tentativas=max(1, int(CODILO_MAX_POLL_SECONDS / max(CODILO_POLL_INTERVAL, 1))),
        tempo_espera=CODILO_POLL_INTERVAL,
        max_seconds=CODILO_MAX_POLL_SECONDS,
    )

    cnjs = resultado.get("fallback_cnjs", []) if isinstance(resultado, dict) else []

    if not cnjs:
        cnjs = achar_cnjs_no_objeto(resultado)

    # Fallback paginado: útil quando GET /request/{id} vem vazio, mas o histórico já tem requests finalizadas.
    if not cnjs and not (isinstance(resultado, dict) and resultado.get("pending_timeout")):
        lista = listar_requests_fallback(item, valor=valor)
        cnjs = achar_cnjs_no_objeto(lista)

        # Se o histórico trouxe IDs exatos, tenta abrir cada um para extrair capa completa/CNJs.
        for req in lista.get("data", {}).get("result", [])[:20]:
            rid = req.get("id")
            if not rid or rid == request_id:
                continue
            detalhe = get_request_result(rid, max_tentativas=2, tempo_espera=2, max_seconds=8)
            cnjs.extend(achar_cnjs_no_objeto(detalhe))

    # Dedup local preservando ordem
    vistos = set()
    cnjs_final = []
    for c in cnjs:
        if c not in vistos:
            vistos.add(c)
            cnjs_final.append(c)

    return item, resultado, cnjs_final




def buscar_cnjs(valor, tipo, nacional=False):
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

    consultas = find_queries(param_keys, uf=uf, nacional=nacional)

    processos_por_ano = {}
    erros = []
    pendentes = []

    # Executa várias rotas em paralelo. Isso evita que uma rota lenta prenda a busca inteira.
    workers = max(1, min(CODILO_CONCURRENCY, len(consultas) or 1))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futuros = {
            executor.submit(processar_rota_busca, item, valor): item
            for item in consultas
        }

        for futuro in as_completed(futuros):
            item = futuros[futuro]

            try:
                item, resultado, cnjs = futuro.result()

                if isinstance(resultado, dict) and resultado.get("pending_timeout"):
                    pendentes.append(
                        f"{item.get('search')}/{item.get('query')}: ainda pending após {CODILO_MAX_POLL_SECONDS}s"
                    )

                if not cnjs:
                    raw = resultado.get("raw", resultado) if isinstance(resultado, dict) else resultado
                    status = get_status_request(raw)
                    if status and status not in ["success", "warning"]:
                        erros.append(f"{item.get('search')}/{item.get('query')}: status {status}")
                    continue

                adicionados, rejeitados = adicionar_cnjs_no_resultado(
                    processos_por_ano,
                    cnjs,
                    item,
                    uf=uf,
                    nacional=nacional,
                )

                if rejeitados:
                    erros.append(
                        f"{item.get('search')}/{item.get('query')}: {rejeitados} CNJ(s) fora do escopo filtrado"
                    )

            except Exception as e:
                erros.append(f"{item.get('search')}/{item.get('query')}: {str(e)[:180]}")
                continue


    # Varredura profunda final no histórico, página por página, para capturar CNJs que a rota criou
    # mas não retornou no polling principal. Filtrada pela consulta exata para evitar sujeira antiga.
    for item in consultas:
        try:
            lista = listar_requests_fallback(item, valor=valor)
            cnjs_hist = achar_cnjs_no_objeto(lista)
            if cnjs_hist:
                adicionar_cnjs_no_resultado(
                    processos_por_ano,
                    cnjs_hist,
                    item,
                    uf=uf,
                    nacional=nacional,
                )
        except Exception as e:
            erros.append(f"{item.get('search')}/{item.get('query')}: fallback profundo falhou {str(e)[:120]}")

    # Mostra pendentes no resumo de erros, mas não trava a entrega.
    erros.extend(pendentes[:20])

    return processos_por_ano, uf, erros


def buscar_detalhe_direto_por_tribunal(cnj, tribunal):
    if not tribunal or tribunal == "Não informado":
        return None

    consultas = find_queries_por_tribunal(["cnj"], tribunal)

    for item in consultas:
        try:
            request_id = create_request(item, cnj)
            resultado = get_request_result(request_id, max_tentativas=8, tempo_espera=8)

            raw = resultado.get("raw", resultado) if isinstance(resultado, dict) else resultado
            processos = extrair_lista_processos(raw)

            log_json("PROCESSOS EXTRAIDOS DETALHE DIRETO", processos)

            for processo in processos:
                if not isinstance(processo, dict):
                    continue

                cover = processo.get("cover", [])
                props = processo.get("properties", {})
                people = processo.get("people", [])
                steps = processo.get("steps", [])

                if cover or props or people or steps:
                    return formatar_processo(
                        processo,
                        fallback_tribunal=tribunal,
                        cnj_forcado=cnj,
                    )

        except Exception as e:
            print("ERRO detalhe direto:", str(e))
            continue

    return None


def criar_autorequest(cnj):
    payload = {
        "key": "cnj",
        "value": cnj,
"makeDownload": False,
        "callbacks": [],
    }

    response = requests.post(
        AUTOREQUEST_URL,
        headers=codilo_headers(),
        json=payload,
        timeout=60,
    )

    if response.status_code not in [200, 201]:
        raise Exception(f"AutoRequest {response.status_code}: {response.text[:500]}")

    data = response.json()
    log_json("CREATE AUTOREQUEST", data, 15000)

    auto_id = data.get("data", {}).get("id") or data.get("id")

    if not auto_id:
        raise Exception(f"AutoRequest sem ID: {data}")

    return auto_id


def consultar_autorequest(auto_id):
    url = f"{AUTOREQUEST_URL}/{auto_id}"
    requests_list = []

    for _ in range(10):
        response = requests.get(
            url,
            headers=codilo_headers(),
            timeout=60,
        )

        if response.status_code not in [200, 201]:
            raise Exception(f"Show AutoRequest {response.status_code}: {response.text[:500]}")

        data = response.json()
        log_json("GET AUTOREQUEST", data, 25000)

        requests_list = data.get("data", {}).get("requests", [])

        success_requests = [
            r for r in requests_list
            if str(r.get("status", "")).lower() in ["success", "completed", "done"]
        ]

        if success_requests:
            return success_requests, requests_list

        time.sleep(8)

    return [], requests_list


def buscar_detalhes_rapido(cnj, tribunal=None):
    cache_key = f"{cnj}:{tribunal or ''}"

    if cache_key in DETAIL_CACHE:
        return DETAIL_CACHE[cache_key]

    direto = buscar_detalhe_direto_por_tribunal(cnj, tribunal)

    if direto:
        DETAIL_CACHE[cache_key] = direto
        return direto

    try:
        auto_id = criar_autorequest(cnj)
        success_requests, all_requests = consultar_autorequest(auto_id)

        for req in success_requests:
            req_id = req.get("id")
            tribunal_req = req.get("court") or req.get("search") or tribunal or "Não informado"

            if not req_id:
                continue

            resultado = get_request_result(req_id, max_tentativas=6, tempo_espera=8)
            raw = resultado.get("raw", resultado) if isinstance(resultado, dict) else resultado
            processos = extrair_lista_processos(raw)

            log_json("PROCESSOS EXTRAIDOS AUTOREQUEST", processos)

            for processo in processos:
                if not isinstance(processo, dict):
                    continue

                cover = processo.get("cover", [])
                props = processo.get("properties", {})
                people = processo.get("people", [])
                steps = processo.get("steps", [])

                if cover or props or people or steps:
                    resposta = formatar_processo(
                        processo,
                        fallback_tribunal=tribunal_req,
                        cnj_forcado=cnj,
                    )

                    DETAIL_CACHE[cache_key] = resposta
                    return resposta

    except Exception as e:
        print("ERRO buscar_detalhes_rapido/autorequest:", str(e))

    resposta_minima = formatar_capa_minima(cnj, tribunal or "Não informado")
    DETAIL_CACHE[cache_key] = resposta_minima
    return resposta_minima


# ============================================================
# TELEGRAM HANDLERS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇧🇷 ConsultaBot V6 Online\n\n"
        "Comandos:\n"
        "/oab 123636--RS\n"
        "/nomeadv Nome do Advogado\n"
        "/nomeparte Nome da Parte\n\n"
        "Depois escolha o ano e clique no processo para ver detalhes."
    )


async def executar_busca_com_botoes(update: Update, context: ContextTypes.DEFAULT_TYPE, tipo: str, nacional: bool = False):
    valor = " ".join(context.args).strip()
    user_id = update.effective_user.id

    if not valor:
        await update.message.reply_text("Digite o termo da busca.")
        return

    if user_id in BUSCAS_ATIVAS:
        await update.message.reply_text(
            "⏳ Já existe uma consulta em andamento.\n\nAguarde finalizar."
        )
        return

    BUSCAS_ATIVAS.add(user_id)

    modo_txt = "nacional" if nacional else "regional"
    msg = await update.message.reply_text(
        f"🔎 Consulta {modo_txt} iniciada.\n\nEstou buscando processos e separando por ano."
    )

    try:
        processos_por_ano, uf, erros = await asyncio.to_thread(buscar_cnjs, valor, tipo, nacional)

        if not processos_por_ano:
            erro_exemplo = "\n".join(erros[:6]) if erros else "Sem erro detalhado."

            await msg.edit_text(
                "❌ Nenhum processo encontrado.\n\n"
                f"UF detectada: {uf or 'Não detectada'}\n"
                f"Falhas/sem retorno: {len(erros)}\n\n"
                f"Primeiros erros:\n{erro_exemplo}"
            )
            return

        USER_RESULTS[user_id] = {
            "valor": valor,
            "modo": "nacional" if nacional else "regional",
            "tipo": tipo,
            "uf": uf,
            "processos_por_ano": processos_por_ano,
            "created_at": time.time(),
        }

        anos = sorted(
            processos_por_ano.keys(),
            key=lambda x: int(x) if str(x).isdigit() else 0,
            reverse=True,
        )

        keyboard = []

        for ano in anos:
            qtd = len(processos_por_ano[ano])
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"📂 Processos {ano} ({qtd})",
                        callback_data=f"abrir_ano:{ano}:0",
                    )
                ]
            )

        total = sum(len(v) for v in processos_por_ano.values())

        texto = (
            f"✅ Busca concluída.\n\n"
            f"Resultados encontrados: {total}\n"
            f"UF detectada: {uf or 'Não aplicada'}\n\n"
            f"Escolha o ano abaixo para ver os processos:"
        )

        await msg.edit_text(texto, reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        await msg.edit_text(f"❌ Erro na busca:\n{str(e)}")

    finally:
        BUSCAS_ATIVAS.discard(user_id)


async def oab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await executar_busca_com_botoes(update, context, "oab", nacional=False)


async def oabnacional(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await executar_busca_com_botoes(update, context, "oab", nacional=True)


async def nomeadv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await executar_busca_com_botoes(update, context, "nomeadv")


async def nomeparte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await executar_busca_com_botoes(update, context, "nomeparte")


async def callback_ano(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Abrindo pasta...")

    user_id = query.from_user.id

    if not query.data.startswith("abrir_ano:"):
        return

    partes = query.data.split(":")
    ano = partes[1]
    pagina = int(partes[2]) if len(partes) > 2 and partes[2].isdigit() else 0

    session = USER_RESULTS.get(user_id)

    if not session:
        await query.edit_message_text("❌ Busca expirada. Faça uma nova consulta.")
        return

    processos_ano = sorted(
        list(session["processos_por_ano"].get(ano, {}).values()),
        key=lambda x: x["cnj"],
        reverse=True,
    )

    if not processos_ano:
        await query.edit_message_text("❌ Nenhum processo encontrado para esse ano.")
        return

    total = len(processos_ano)
    inicio = pagina * PAGE_SIZE
    fim = inicio + PAGE_SIZE
    itens = processos_ano[inicio:fim]

    texto = (
        f"📂 Processos do ano {ano}\n"
        f"Total encontrado: {total}\n"
        f"Página: {pagina + 1}\n\n"
        "Clique em um processo para ver detalhes:"
    )

    keyboard = []

    for i, item in enumerate(itens, start=inicio + 1):
        cnj = item["cnj"]
        tribunal = item.get("tribunal", "")

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🔎 {i}. {cnj}",
                    callback_data=f"detalhe:{cnj}:{tribunal}",
                )
            ]
        )

    nav = []

    if pagina > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️ Anterior",
                callback_data=f"abrir_ano:{ano}:{pagina - 1}",
            )
        )

    if fim < total:
        nav.append(
            InlineKeyboardButton(
                "➡️ Próxima",
                callback_data=f"abrir_ano:{ano}:{pagina + 1}",
            )
        )

    if nav:
        keyboard.append(nav)

    await query.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(keyboard))


async def callback_detalhe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Consultando detalhes...")

    partes = query.data.split(":")
    cnj = partes[1] if len(partes) > 1 else ""
    tribunal = partes[2] if len(partes) > 2 else ""

    await query.edit_message_text(
        f"🔎 Consultando capa do processo:\n{cnj}\n\nAguarde..."
    )

    try:
        resposta = await asyncio.to_thread(buscar_detalhes_rapido, cnj, tribunal)

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=str(resposta)[:4000],
        )

    except Exception as e:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"❌ Erro ao buscar detalhes.\n\n"
                f"Nº do processo: {cnj}\n"
                f"Erro: {str(e)[:500]}"
            ),
        )


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("oab", oab))
telegram_app.add_handler(CommandHandler("oabnacional", oabnacional))
telegram_app.add_handler(CommandHandler("nomeadv", nomeadv))
telegram_app.add_handler(CommandHandler("nomeparte", nomeparte))
telegram_app.add_handler(CallbackQueryHandler(callback_ano, pattern=r"^abrir_ano:"))
telegram_app.add_handler(CallbackQueryHandler(callback_detalhe, pattern=r"^detalhe:"))


# ============================================================
# WEBHOOK FASTAPI
# ============================================================

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()

    update_id = data.get("update_id")

    if update_id in UPDATES_PROCESSADOS:
        return {"ok": True, "duplicado": True}

    UPDATES_PROCESSADOS.add(update_id)

    if len(UPDATES_PROCESSADOS) > 1000:
        UPDATES_PROCESSADOS.clear()

    update = Update.de_json(data, telegram_app.bot)

    asyncio.create_task(telegram_app.process_update(update))

    return {"ok": True}


@app.get("/")
def home():
    return {"status": "ConsultaBot V6 Online"}


@app.on_event("startup")
async def startup():
    await telegram_app.initialize()

    if not RENDER_URL:
        print("ATENÇÃO: RENDER_URL não configurada.")
        return

    await telegram_app.bot.set_webhook(
        url=f"{RENDER_URL}/webhook",
        drop_pending_updates=True,
    )


@app.on_event("shutdown")
async def shutdown():
    await telegram_app.shutdown()
