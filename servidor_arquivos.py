import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class FileServerHandler(BaseHTTPRequestHandler):
    server_version = "ServidorArquivos/1.0"

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

def main():
    parser = argparse.ArgumentParser(description="Servidor de arquivos demonstrativo.")
    parser.add_argument("--nome", choices=["A", "B"], required=True)
    parser.add_argument("--porta", type=int, required=True)
    args = parser.parse_args()

    servidor = ThreadingHTTPServer(("127.0.0.1", args.porta), FileServerHandler)
    servidor.nome = args.nome
    print(f"Servidor de Arquivos {args.nome} iniciado em http://127.0.0.1:{args.porta}", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print(f"Servidor {args.nome} encerrado.", flush=True)
    finally:
        servidor.server_close()

if __name__ == "__main__":
    main()
