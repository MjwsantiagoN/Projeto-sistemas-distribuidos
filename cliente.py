import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

URL_CENTRAL = "http://127.0.0.1:8000/comunicacao"

def enviar_requisicao(servidor, mensagem):
    corpo = json.dumps(
        {"servidor": servidor, "mensagem": mensagem},
        ensure_ascii=False
    ).encode("utf-8")
    requisicao = Request(
        URL_CENTRAL,
        data=corpo,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST"
    )
    try:
        with urlopen(requisicao, timeout=5) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
            print(f"Resposta HTTP: {resposta.status}")
            print(json.dumps(dados, ensure_ascii=False, indent=2))
    except HTTPError as erro:
        corpo_erro = erro.read().decode("utf-8", errors="replace")
        print(f"Erro HTTP {erro.code}: {corpo_erro}")
    except URLError as erro:
        print(f"Falha de comunicacao com o Servidor Central: {erro.reason}")

def main():
    print("=== Cliente do Sistema de Arquivos Distribuidos ===")
    while True:
        servidor = input("Servidor de destino (A/B ou sair): ").strip().upper()
        if servidor == "SAIR":
            break
        if servidor not in ("A", "B"):
            print("Escolha A, B ou sair.")
            continue
        mensagem = input("Mensagem para enviar: ").strip()
        if not mensagem:
            print("A mensagem nao pode ficar vazia.")
            continue
        enviar_requisicao(servidor, mensagem)

if __name__ == "__main__":
    main()
