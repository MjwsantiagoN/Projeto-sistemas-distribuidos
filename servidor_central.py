import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

# Enderecos dos servidores de arquivos. Para executar em computadores
# diferentes, substitua 127.0.0.1 pelo IP de cada maquina.
SERVIDORES = {
    "A": "http://127.0.0.1:8001",
    "B": "http://127.0.0.1:8002",
}

MAX_CORPO = 14 * 1024 * 1024  # margem para JSON/Base64 dentro do limite de 15 MiB
TIMEOUT_SEGUNDOS = 5
ARQUIVO_METADADOS = Path(__file__).resolve().parent / "metadados.json"

# Estrutura: {nome_original: {"id": identificador, "servidor": "A" ou "B"}}
metadados = {}
trava_metadados = threading.RLock()


def carregar_metadados():
    """Recupera o indice local para que reiniciar o Central nao perca os nomes."""
    global metadados
    if not ARQUIVO_METADADOS.exists():
        metadados = {}
        return
    try:
        dados = json.loads(ARQUIVO_METADADOS.read_text(encoding="utf-8"))
        if not isinstance(dados, dict):
            raise ValueError("O arquivo de metadados precisa conter um objeto JSON.")
        validos = {}
        for nome, info in dados.items():
            if (isinstance(nome, str) and isinstance(info, dict)
                    and isinstance(info.get("id"), str)
                    and info.get("servidor") in SERVIDORES):
                validos[nome] = {"id": info["id"], "servidor": info["servidor"]}
        metadados = validos
    except (OSError, json.JSONDecodeError, ValueError) as erro:
        raise RuntimeError(f"Nao foi possivel carregar {ARQUIVO_METADADOS}: {erro}") from erro


def salvar_metadados():
    temporario = ARQUIVO_METADADOS.with_suffix(".json.tmp")
    temporario.write_text(json.dumps(metadados, ensure_ascii=False, indent=2), encoding="utf-8")
    temporario.replace(ARQUIVO_METADADOS)


def requisitar_servidor(servidor, metodo, caminho, corpo=None):
    """Executa uma chamada HTTP ao servidor de arquivos e devolve status e JSON."""
    url = SERVIDORES[servidor] + caminho
    dados = None if corpo is None else json.dumps(corpo, ensure_ascii=False).encode("utf-8")
    requisicao = Request(
        url,
        data=dados,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method=metodo,
    )
    try:
        with urlopen(requisicao, timeout=TIMEOUT_SEGUNDOS) as resposta:
            texto = resposta.read().decode("utf-8")
            return resposta.status, json.loads(texto) if texto else {}
    except HTTPError as erro:
        try:
            texto = erro.read().decode("utf-8", errors="replace")
            retorno = json.loads(texto) if texto else {}
        except json.JSONDecodeError:
            retorno = {"mensagem": "Resposta invalida do servidor de arquivos."}
        return erro.code, retorno
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise ConnectionError(str(erro)) from erro


def escolher_servidor():
    """Distribui novos arquivos para o servidor com menos arquivos no indice."""
    quantidade = {nome: 0 for nome in SERVIDORES}
    for info in metadados.values():
        if info["servidor"] in quantidade:
            quantidade[info["servidor"]] += 1
    return min(SERVIDORES, key=lambda nome: quantidade[nome])


