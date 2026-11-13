import base64
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL_CENTRAL = "http://127.0.0.1:8000/comunicacao"
TIMEOUT_SEGUNDOS = 10
LIMITE_CORPO_CENTRAL = 14 * 1024 * 1024


def enviar_operacao(operacao):
    corpo = json.dumps(operacao, ensure_ascii=False).encode("utf-8")
    if len(corpo) > LIMITE_CORPO_CENTRAL:
        print("✗ A solicitação excede o limite de tamanho do Servidor Central.")
        return None, None
    requisicao = Request(URL_CENTRAL, data=corpo,
                         headers={"Content-Type": "application/json; charset=utf-8"},
                         method="POST")
    try:
        with urlopen(requisicao, timeout=TIMEOUT_SEGUNDOS) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
            return resposta.status, dados
    except HTTPError as erro:
        try:
            dados = json.loads(erro.read().decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            dados = {"mensagem": "Resposta HTTP inválida."}
        return erro.code, dados
    except (URLError, TimeoutError, OSError) as erro:
        print(f"✗ Servidor Central indisponível: {erro}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("✗ O Servidor Central retornou uma resposta inválida.")
    return None, None


def tratar_resposta(status, dados, nome_operacao):
    if status is None:
        return None
    if status < 200 or status >= 300:
        mensagem = dados.get("mensagem") if isinstance(dados, dict) else None
        print(f"✗ {mensagem or f'Falha na operação de {nome_operacao} (HTTP {status}).'}")
        return None
    resultado = dados.get("resultado") if isinstance(dados, dict) else None
    if not isinstance(resultado, dict) or resultado.get("status") != "sucesso":
        print("✗ Resposta do Servidor Central inválida.")
        return None
    return resultado


def upload():
    entrada = input("Caminho do arquivo local: ").strip().strip('"')
    if not entrada:
        print("✗ Informe o caminho de um arquivo.")
        return
    try:
        caminho = Path(entrada).expanduser().resolve(strict=True)
        if not caminho.is_file():
            print("✗ O caminho informado não é um arquivo.")
            return
        conteudo = caminho.read_bytes()
    except (OSError, RuntimeError) as erro:
        print(f"✗ Não foi possível acessar o arquivo: {erro}")
        return
    operacao = {"operacao": "upload", "nome": caminho.name,
                "conteudo_base64": base64.b64encode(conteudo).decode("ascii")}
    status, dados = enviar_operacao(operacao)
    resultado = tratar_resposta(status, dados, "upload")
    if resultado is not None:
        print(f"✓ Upload realizado: {resultado.get('nome')} (servidor {resultado.get('servidor')}).")


def listar():
    status, dados = enviar_operacao({"operacao": "listar"})
    resultado = tratar_resposta(status, dados, "listagem")
    if resultado is None:
        return
    arquivos = resultado.get("arquivos")
    if not isinstance(arquivos, list):
        print("✗ Resposta de listagem inválida.")
        return
    print("Arquivos disponíveis:")
    if not arquivos:
        print("(nenhum arquivo)")
    else:
        for arquivo in arquivos:
            print(f"- {arquivo['nome']} [servidor {arquivo['servidor']}]")


def download():
    nome = input("Nome do arquivo: ").strip()
    if not nome:
        print("✗ Informe o nome do arquivo.")
        return
    destino_informado = input("Diretório para salvar [.] : ").strip().strip('"') or "."
    try:
        diretorio = Path(destino_informado).expanduser().resolve(strict=True)
        if not diretorio.is_dir():
            print("✗ O destino informado não é um diretório.")
            return
    except (OSError, RuntimeError) as erro:
        print(f"✗ Diretório de destino inválido: {erro}")
        return
    status, dados = enviar_operacao({"operacao": "download", "nome": nome})
    resultado = tratar_resposta(status, dados, "download")
    if resultado is None:
        return
    conteudo = resultado.get("conteudo_base64")
    if not isinstance(conteudo, str):
        print("✗ Resposta de download sem conteúdo válido.")
        return
    try:
        arquivo = base64.b64decode(conteudo, validate=True)
        caminho_destino = diretorio / Path(nome).name
        caminho_destino.write_bytes(arquivo)
    except (ValueError, OSError) as erro:
        print(f"✗ Não foi possível salvar o download: {erro}")
        return
    print(f"✓ Download concluído: {caminho_destino}")


def remover():
    nome = input("Nome do arquivo: ").strip()
    if not nome:
        print("✗ Informe o nome do arquivo.")
        return
    status, dados = enviar_operacao({"operacao": "remover", "nome": nome})
    resultado = tratar_resposta(status, dados, "remoção")
    if resultado is not None:
        print(f"✓ Arquivo removido: {resultado.get('nome')}.")


def menu():
    print("\n----------------------\nSistema de Arquivos Distribuídos\n----------------------\n")
    print("1 - Upload\n2 - Listar arquivos\n3 - Download\n4 - Remover arquivo\n0 - Sair")
    return input("\nEscolha: ").strip()


def main():
    operacoes = {"1": upload, "2": listar, "3": download, "4": remover}
    while True:
        try:
            escolha = menu()
            if escolha == "0":
                print("Até logo!")
                return
            operacao = operacoes.get(escolha)
            if operacao is None:
                print("✗ Opção inválida. Escolha uma opção do menu.")
                continue
            operacao()
        except (EOFError, KeyboardInterrupt):
            print("\nAté logo!")
            return


if __name__ == "__main__":
    main()
