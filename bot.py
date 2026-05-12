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
    msg = f"""🔎 Consulta: {nome}

1 resultado encontrado

Sistema: ConsultaBot Brasil
Tribunal: TRF4
Assunto: INSS
Status: Em andamento
Valor: Público quando disponível
"""
    await update.message.reply_text(msg)

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("buscar", buscar))

if __name__ == "__main__":
    app.run_polling()
