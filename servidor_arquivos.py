import argparse
import base64
import binascii
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MAX_CORPO = 15 * 1024 * 1024
ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

class FileServerHandler(BaseHTTPRequestHandler):
    server_version = "ServidorArquivos/1.0"

    def partes_caminho(self):
        """'/arquivos/abc123' -> ['arquivos', 'abc123']"""
        return [p for p in urlparse(self.path).path.split("/") if p]

    def do_GET(self):
        partes = self.partes_caminho()
        if partes == ["status"]:
            self.status_servidor()
        elif partes == ["arquivos"]:
            self.listar_arquivos()
        elif len(partes) == 2 and partes[0] == "arquivos":
            self.ler_arquivo(partes[1])
        else:
            self.erro(404, "Endpoint nao encontrado")

    def do_PUT(self):
        partes = self.partes_caminho()
        if len(partes) == 2 and partes[0] == "arquivos":
            self.gravar_arquivo(partes[1])
        else:
            self.erro(404, "Endpoint nao encontrado")

    def do_DELETE(self):
        partes = self.partes_caminho()
        if len(partes) == 2 and partes[0] == "arquivos":
            self.remover_arquivo(partes[1])
        else:
            self.erro(404, "Endpoint nao encontrado")

    def do_POST(self):
        if self.path != "/comunicacao":
            self.send_json(404, {"status": "erro", "mensagem": "Endpoint nao encontrado"})
            return

        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
            if tamanho <= 0 or tamanho > 1_000_000:
                self.send_json(400, {"status": "erro", "mensagem": "Corpo da requisicao invalido"})
                return
            dados = json.loads(self.rfile.read(tamanho).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"status": "erro", "mensagem": "JSON invalido"})
            return

        mensagem = dados.get("mensagem")
        if not isinstance(mensagem, str) or not mensagem.strip():
            self.send_json(400, {"status": "erro", "mensagem": "Informe uma mensagem nao vazia"})
            return

        self.send_json(200, {
            "status": "sucesso",
            "servidor": self.server.nome,
            "mensagem_recebida": mensagem
        })

    def send_json(self, status, payload):
        corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, formato, *args):
        print(f"[{self.server.nome}] {self.address_string()} - {formato % args}", flush=True)

    def status_servidor(self):
        self.send_json(200, {
            "status": "ok",
            "servidor": self.server.nome,
            "arquivos": len(self.ids_armazenados())
        })

    def listar_arquivos(self):
        arquivos = []
        with self.server.trava:
            for id_arquivo in self.ids_armazenados():
                caminho = self.server.pasta / id_arquivo
                arquivos.append({"id": id_arquivo, "tamanho": caminho.stat().st_size})
        self.send_json(200, {
            "status": "sucesso",
            "servidor": self.server.nome,
            "arquivos": arquivos
        })

    def ler_arquivo(self, id_arquivo):
        if not self.id_ok(id_arquivo):
            return
        caminho = self.server.pasta / id_arquivo
        with self.server.trava:
            if not caminho.is_file():
                self.erro(404, f"Arquivo {id_arquivo} nao encontrado")
                return
            conteudo = caminho.read_bytes()
        self.send_json(200, {
            "status": "sucesso",
            "servidor": self.server.nome,
            "id": id_arquivo,
            "tamanho": len(conteudo),
            "conteudo_base64": base64.b64encode(conteudo).decode("ascii")
        })
        self.log_operacao(f"download de {id_arquivo} ({len(conteudo)} bytes)")

    def gravar_arquivo(self, id_arquivo):
        if not self.id_ok(id_arquivo):
            return
        dados = self.ler_json()
        if dados is None:
            return
        texto_base64 = dados.get("conteudo_base64")
        if not isinstance(texto_base64, str):
            self.erro(400, "Campo 'conteudo_base64' obrigatorio")
            return
        try:
            conteudo = base64.b64decode(texto_base64, validate=True)
        except (binascii.Error, ValueError):
            self.erro(400, "Conteudo base64 invalido")
            return

        caminho = self.server.pasta / id_arquivo
        temporario = self.server.pasta / f"{id_arquivo}.tmp"
        with self.server.trava:
            ja_existia = caminho.exists()
            # Grava num arquivo temporario e depois renomeia (operacao atomica):
            # se o processo cair no meio da escrita, nao fica arquivo pela metade.
            temporario.write_bytes(conteudo)
            os.replace(temporario, caminho)

        # PUT e idempotente: repetir a mesma gravacao nao duplica o arquivo
        self.send_json(200 if ja_existia else 201, {
            "status": "sucesso",
            "servidor": self.server.nome,
            "id": id_arquivo,
            "tamanho": len(conteudo)
        })
        acao = "sobrescrito" if ja_existia else "gravado"
        self.log_operacao(f"arquivo {id_arquivo} {acao} ({len(conteudo)} bytes)")

    def remover_arquivo(self, id_arquivo):
        if not self.id_ok(id_arquivo):
            return
        caminho = self.server.pasta / id_arquivo
        with self.server.trava:
            if not caminho.is_file():
                self.erro(404, f"Arquivo {id_arquivo} nao encontrado")
                return
            caminho.unlink()
        self.send_json(200, {
            "status": "removido",
            "servidor": self.server.nome,
            "id": id_arquivo
        })
        self.log_operacao(f"arquivo {id_arquivo} removido")

    def ids_armazenados(self):
        return sorted(
            p.name for p in self.server.pasta.iterdir()
            if p.is_file() and not p.name.endswith(".tmp")
        )

    def id_ok(self, id_arquivo):
        if ID_VALIDO.match(id_arquivo):
            return True
        self.erro(400, "ID de arquivo invalido")
        return False

    def ler_json(self):
        """Le o corpo como JSON. Em caso de erro, ja responde 400 e devolve None."""
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = 0
        if tamanho <= 0 or tamanho > MAX_CORPO:
            self.erro(400, "Corpo da requisicao invalido ou maior que o limite")
            return None
        try:
            dados = json.loads(self.rfile.read(tamanho).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.erro(400, "JSON invalido")
            return None
        if not isinstance(dados, dict):
            self.erro(400, "O corpo deve ser um objeto JSON")
            return None
        return dados

    def erro(self, status, mensagem):
        self.send_json(status, {
            "status": "erro",
            "servidor": self.server.nome,
            "mensagem": mensagem
        })

    def log_operacao(self, texto):
        print(f"[{self.server.nome}] {texto}", flush=True)

def main():
    parser = argparse.ArgumentParser(description="Servidor de arquivos demonstrativo.")
    parser.add_argument("--nome", choices=["A", "B"], required=True)
    parser.add_argument("--porta", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1",
                        help="Use 0.0.0.0 para aceitar conexoes de outros computadores")
    args = parser.parse_args()

    pasta = Path(__file__).resolve().parent / f"armazenamento_{args.nome}"
    pasta.mkdir(exist_ok=True)

    servidor = ThreadingHTTPServer((args.host, args.porta), FileServerHandler)
    servidor.nome = args.nome
    servidor.pasta = pasta
    servidor.trava = threading.Lock()
    print(f"Servidor de Arquivos {args.nome} iniciado em http://{args.host}:{args.porta}", flush=True)
    print(f"Pasta de armazenamento: {pasta}", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print(f"Servidor {args.nome} encerrado.", flush=True)
    finally:
        servidor.server_close()

if __name__ == "__main__":
    main()
