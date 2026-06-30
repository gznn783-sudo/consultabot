import os
import re
import fitz
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN")
MODELO_PDF = "modelo.pdf"

CAMPOS = [
    "autor", "cpf", "processo", "assunto", "valor", "quitacao",
    "tribunal", "tabeliao", "pix", "banco", "agencia", "conta", "advogado"
]

def data_extenso():
    meses = [
        "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
    ]
    dias = [
        "Segunda-Feira", "Terça-Feira", "Quarta-Feira",
        "Quinta-Feira", "Sexta-Feira", "Sábado", "Domingo"
    ]

    hoje = datetime.now()
    return f"{dias[hoje.weekday()]} {hoje.day} de {meses[hoje.month - 1]} de {hoje.year}"

def ler_dados(texto):
    dados = {}
    for linha in texto.splitlines():
        if ":" in linha:
            chave, valor = linha.split(":", 1)
            chave = chave.strip().lower()
            valor = valor.strip()
            if chave in CAMPOS:
                dados[chave] = valor
    return dados

def substituir_texto(page, antigo, novo):
    areas = page.search_for(antigo)
    for area in areas:
        page.add_redact_annot(area, fill=(1, 1, 1))
    page.apply_redactions()

    for area in areas:
        page.insert_text(
            area.tl,
            novo,
            fontsize=11,
            fontname="helv",
            color=(0, 0, 0)
        )

def gerar_pdf(dados):
    doc = fitz.open(MODELO_PDF)

    substituicoes = {
        "Neusa Campos Prates": dados.get("autor", ""),
        "558.678.800-44": dados.get("cpf", ""),
        "5000667-82.2026.4.04.7131": dados.get("processo", ""),
        "Auxílio-Acidente (Art. 86),Benefícios em Espécie,DIREITO PREVIDENCIÁRIO": dados.get("assunto", ""),
        "R$ 35.057,30": dados.get("valor", ""),
        "R$ 1.183,50": dados.get("quitacao", ""),
        "TRF4 • Comarca • Soledade, RS": dados.get("tribunal", ""),
        "KARINE CAVALCANTE NUNES": dados.get("tabeliao", ""),
        "60520615379": dados.get("pix", ""),
        "536 - Neon Pagamentos": dados.get("banco", ""),
        "0655": dados.get("agencia", ""),
        "3631141-3": dados.get("conta", ""),
        "Marivone Hardt Betiollo": dados.get("advogado", ""),
        "Sexta - Feira 26 de Junho de 2026": data_extenso(),
    }

    for page in doc:
        for antigo, novo in substituicoes.items():
            if novo:
                substituir_texto(page, antigo, novo)

    nome_saida = f"documento_gerado_{datetime.now().strftime('%d%m%Y_%H%M%S')}.pdf"
    doc.save(nome_saida)
    doc.close()
    return nome_saida

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot online.\n\n"
        "Envie /gerar e depois mande os dados neste formato:\n\n"
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
        "advogado: Dra. Nome"
    )

async def gerar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["aguardando_dados"] = True
    await update.message.reply_text("Envie agora os dados do documento.")

async def receber_dados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("aguardando_dados"):
        return

    dados = ler_dados(update.message.text)

    faltando = [campo for campo in CAMPOS if campo not in dados]
    if faltando:
        await update.message.reply_text(
            "Faltam estes campos:\n" + "\n".join(faltando)
        )
        return

    await update.message.reply_text("Gerando PDF, aguarde...")

    try:
        arquivo = gerar_pdf(dados)

        with open(arquivo, "rb") as pdf:
            await update.message.reply_document(
                document=pdf,
                filename=arquivo,
                caption="PDF gerado com sucesso."
            )

        os.remove(arquivo)
        context.user_data["aguardando_dados"] = False

    except Exception as e:
        await update.message.reply_text(f"Erro ao gerar PDF: {e}")

def main():
    if not TOKEN:
        raise RuntimeError("Defina a variável BOT_TOKEN no Render.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("gerar", gerar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_dados))

    app.run_polling()

if __name__ == "__main__":
    main()
