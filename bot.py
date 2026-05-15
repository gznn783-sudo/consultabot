# bot.py
# ConsultaBot - versão ajustada Codilo API

import os
import time
import requests

API_TOKEN = os.getenv("CODILO_TOKEN", "SEU_TOKEN")
BASE_URL = "https://api.consulta.codilo.com.br/v1"

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}


def criar_consulta_automatica(cnj):
    payload = {
        "key": "cnj",
        "value": cnj,
        "format": "allRequests"
    }

    response = requests.post(
        f"{BASE_URL}/autorequest",
        headers=HEADERS,
        json=payload
    )

    return response.json()


def consultar_por_id(request_id):
    response = requests.get(
        f"{BASE_URL}/request/{request_id}",
        headers=HEADERS
    )

    return response.json()


def listar_consultas():
    response = requests.get(
        f"{BASE_URL}/request",
        headers=HEADERS
    )

    return response.json()


if __name__ == "__main__":
    print("ConsultaBot iniciado")

    exemplo_cnj = "0804495-71.2018.8.10.0001"

    print("\nCriando consulta automática...\n")

    resultado = criar_consulta_automatica(exemplo_cnj)

    print(resultado)

    if resultado.get("success"):
        try:
            request_id = resultado["data"]["requests"][0]["id"]

            print("\nAguardando processamento...\n")
            time.sleep(5)

            detalhes = consultar_por_id(request_id)

            print("\nResultado detalhado:\n")
            print(detalhes)

        except Exception as e:
            print("Erro ao consultar detalhes:", e)
