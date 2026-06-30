import os
import re
import fitz
import locale
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

MODELO_PDF = "modelo.pdf"

app = FastAPI()
telegram_app = Application.builder().token(BOT_TOKEN).build()


CAMPOS = [
    "autor",
    "cpf",
    "processo",
    "assunto",
    "valor",
    "quitacao",
    "tribunal",
    "tabeliao",
    "pix",
    "banco",
    "agencia",
    "conta",
    "advogado",
]


def data_extenso():
    dias = [
        "Segunda-Feira",
        "Terça-Feira",
        "Quarta-Feira",
        "Quinta-Feira",
        "Sexta-Feira",
        "Sábado",
        "Domingo",
    ]

    meses = [
        "Janeiro",
        "Fevereiro",
        "Março",
        "Abril",
        "Maio",
        "Junho",
        "Julho",
        "Agosto",
        "Setembro",
        "Outubro",
        "Novembro",
        "Dezembro",
    ]

    hoje = datetime.now()
    return f"{dias[hoje.weekday()]} {hoje.day} de {meses[hoje.month - 1]} de {hoje.year}"


def ler_dados(texto):
    dados = {}

    for linha in texto.splitlines():
        if ":" not in linha:
            continue

        chave, valor = linha.split(":", 1)
        chave = chave.strip().lower()
        valor = valor.strip()

        if chave in CAMPOS:
            dados[chave] = valor

    return dados


def apagar_area(page, rect):
    page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))


def escrever(page, x, y, texto, tamanho=11, negrito=False):
    fonte = "helv"
    if negrito:
        fonte = "helv"

    page.insert_text(
        (x, y),
        texto,
        fontsize=tamanho,
        fontname=fonte,
        color=(0, 0, 0),
    )


def gerar_pdf(dados):
    if not os.path.exists(MODELO_PDF):
        raise FileNotFoundError("O arquivo modelo.pdf não foi encontrado no projeto.")

    doc = fitz.open(MODELO_PDF)

    p1 = doc[0]
    p2 = doc[1]

    data_atual = data_extenso()

    # Página 1 — apaga campos antigos
    apagar_area(p1, fitz.Rect(120, 338, 290, 356))   # autor
    apagar_area(p1, fitz.Rect(108, 354, 230, 374))   # cpf
    apagar_area(p1, fitz.Rect(300, 398, 470, 418))   # processo
    apagar_area(p1, fitz.Rect(230, 430, 430, 448))   # tribunal/comarca
    apagar_area(p1, fitz.Rect(232, 446, 320, 464))   # valor
    apagar_area(p1, fitz.Rect(180, 232, 430, 252))   # data

    # Página 1 — escreve novos campos
    escrever(p1, 124, 351, dados["autor"], 11)
    escrever(p1, 110, 368, dados["cpf"], 11)
    escrever(p1, 302, 412, dados["processo"], 10)
    escrever(p1, 232, 443, dados["tribunal"], 10)
    escrever(p1, 235, 459, dados["valor"], 10)
    escrever(p1, 184, 247, data_atual, 12)

    # Página 2 — apaga campos antigos
    apagar_area(p2, fitz.Rect(70, 92, 230, 110))      # autor
    apagar_area(p2, fitz.Rect(52, 105, 165, 124))     # cpf
    apagar_area(p2, fitz.Rect(88, 118, 500, 148))     # assunto
    apagar_area(p2, fitz.Rect(65, 145, 165, 165))     # valor
    apagar_area(p2, fitz.Rect(265, 185, 360, 205))    # quitacao
    apagar_area(p2, fitz.Rect(72, 240, 290, 258))     # tabeliao
    apagar_area(p2, fitz.Rect(130, 253, 225, 272))    # pix
    apagar_area(p2, fitz.Rect(175, 266, 350, 286))    # banco
    apagar_area(p2, fitz.Rect(86, 280, 130, 300))     # agencia
    apagar_area(p2, fitz.Rect(73, 293, 160, 314))     # conta
    apagar_area(p2, fitz.Rect(43, 400, 250, 418))     # tribunal/comarca
    apagar_area(p2, fitz.Rect(28, 674, 200, 694))     # advogado

    # Página 2 — escreve novos campos
    escrever(p2, 72, 105, dados["autor"], 11)
    escrever(p2, 54, 119, dados["cpf"], 11)
    escrever(p2, 89, 132, dados["assunto"], 10)
    escrever(p2, 66, 159, dados["valor"], 11)
    escrever(p2, 267, 200, dados["quitacao"], 11)
    escrever(p2, 73, 254, dados["tabeliao"], 11)
    escrever(p2, 132, 267, dados["pix"], 11)
    escrever(p2, 177, 281, dados["banco"], 11)
    escrever(p2, 88, 294, dados["agencia"], 11)
    escrever(p2, 74, 308, dados["conta"], 11)
    escrever(p2, 44, 414, dados["tribunal"], 10)
    escrever(p2, 29, 688, dados["advogado"], 11)

    nome_limpo = re.sub(r"[^a-zA-Z0-9_-]", "_", dados["autor"])[:40]
    saida = f"documento_{nome_limpo}_{datetime.now().strftime('%d%m%Y_%H%M%S')}.pdf"

    doc.save(saida)
    doc.close()

    return saida


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot de PDF online.\n\n"
        "Use /gerar para iniciar."
    )


async def gerar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["aguardando"] = True

    await update.message.reply_text(
        "Envie os dados neste formato:\n\n"
        "autor: João da Silva\n"
        "cpf: 000.000.000-00\n"
        "processo: 5000000-00.2026.8.21.0000\n"
        "assunto: Auxílio-Acidente\n"
        "valor: R$ 35.057,30\n"
        "quitacao: R$ 1.183,50\n"
        "tribunal: TJRS • Comarca • Porto Alegre, RS\n"
        "tabeliao: Nome do prestador\n"
        "pix: 00000000000\n"
        "banco: 000 - Banco\n"
        "agencia: 0000\n"
        "conta: 000000-0\n"
        "advogado: Nome do advogado"
    )


async def receber_dados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("aguardando"):
        return

    dados = ler_dados(update.message.text)

    faltando = [campo for campo in CAMPOS if campo not in dados]

    if faltando:
        await update.message.reply_text(
            "Faltam estes campos:\n\n" + "\n".join(faltando)
        )
        return

    await update.message.reply_text("Gerando PDF...")

    try:
        arquivo = gerar_pdf(dados)

        with open(arquivo, "rb") as pdf:
            await update.message.reply_document(
                document=pdf,
                filename=arquivo,
                caption="PDF gerado com sucesso."
            )

        os.remove(arquivo)
        context.user_data.clear()

    except Exception as e:
        await update.message.reply_text(f"Erro ao gerar PDF: {e}")


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("gerar", gerar))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_dados))


@app.on_event("startup")
async def startup():
    await telegram_app.initialize()
    await telegram_app.start()

    if WEBHOOK_URL:
        await telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")


@app.on_event("shutdown")
async def shutdown():
    await telegram_app.stop()
    await telegram_app.shutdown()


@app.get("/")
async def home():
    return {"status": "Bot PDF online"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
