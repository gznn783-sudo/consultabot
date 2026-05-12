import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🇧🇷 ConsultaBot Brasil online!\nUse /buscar Nome Sobrenome")

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = " ".join(context.args).strip()
    if not nome:
        await update.message.reply_text("Use: /buscar Nome Sobrenome")
        return
    await update.message.reply_text(f"🔎 Consulta: {nome}\n\nSistema: ConsultaBot Brasil\nTribunal: TRF4\nAssunto: INSS\nStatus: Em andamento\nValor: Público quando disponível")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("buscar", buscar))
app.run_polling()
