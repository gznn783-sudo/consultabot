import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# =========================
# CONFIGURACAO
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")

BASE_DIR = Path(__file__).resolve().parent
MODELO_PDF = BASE_DIR / "modelo.pdf"

app = FastAPI(title="Bot PDF")
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

EXEMPLO = """autor: João da Silva
cpf: 000.000.000-00
processo: 5000000-00.2026.8.21.0000
assunto: Aposentadoria por Idade Rural, Benefícios em Espécie, DIREITO PREVIDENCIÁRIO
valor: R$ 35.057,30
quitacao: R$ 1.183,50
tribunal: TRF4 • Comarca • Porto Alegre, RS
tabeliao: Nome do prestador
pix: 00000000000
banco: 000 - Banco
agencia: 0000
conta: 000000-0
advogado: Nome do advogado"""


# =========================
# FUNCOES AUXILIARES
# =========================
def data_extenso() -> str:
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


def ler_dados(texto: str) -> dict:
    dados = {}
    for linha in texto.splitlines():
        if ":" not in linha:
            continue
        chave, valor = linha.split(":", 1)
        chave = chave.strip().lower()
        valor = valor.strip()
        if chave in CAMPOS and valor:
            dados[chave] = valor
    return dados


def nome_arquivo_seguro(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9_-]+", "_", texto).strip("_")
    return texto[:45] or "documento"


def limpar(page: fitz.Page, rect: fitz.Rect) -> None:
    # Retangulo branco por cima do texto antigo.
    page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)


def escrever_box(
    page: fitz.Page,
    rect: fitz.Rect,
    texto: str,
    tamanho: float = 10,
    align: int = 0,
    min_size: float = 7,
) -> None:
    """
    Escreve texto dentro de uma caixa. Se não couber, reduz a fonte automaticamente.
    align: 0 esquerda, 1 centro, 2 direita.
    """
    texto = str(texto or "")
    fonte = "helv"
    size = tamanho
    while size >= min_size:
        resultado = page.insert_textbox(
            rect,
            texto,
            fontsize=size,
            fontname=fonte,
            color=(0, 0, 0),
            align=align,
        )
        if resultado >= 0:
            return
        # Se não coube, apaga a tentativa e tenta menor.
        limpar(page, rect)
        size -= 0.5

    page.insert_textbox(
        rect,
        texto,
        fontsize=min_size,
        fontname=fonte,
        color=(0, 0, 0),
        align=align,
    )


