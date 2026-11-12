import base64
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL_CENTRAL = "http://127.0.0.1:8000/comunicacao"
TIMEOUT_SEGUNDOS = 5
LIMITE_CORPO_CENTRAL = 1_000_000
SERVIDORES = ("A", "B")


def escolher_servidor():
    while True:
        servidor = input("Servidor de destino (A/B): ").strip().upper()
        if servidor in SERVIDORES:
            return servidor
        print("✗ Destino inválido. Escolha A ou B.")


def enviar_requisicao(servidor, mensagem):
    """Envia uma mensagem pelo POST /comunicacao existente."""
    corpo = json.dumps({"servidor": servidor, "mensagem": mensagem}, ensure_ascii=False).encode("utf-8")
    if len(corpo) > LIMITE_CORPO_CENTRAL:
        print("✗ A solicitação excede o limite atual de 1 MB do Servidor Central.")
        return None, None
    requisicao = Request(URL_CENTRAL, data=corpo, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with urlopen(requisicao, timeout=TIMEOUT_SEGUNDOS) as resposta:
            return resposta.status, json.loads(resposta.read().decode("utf-8"))
    except HTTPError as erro:
        try:
            dados = json.loads(erro.read().decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            dados = {"mensagem": "Resposta HTTP inválida."}
        return erro.code, dados
    except (URLError, TimeoutError, OSError) as erro:
        print(f"✗ Servidor indisponível: {erro}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("✗ O Servidor Central retornou uma resposta inválida.")
    return None, None


def enviar_operacao(servidor, operacao):
    """Empacota a operação no campo mensagem do contrato atual.

    TODO: o Central precisa interpretar este JSON. Hoje ele apenas encaminha a
    mensagem como texto e o Servidor de Arquivos a ecoa.
    """
    return enviar_requisicao(servidor, json.dumps(operacao, ensure_ascii=False))


def resultado_da_operacao(dados):
    """Interpreta resposta futura sem confundir o eco atual com sucesso."""
    if not isinstance(dados, dict):
        return None
    # TODO: alinhar este formato com o Central quando o protocolo de arquivos existir.
    resultado = dados.get("resultado")
    if isinstance(resultado, dict):
        return resultado
    resposta = dados.get("resposta_servidor_arquivos")
    if isinstance(resposta, dict):
        recebido = resposta.get("mensagem_recebida")
        if isinstance(recebido, str):
            try:
                recebido = json.loads(recebido)
            except json.JSONDecodeError:
                return None
        if isinstance(recebido, dict) and isinstance(recebido.get("resultado"), dict):
            return recebido["resultado"]
    return None


def tratar_resposta(status, dados, nome_operacao):
    if status is None:
        return None
    if status == 404:
        print("✗ Arquivo ou recurso não encontrado.")
        return None
    if status >= 500:
        print("✗ Servidor indisponível.")
        return None
    if status < 200 or status >= 300:
        mensagem = dados.get("mensagem") if isinstance(dados, dict) else None
        print(f"✗ {mensagem or f'Falha na operação de {nome_operacao}.'}")
        return None
    resultado = resultado_da_operacao(dados)
    if resultado is None:
        print(f"ℹ Solicitação de {nome_operacao} encaminhada; a execução aguarda integração futura no Servidor Central.")
        return None
    if resultado.get("status") != "sucesso":
        print(f"✗ {resultado.get('mensagem', 'A operação não foi concluída.')}")
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
    # TODO: o Central ainda não interpreta nem armazena uploads.
    operacao = {"operacao": "upload", "nome": caminho.name, "conteudo_base64": base64.b64encode(conteudo).decode("ascii")}
    status, dados = enviar_operacao(escolher_servidor(), operacao)
    if tratar_resposta(status, dados, "upload") is not None:
        print("✓ Upload realizado com sucesso.")


def listar():
    # TODO: aguarda o Central interpretar a solicitação e devolver arquivos.
    status, dados = enviar_operacao(escolher_servidor(), {"operacao": "listar"})
    resultado = tratar_resposta(status, dados, "listagem")
    if resultado is None:
        return
    arquivos = resultado.get("arquivos")
    if not isinstance(arquivos, list):
        print("✗ Resposta de listagem inválida.")
        return
    print("Arquivos disponíveis:")
    print("\n".join(f"- {arquivo}" for arquivo in arquivos) if arquivos else "(nenhum arquivo)")


def download():
    nome = input("Nome do arquivo: ").strip()
    if not nome:
        print("✗ Informe o nome do arquivo.")
        return
    destino_informado = input("Diretório para salvar: ").strip().strip('"') or "."
    try:
        diretorio = Path(destino_informado).expanduser().resolve(strict=True)
        if not diretorio.is_dir():
            print("✗ O destino informado não é um diretório.")
            return
    except (OSError, RuntimeError) as erro:
        print(f"✗ Diretório de destino inválido: {erro}")
        return
    # TODO: o Central precisa devolver conteudo_base64 no resultado do download.
    status, dados = enviar_operacao(escolher_servidor(), {"operacao": "download", "nome": nome})
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
    # TODO: aguarda o Central interpretar e confirmar a remoção.
    status, dados = enviar_operacao(escolher_servidor(), {"operacao": "remover", "nome": nome})
    if tratar_resposta(status, dados, "remoção") is not None:
        print("✓ Arquivo removido.")


def menu():
    print("\n----------------------\nSistema de Arquivos\n----------------------\n")
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
