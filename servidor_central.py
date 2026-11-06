import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SERVIDORES = {
    "A": "http://127.0.0.1:8001/comunicacao",
    "B": "http://127.0.0.1:8002/comunicacao",
}

class CentralHandler(BaseHTTPRequestHandler):
    server_version = "ServidorCentral/1.0"

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

        destino = dados.get("servidor")
        mensagem = dados.get("mensagem")
        if destino not in SERVIDORES:
            self.send_json(400, {"status": "erro", "mensagem": "Servidor deve ser A ou B"})
            return
        if not isinstance(mensagem, str) or not mensagem.strip():
            self.send_json(400, {"status": "erro", "mensagem": "Informe uma mensagem nao vazia"})
            return

        corpo = json.dumps({"mensagem": mensagem}, ensure_ascii=False).encode("utf-8")
        requisicao = Request(
            SERVIDORES[destino],
            data=corpo,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST"
        )
        try:
            with urlopen(requisicao, timeout=3) as resposta:
                retorno = json.loads(resposta.read().decode("utf-8"))
                self.send_json(200, {
                    "status": "sucesso",
                    "encaminhado_para": destino,
                    "resposta_servidor_arquivos": retorno
                })
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as erro:
            self.send_json(502, {
                "status": "erro",
                "encaminhado_para": destino,
                "mensagem": "Nao foi possivel obter resposta do servidor de arquivos",
                "detalhe": str(erro)
            })

    def send_json(self, status, payload):
        corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, formato, *args):
        print(f"[CENTRAL] {self.address_string()} - {formato % args}", flush=True)

def main():
    servidor = ThreadingHTTPServer(("127.0.0.1", 8000), CentralHandler)
    print("Servidor Central iniciado em http://127.0.0.1:8000", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("Servidor Central encerrado.", flush=True)
    finally:
        servidor.server_close()

if __name__ == "__main__":
    main()
