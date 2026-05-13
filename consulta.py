import requests
from datetime import datetime


# 🔎 BUSCA DE PROCESSOS (CNJ / DataJud)
def buscar_processos_nome(nome):
    url = "https://api-publica.datajud.cnj.jus.br/api_publica_processos/_search"

    payload = {
        "query": {
            "match": {
                "partes.nome": nome
            }
        }
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        data = r.json()

        processos = []

        for hit in data.get("hits", {}).get("hits", []):
            p = hit.get("_source", {})

            processos.append({
                "numero": p.get("numeroProcesso"),
                "tribunal": p.get("tribunal"),
                "classe": p.get("classeJudicial"),
                "assunto": p.get("assuntoPrincipal"),
                "partes": p.get("partes"),
                "dataAjuizamento": p.get("dataAjuizamento")
            })

        return processos

    except Exception:
        return []


# 📅 ORDENAR DO MAIS RECENTE → MAIS ANTIGO
def ordenar_por_data(processos):
    def pegar_data(p):
        d = p.get("dataAjuizamento")

        try:
            if not d:
                return datetime.min
            return datetime.fromisoformat(d.replace("Z", ""))
        except:
            return datetime.min

    return sorted(processos, key=pegar_data, reverse=True)