# =========================
# GERADOR DO PDF
# =========================
def gerar_pdf(dados: dict) -> Path:
    if not MODELO_PDF.exists():
        raise FileNotFoundError("Arquivo modelo.pdf não encontrado. Envie o modelo para a raiz do projeto.")

    doc = fitz.open(str(MODELO_PDF))
    if len(doc) < 2:
        doc.close()
        raise RuntimeError("O modelo.pdf precisa ter pelo menos 2 páginas.")

    p1 = doc[0]
    p2 = doc[1]
    data_atual = data_extenso()

    # =========================================================
    # PAGINA 1 - LIMPEZA DE AREAS MAIORES
    # =========================================================
    limpar(p1, fitz.Rect(250, 232, 585, 258))   # data abaixo da edicao
    limpar(p1, fitz.Rect(135, 335, 430, 358))   # autor
    limpar(p1, fitz.Rect(135, 352, 310, 377))   # cpf
    limpar(p1, fitz.Rect(360, 398, 650, 420))   # processo
    limpar(p1, fitz.Rect(335, 430, 720, 452))   # tribunal/comarca
    limpar(p1, fitz.Rect(282, 445, 430, 468))   # valor

    # PAGINA 1 - ESCRITA
    escrever_box(p1, fitz.Rect(250, 235, 585, 258), data_atual, 12, align=1)
    escrever_box(p1, fitz.Rect(137, 339, 430, 358), dados["autor"], 11)
    escrever_box(p1, fitz.Rect(137, 356, 310, 377), dados["cpf"], 11)
    escrever_box(p1, fitz.Rect(365, 401, 650, 420), dados["processo"], 10)
    escrever_box(p1, fitz.Rect(338, 432, 720, 452), dados["tribunal"], 10)
    escrever_box(p1, fitz.Rect(286, 448, 430, 468), dados["valor"], 10)

    # =========================================================
    # PAGINA 2 - LIMPEZA MAIS AMPLA PARA EVITAR TEXTO MISTURADO
    # =========================================================
    limpar(p2, fitz.Rect(48, 90, 735, 170))      # autor, cpf, assunto, valor
    limpar(p2, fitz.Rect(48, 183, 735, 210))     # custo/quitacao
    limpar(p2, fitz.Rect(48, 235, 560, 325))     # dados tabeliao
    limpar(p2, fitz.Rect(48, 392, 735, 430))     # item 1 tribunal
    limpar(p2, fitz.Rect(48, 675, 420, 710))     # advogado

    # PAGINA 2 - CABECALHO/DADOS
    escrever_box(p2, fitz.Rect(48, 92, 735, 110), f"Autor: {dados['autor']}", 11)
    escrever_box(p2, fitz.Rect(48, 108, 735, 126), f"Cpf:{dados['cpf']}", 11)
    escrever_box(p2, fitz.Rect(48, 124, 735, 156), f"Assunto: {dados['assunto']}", 9.5)
    escrever_box(p2, fitz.Rect(48, 155, 300, 173), f"Valor:{dados['valor']}", 11)

    escrever_box(
        p2,
        fitz.Rect(48, 185, 735, 208),
        f"Custa do serviço: (valor para quitação {dados['quitacao']})",
        11,
    )

    # PAGINA 2 - DADOS DO TABELIAO/PRESTADOR
    escrever_box(p2, fitz.Rect(48, 238, 560, 258), f"Nome: {dados['tabeliao']}", 11)
    escrever_box(p2, fitz.Rect(48, 254, 560, 274), f"Chave PIX/CPF: {dados['pix']}", 11)
    escrever_box(p2, fitz.Rect(48, 270, 560, 290), f"Número da instituição: {dados['banco']}", 11)
    escrever_box(p2, fitz.Rect(48, 286, 560, 306), f"Agência: {dados['agencia']}", 11)
    escrever_box(p2, fitz.Rect(48, 302, 560, 322), f"Conta: {dados['conta']}", 11)

    # PAGINA 2 - ITEM 1
    texto_item_1 = (
        f"1- {dados['tribunal']} ao recolhimento das custas judiciais devidas "
        "decorrentes dos atos realizados no trâmite do processo."
    )
    escrever_box(p2, fitz.Rect(48, 395, 735, 430), texto_item_1, 10)

    # PAGINA 2 - ADVOGADO
    escrever_box(p2, fitz.Rect(48, 683, 420, 710), dados["advogado"], 11)

    saida = BASE_DIR / f"documento_{nome_arquivo_seguro(dados['autor'])}_{datetime.now().strftime('%d%m%Y_%H%M%S')}.pdf"
    doc.save(str(saida), garbage=4, deflate=True)
    doc.close()
    return saida


# =========================
# HANDLERS TELEGRAM
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot de PDF online.\n\n"
        "Use /gerar e envie os dados do documento."
    )


async def gerar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["aguardando_dados"] = True
    await update.message.reply_text(
        "Envie os dados neste formato:\n\n" + EXEMPLO
    )


async def cancelar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Operação cancelada.")


async def receber_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("aguardando_dados"):
        await update.message.reply_text("Use /gerar para iniciar.")
        return

    dados = ler_dados(update.message.text or "")
    faltando = [campo for campo in CAMPOS if campo not in dados]

    if faltando:
        await update.message.reply_text(
            "Faltam estes campos:\n\n" + "\n".join(faltando) + "\n\nEnvie novamente todos os dados."
        )
        return

    await update.message.reply_text("Gerando PDF, aguarde...")

    try:
        arquivo = gerar_pdf(dados)
        with arquivo.open("rb") as pdf:
            await update.message.reply_document(
                document=pdf,
                filename=arquivo.name,
                caption="PDF gerado com sucesso.",
            )
        try:
            arquivo.unlink()
        except Exception:
            pass
        context.user_data.clear()
    except Exception as e:
        await update.message.reply_text(f"Erro ao gerar PDF: {e}")


telegram_app.add_handler(CommandHandler("start", start_cmd))
telegram_app.add_handler(CommandHandler("gerar", gerar_cmd))
telegram_app.add_handler(CommandHandler("cancelar", cancelar_cmd))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_texto))


# =========================
# FASTAPI / WEBHOOK RENDER
# =========================
@app.on_event("startup")
async def startup_event():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN não foi definido nas variáveis de ambiente.")

    await telegram_app.initialize()
    await telegram_app.start()

    if WEBHOOK_URL:
        await telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")


@app.on_event("shutdown")
async def shutdown_event():
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
