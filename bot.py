import os
import re
import time
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

TOKEN = os.getenv("BOT_TOKEN")
CODILO_TOKEN = os.getenv("CODILO_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {CODILO_TOKEN}",
    "Content-Type": "application/json"
}

BASE_URL = "https://api.consulta.codilo.com.br/v1/request"

ESTADOS = {
    "RS": ["tjrs", "trf4"],
    "SC": ["tjsc", "trf4"],
    "PR": ["tjpr", "trf4"],
    "SP": ["tjsp", "trf3"],
    "RJ": ["tjrj", "trf2"],
    "MG": ["tjmg", "trf6"],
    "BA": ["tjba", "trf1"],
    "CE": ["tjce", "trf5"],
}

# =========================================
# UTIL
# =========================================

def limpar_oab(oab):
    return re.sub(r'[^0-9]', '', oab)

def detectar_uf(texto):
    texto = texto.upper()

    for uf in ESTADOS.keys():
        if uf in texto:
            return uf

    return "RS"

def extrair_oab(texto):
    numeros = re.findall(r'\d+', texto)
    return numeros[0] if numeros else None

# =========================================
# CRIAR CONSULTA
# =========================================

def criar_consulta(search, oab, uf):
    payload = {
        "source": "courts",
        "platform": "esaj",
        "search": search,
        "query": "principal",
        "param": {
            "key": "oab",
            "value": f"{oab}/{uf}"
        }
    }

    try:
        response = requests.post(
            BASE_URL,
            headers=HEADERS,
            json=payload,
            timeout=60
        )

        data = response.json()

        print("CRIAR:", data)

        if data.get("success"):
            return data["data"]["id"]

    except Exception as e:
        print("ERRO CRIAR:", e)

    return None

# =========================================
# CONSULTAR POR ID
# =========================================

def consultar_request(request_id):
    try:
        response = requests.get(
            f"{BASE_URL}/{request_id}",
            headers=HEADERS,
            timeout=60
        )

        data = response.json()

        print("REQUEST:", data)

        return data

    except Exception as e:
        print("ERRO REQUEST:", e)

    return {}

# =========================================
# LISTAR REQUESTS
# =========================================

def listar_requests(search):
    try:
        response = requests.get(
            BASE_URL,
            headers=HEADERS,
            params={
                "source": "courts",
                "search": search,
                "success": "true",
                "limit": 25
            },
            timeout=60
        )

        data = response.json()

        print("LISTA:", data)

        return data

    except Exception as e:
        print("ERRO LISTA:", e)

    return {}

# =========================================
# EXTRAIR PROCESSOS
# =========================================

def extrair_processos(data):
    encontrados = []

    try:
        if isinstance(data, dict):

            # FORMATO REQUEST/{ID}
            if "data" in data and isinstance(data["data"], list):
                lista = data["data"]

            elif "data" in data and isinstance(data["data"], dict):
                lista = data["data"].get("data", [])

            else:
                lista = []

            for item in lista:

                props = item.get("properties", {})

                numero = (
                    props.get("cnj")
                    or props.get("number")
                    or "Sem número"
                )

                classe = props.get("class", "Não informado")
                origem = props.get("origin", "Não informado")
                valor = props.get("value", "Não informado")

                encontrados.append({
                    "numero": numero,
                    "classe": classe,
                    "origem": origem,
                    "valor": valor
                })

    except Exception as e:
        print("ERRO EXTRAIR:", e)

    return encontrados

# =========================================
# CONSULTA PRINCIPAL
# =========================================

async def consultar_oab(update: Update, context: ContextTypes.DEFAULT_TYPE):

    texto = update.message.text.upper()

    uf = detectar_uf(texto)
    oab = extrair_oab(texto)

    if not oab:
        await update.message.reply_text("❌ OAB inválida.")
        return

    await update.message.reply_text(
        f"🔎 Consultando OAB {oab}/{uf}..."
    )

    tribunais = ESTADOS.get(uf, ["tjrs"])

    processos = []
    erros = []

    for tribunal in tribunais:

        try:

            request_id = criar_consulta(
                tribunal,
                limpar_oab(oab),
                uf
            )

            if not request_id:
                erros.append(f"{tribunal}: erro criar request")
                continue

            encontrado = False

            # =================================
            # POLLING
            # =================================

            for tentativa in range(15):

                time.sleep(8)

                resultado = consultar_request(request_id)

                status = (
                    resultado.get("data", {})
                    .get("status", "")
                )

                print("STATUS:", status)

                if status == "success":

                    extraidos = extrair_processos(resultado)

                    if extraidos:
                        processos.extend(extraidos)
                        encontrado = True
                        break

                elif status == "warning":
                    break

            # =================================
            # FALLBACK LISTAGEM
            # =================================

            if not encontrado:

                lista = listar_requests(tribunal)

                results = (
                    lista.get("data", {})
                    .get("result", [])
                )

                for item in results:

                    props = item.get("properties", {})

                    numero = (
                        props.get("cnj")
                        or props.get("number")
                    )

                    if numero:

                        processos.append({
                            "numero": numero,
                            "classe": props.get("class", ""),
                            "origem": props.get("origin", ""),
                            "valor": props.get("value", "")
                        })

        except Exception as e:
            erros.append(f"{tribunal}: {str(e)}")

    # REMOVE DUPLICADOS

    unicos = {}
    for p in processos:
        unicos[p["numero"]] = p

    processos = list(unicos.values())

    # =========================================
    # RESPOSTA
    # =========================================

    if not processos:

        texto = (
            "❌ Nenhum processo encontrado.\n\n"
            f"UF detectada: {uf}\n"
            f"Falhas/sem retorno: {len(erros)}"
        )

        if erros:
            texto += "\n\nPrimeiros erros:\n"
            texto += "\n".join(erros[:3])

        await update.message.reply_text(texto)
        return

    keyboard = []

    for i, proc in enumerate(processos):

        ano = "----"

        match = re.search(r'\d{4}', proc["numero"])
        if match:
            ano = match.group()

        keyboard.append([
            InlineKeyboardButton(
                f"{proc['numero']} ({ano})",
                callback_data=f"proc_{i}"
            )
        ])

    context.user_data["processos"] = processos

    await update.message.reply_text(
        f"✅ {len(processos)} processo(s) encontrado(s):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# =========================================
# DETALHES
# =========================================

async def detalhes_processo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    idx = int(query.data.split("_")[1])

    processos = context.user_data.get("processos", [])

    if idx >= len(processos):
        return

    proc = processos[idx]

    texto = (
        f"📄 Processo:\n\n"
        f"🔹 Número: {proc['numero']}\n"
        f"🔹 Classe: {proc['classe']}\n"
        f"🔹 Origem: {proc['origem']}\n"
        f"💰 Valor: {proc['valor']}"
    )

    await query.message.reply_text(texto)

# =========================================
# START
# =========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 ConsultaBot Brasil Online\n\n"
        "Envie uma OAB.\n"
        "Exemplo:\n"
        "123456 RS"
    )

# =========================================
# MAIN
# =========================================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))

app.add_handler(
    CallbackQueryHandler(
        detalhes_processo,
        pattern="^proc_"
    )
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        consultar_oab
    )
)

print("BOT ONLINE")

app.run_polling()
