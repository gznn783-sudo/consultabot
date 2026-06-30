import os
import re
import textwrap
from datetime import datetime
from io import BytesIO

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("Defina BOT_TOKEN nas variáveis de ambiente do Render.")

app = FastAPI(title="Gerador de PDF")
telegram_app = Application.builder().token(BOT_TOKEN).build()

CAMPOS = [
    "autor", "cpf", "processo", "assunto", "valor", "quitacao", "tribunal",
    "tabeliao", "pix", "banco", "agencia", "conta", "advogado"
]

EXEMPLO = """autor: João da Silva
cpf: 000.000.000-00
processo: 5000000-00.2026.8.21.0000
assunto: Aposentadoria por Idade Rural, Benefícios em Espécie, Direito Previdenciário
valor: R$ 32.703,63
quitacao: R$ 1.867,50
tribunal: TRF4 · Comarca · Porto Alegre, RS
tabeliao: João Ferreira Neto
pix: 00000000000
banco: 536 - Neon Pagamentos
agencia: 0000
conta: 0000000-0
advogado: Vanessa La Cruz Bueno"""


def normalizar_chave(chave: str) -> str:
    chave = chave.strip().lower()
    chave = chave.replace("valor para quitação", "quitacao")
    chave = chave.replace("valor para quitacao", "quitacao")
    chave = chave.replace("quitação", "quitacao")
    chave = chave.replace("quitacao", "quitacao")
    chave = chave.replace("número do processo", "processo")
    chave = chave.replace("numero do processo", "processo")
    chave = chave.replace("n processo", "processo")
    chave = chave.replace("processo", "processo")
    chave = chave.replace("cpf", "cpf")
    chave = chave.replace("nome do autor", "autor")
    chave = chave.replace("autor", "autor")
    chave = chave.replace("comarca", "tribunal") if chave == "comarca" else chave
    chave = chave.replace("tabelião", "tabeliao")
    chave = chave.replace("tabeliao", "tabeliao")
    chave = chave.replace("prestador", "tabeliao")
    chave = chave.replace("chave pix", "pix")
    chave = chave.replace("pix/cpf", "pix")
    chave = chave.replace("banco", "banco")
    chave = chave.replace("instituição", "banco")
    chave = chave.replace("instituicao", "banco")
    chave = chave.replace("agência", "agencia")
    chave = chave.replace("agencia", "agencia")
    chave = chave.replace("advogado responsável", "advogado")
    chave = chave.replace("advogado responsavel", "advogado")
    chave = chave.replace("advogado", "advogado")
    return chave


def ler_dados(texto: str) -> dict:
    dados = {}
    for linha in texto.splitlines():
        if ":" not in linha:
            continue
        chave, valor = linha.split(":", 1)
        chave = normalizar_chave(chave)
        valor = valor.strip()
        if chave in CAMPOS and valor:
            dados[chave] = valor
    return dados


def data_extenso() -> str:
    dias = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
    hoje = datetime.now()
    return f"{dias[hoje.weekday()]}, {hoje.day} de {meses[hoje.month - 1]} de {hoje.year}"


def limpar_nome_arquivo(nome: str) -> str:
    nome = re.sub(r"[^a-zA-Z0-9À-ÿ _.-]", "", nome).strip()
    nome = re.sub(r"\s+", "_", nome)
    return nome[:60] or "documento"


def draw_text(c: canvas.Canvas, x: float, y: float, text: str, size=10, bold=False):
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, size)
    c.drawString(x, y, str(text))


def draw_center(c: canvas.Canvas, y: float, text: str, size=12, bold=False):
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, size)
    width, _ = A4
    c.drawCentredString(width / 2, y, str(text))


def wrap_text(text: str, max_width: float, font="Helvetica", size=10):
    words = str(text).split()
    lines = []
    current = ""
    for word in words:
        test = word if not current else current + " " + word
        if stringWidth(test, font, size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_wrapped(c: canvas.Canvas, x: float, y: float, text: str, max_width: float, size=10, bold=False, leading=None, max_lines=None):
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, size)
    leading = leading or size + 3
    lines = wrap_text(text, max_width, font, size)
    if max_lines:
        lines = lines[:max_lines]
    for i, line in enumerate(lines):
        c.drawString(x, y - i * leading, line)
    return y - len(lines) * leading


def draw_watermark(c: canvas.Canvas):
    width, height = A4
    c.saveState()
    c.setFont("Helvetica-Bold", 38)
    c.setFillColor(colors.Color(0.82, 0.82, 0.82, alpha=0.22))
    c.translate(width / 2, height / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, "DOCUMENTO INFORMATIVO")
    c.restoreState()


def draw_header(c: canvas.Canvas, title="DIÁRIO ELETRÔNICO DA JUSTIÇA"):
    width, height = A4
    draw_watermark(c)
    draw_center(c, height - 78 * mm, title, 16, True)
    draw_center(c, height - 90 * mm, f"Edição gerada em {datetime.now().year}", 13, True)
    draw_center(c, height - 101 * mm, data_extenso(), 11, False)
    draw_center(c, height - 118 * mm, "DESPACHO/DECISÃO", 14, True)