class CentralHandler(BaseHTTPRequestHandler):
    server_version = "ServidorCentral/2.0"

    def do_GET(self):
        if self.path == "/status":
            self.send_json(200, {"status": "ok", "componente": "servidor_central"})
        else:
            self.send_json(404, {"status": "erro", "mensagem": "Endpoint nao encontrado"})

    def do_POST(self):
        if self.path != "/comunicacao":
            self.send_json(404, {"status": "erro", "mensagem": "Endpoint nao encontrado"})
            return

        dados = self.ler_json()
        if dados is None:
            return
        operacao = dados.get("operacao")
        if operacao == "upload":
            self.upload(dados)
        elif operacao == "listar":
            self.listar()
        elif operacao == "download":
            self.download(dados)
        elif operacao == "remover":
            self.remover(dados)
        else:
            self.send_json(400, {"status": "erro", "mensagem": "Operacao invalida. Use upload, listar, download ou remover."})

    def ler_json(self):
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = 0
        if tamanho <= 0 or tamanho > MAX_CORPO:
            self.send_json(400, {"status": "erro", "mensagem": "Corpo da requisicao invalido ou maior que o limite."})
            return None
        try:
            dados = json.loads(self.rfile.read(tamanho).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"status": "erro", "mensagem": "JSON invalido."})
            return None
        if not isinstance(dados, dict):
            self.send_json(400, {"status": "erro", "mensagem": "O corpo deve ser um objeto JSON."})
            return None
        return dados

    def upload(self, dados):
        nome = dados.get("nome")
        conteudo = dados.get("conteudo_base64")
        if (not isinstance(nome, str) or not nome.strip() or nome in {".", ".."}
                or Path(nome).name != nome or "/" in nome or "\\" in nome):
            self.send_json(400, {"status": "erro", "mensagem": "Nome de arquivo invalido."})
            return
        if not isinstance(conteudo, str):
            self.send_json(400, {"status": "erro", "mensagem": "Campo 'conteudo_base64' obrigatorio."})
            return

        with trava_metadados:
            if nome in metadados:
                self.send_json(409, {"status": "erro", "mensagem": "Ja existe um arquivo com esse nome."})
                return
            destino = escolher_servidor()
            id_arquivo = uuid.uuid4().hex
            try:
                status, retorno = requisitar_servidor(
                    destino, "PUT", f"/arquivos/{id_arquivo}", {"conteudo_base64": conteudo}
                )
            except ConnectionError as erro:
                self.send_json(502, {"status": "erro", "servidor": destino,
                                     "mensagem": "Servidor de arquivos indisponivel.", "detalhe": str(erro)})
                return
            if status not in (200, 201) or retorno.get("status") != "sucesso":
                self.send_json(status if 400 <= status < 600 else 502,
                               {"status": "erro", "mensagem": retorno.get("mensagem", "Falha ao armazenar arquivo."),
                                "servidor": destino})
                return
            metadados[nome] = {"id": id_arquivo, "servidor": destino}
            try:
                salvar_metadados()
            except OSError as erro:
                # Evita deixar no armazenamento um arquivo sem entrada no indice.
                try:
                    requisitar_servidor(destino, "DELETE", f"/arquivos/{id_arquivo}")
                except ConnectionError:
                    pass
                metadados.pop(nome, None)
                self.send_json(500, {"status": "erro", "mensagem": f"Nao foi possivel salvar os metadados: {erro}"})
                return

        self.send_json(201, {"resultado": {"status": "sucesso", "mensagem": "Upload realizado.",
                                             "nome": nome, "id": id_arquivo, "servidor": destino}})
        self.log_operacao(f"upload de {nome} armazenado no servidor {destino}")

    def listar(self):
        with trava_metadados:
            arquivos = [
                {"nome": nome, "id": info["id"], "servidor": info["servidor"]}
                for nome, info in sorted(metadados.items())
            ]
        self.send_json(200, {"resultado": {"status": "sucesso", "arquivos": arquivos}})

    def download(self, dados):
        nome = dados.get("nome")
        if not isinstance(nome, str) or not nome.strip():
            self.send_json(400, {"status": "erro", "mensagem": "Informe o nome do arquivo."})
            return
        with trava_metadados:
            info = metadados.get(nome)
        if info is None:
            self.send_json(404, {"status": "erro", "mensagem": "Arquivo nao encontrado."})
            return
        try:
            status, retorno = requisitar_servidor(info["servidor"], "GET", f"/arquivos/{quote(info['id'], safe='')}")
        except ConnectionError as erro:
            self.send_json(502, {"status": "erro", "mensagem": "Servidor de arquivos indisponivel.", "detalhe": str(erro)})
            return
        if status != 200:
            self.send_json(status, {"status": "erro", "mensagem": retorno.get("mensagem", "Falha no download.")})
            return
        resultado = {"status": "sucesso", "nome": nome,
                     "conteudo_base64": retorno.get("conteudo_base64")}
        self.send_json(200, {"resultado": resultado})
        self.log_operacao(f"download de {nome} obtido do servidor {info['servidor']}")

    def remover(self, dados):
        nome = dados.get("nome")
        if not isinstance(nome, str) or not nome.strip():
            self.send_json(400, {"status": "erro", "mensagem": "Informe o nome do arquivo."})
            return
        with trava_metadados:
            info = metadados.get(nome)
            if info is None:
                self.send_json(404, {"status": "erro", "mensagem": "Arquivo nao encontrado."})
                return
            try:
                status, retorno = requisitar_servidor(
                    info["servidor"], "DELETE", f"/arquivos/{quote(info['id'], safe='')}"
                )
            except ConnectionError as erro:
                self.send_json(502, {"status": "erro", "mensagem": "Servidor de arquivos indisponivel.", "detalhe": str(erro)})
                return
            if status != 200:
                self.send_json(status, {"status": "erro", "mensagem": retorno.get("mensagem", "Falha na remocao.")})
                return
            metadados.pop(nome)
            try:
                salvar_metadados()
            except OSError as erro:
                self.send_json(500, {"status": "erro", "mensagem": f"Arquivo removido, mas nao foi possivel atualizar os metadados: {erro}"})
                return
        self.send_json(200, {"resultado": {"status": "sucesso", "mensagem": "Arquivo removido.", "nome": nome}})
        self.log_operacao(f"arquivo {nome} removido do servidor {info['servidor']}")

    def send_json(self, status, payload):
        corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, formato, *args):
        print(f"[CENTRAL] {self.address_string()} - {formato % args}", flush=True)

    def log_operacao(self, mensagem):
        print(f"[CENTRAL] {mensagem}", flush=True)


def main():
    carregar_metadados()
    servidor = ThreadingHTTPServer(("127.0.0.1", 8000), CentralHandler)
    print("Servidor Central iniciado em http://127.0.0.1:8000", flush=True)
    print(f"Metadados: {ARQUIVO_METADADOS}", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("Servidor Central encerrado.", flush=True)
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
