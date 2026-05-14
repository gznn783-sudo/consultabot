async def mapacodilo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        available = get_available()

        tags_alvo = {
            "oab",
            "nomeadv",
            "nomeadvogado",
            "nomeparte",
            "nome",
            "doc",
            "cnj"
        }

        linhas = []
        consultas = extrair_queries_disponiveis(
            available,
            list(tags_alvo)
        )

        for c in consultas:
            linhas.append(
                f"{c['search']}/{c['query']}/{c['param_key']} | platform={c['platform']}"
            )

        if not linhas:
            await update.message.reply_text("❌ Nenhuma combinação encontrada no /available.")
            return

        texto = "📌 Combinações disponíveis:\n\n" + "\n".join(linhas[:80])

        await update.message.reply_text(texto[:4000])

    except Exception as e:
        await update.message.reply_text(f"❌ Erro no mapa Codilo:\n{str(e)}")