def gerar_pdf(dados: dict) -> str:
    saida = f"Cumprimento_de_sentenca_{limpar_nome_arquivo(dados['autor'])}_{datetime.now().strftime('%d%m%Y_%H%M%S')}.pdf"
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margem = 24 * mm

    # Página 1
    draw_header(c)

    y = height - 150 * mm
    draw_text(c, margem, y, "Autor:", 12, True)
    draw_text(c, margem + 24 * mm, y, dados["autor"], 12)
    y -= 8 * mm
    draw_text(c, margem, y, "Cpf:", 12, True)
    draw_text(c, margem + 18 * mm, y, dados["cpf"], 12)

    y -= 24 * mm
    texto = (
        f"AUTORIZAÇÃO DE PROCESSO: {dados['processo']}  "
        f"Diante dos argumentos apresentados e em cumprimento às informações repassadas, "
        f"fica registrado que o pagamento no valor de ({dados['valor']}) deve ser realizado "
        f"para fins de regularização das pendências informadas."
    )
    draw_wrapped(c, margem, y, texto, width - 2 * margem, 11, False, leading=15)

    y = 70 * mm
    draw_text(c, margem, y, "Observação:", 10, True)
    draw_wrapped(
        c,
        margem + 26 * mm,
        y,
        "Documento informativo interno, sem valor de decisão judicial, certidão, intimação ou publicação oficial.",
        width - 2 * margem - 26 * mm,
        10,
        False,
        leading=13,
    )
    c.line(margem, 50 * mm, width - margem, 50 * mm)
    draw_center(c, 42 * mm, "Documento gerado automaticamente para conferência das informações", 9, False)
    c.showPage()

    # Página 2
    draw_watermark(c)
    draw_center(c, height - 32 * mm, "DOCUMENTO DE PRESTAÇÃO DE SERVIÇO", 16, True)

    y = height - 54 * mm
    draw_text(c, margem, y, "Autor:", 12, True)
    draw_text(c, margem + 23 * mm, y, dados["autor"], 12)
    y -= 8 * mm
    draw_text(c, margem, y, "Cpf:", 12, True)
    draw_text(c, margem + 17 * mm, y, dados["cpf"], 12)
    y -= 8 * mm
    draw_text(c, margem, y, "Assunto:", 12, True)
    y = draw_wrapped(c, margem + 31 * mm, y, dados["assunto"], width - margem - (margem + 31 * mm), 10, False, leading=12)
    y -= 2 * mm
    draw_text(c, margem, y, "Valor:", 12, True)
    draw_text(c, margem + 22 * mm, y, dados["valor"], 12)
    y -= 8 * mm
    draw_text(c, margem, y, "Pendências:", 12, True)
    draw_wrapped(c, margem + 35 * mm, y, "Certidão negativa conjunta, selos, autenticação e cópias das folhas do processo.", width - margem - (margem + 35 * mm), 10, False, leading=12)
    y -= 16 * mm
    draw_text(c, margem, y, "Custa do serviço:", 12, True)
    draw_text(c, margem + 45 * mm, y, f"valor para quitação {dados['quitacao']}", 12)

    y -= 18 * mm
    draw_text(c, margem, y, "Dados do prestador para prestação de serviço", 13, True)
    y -= 13 * mm
    linhas = [
        ("Nome:", dados["tabeliao"]),
        ("Chave PIX/CPF:", dados["pix"]),
        ("Número da instituição:", dados["banco"]),
        ("Agência:", dados["agencia"]),
        ("Conta:", dados["conta"]),
    ]
    for label, valor in linhas:
        draw_text(c, margem, y, label, 11, True)
        draw_text(c, margem + 45 * mm, y, valor, 11)
        y -= 7 * mm

    y -= 30 * mm
    par1 = f"1- {dados['tribunal']} ao recolhimento das custas e despesas informadas, decorrentes dos atos realizados no trâmite do processo."
    y = draw_wrapped(c, margem, y, par1, width - 2 * margem, 11, True, leading=14)
    y -= 8 * mm
    par2 = (
        "2- Este documento é uma prestação informativa de serviço e deve ser conferido antes de qualquer envio ao cliente. "
        "Os dados bancários e processuais são preenchidos conforme as informações encaminhadas pelo usuário."
    )
    y = draw_wrapped(c, margem, y, par2, width - 2 * margem, 11, True, leading=14)

    y = 53 * mm
    draw_text(c, margem, y, "Advogado(a) Responsável:", 12, True)
    y -= 8 * mm
    draw_text(c, margem, y, dados["advogado"], 12)
    c.line(margem, 30 * mm, width - margem, 30 * mm)
    draw_center(c, 22 * mm, "Documento informativo interno - sem valor oficial", 9, True)

    c.save()
    with open(saida, "wb") as f:
        f.write(buffer.getvalue())
    return saida


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot de geração de PDF online.\n\nUse /novo para gerar um documento."
    )


async def novo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["aguardando_dados"] = True
    await update.message.reply_text(
        "Envie os dados neste formato:\n\n" + EXEMPLO
    )


async def receber_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("aguardando_dados"):
        await update.message.reply_text("Use /novo para iniciar a geração do PDF.")
        return

    dados = ler_dados(update.message.text or "")
    faltando = [campo for campo in CAMPOS if campo not in dados]
    if faltando:
        await update.message.reply_text("Faltam estes campos:\n\n" + "\n".join(faltando) + "\n\nEnvie novamente todos os dados.")
        return

    await update.message.reply_text("Gerando PDF limpo, aguarde...")
    try:
        arquivo = gerar_pdf(dados)
        with open(arquivo, "rb") as pdf:
            await update.message.reply_document(document=pdf, filename=arquivo, caption="PDF gerado com sucesso.")
        os.remove(arquivo)
        context.user_data.clear()
    except Exception as exc:
        await update.message.reply_text(f"Erro ao gerar PDF: {exc}")


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("novo", novo))
telegram_app.add_handler(CommandHandler("gerar", novo))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_texto))


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
    return {"status": "online", "service": "gerador-pdf"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
