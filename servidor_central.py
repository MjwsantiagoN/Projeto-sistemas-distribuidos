import json
import threading
import time
import unicodedata
import uuid
from collections import deque
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

# Redundancia: cada arquivo deve existir em FATOR_REPLICACAO servidores.
# Com 2 servidores, cada arquivo fica em A e em B.
FATOR_REPLICACAO = 2
INTERVALO_REPARO = 3  # segundos entre rodadas do reparo automatico

MAX_CORPO = 14 * 1024 * 1024  # margem para JSON/Base64 dentro do limite de 15 MiB
TIMEOUT_SEGUNDOS = 5
BASE = Path(__file__).resolve().parent
ARQUIVO_METADADOS = BASE / "metadados.json"
ARQUIVO_PENDENTES = BASE / "remocoes_pendentes.json"
ARQUIVO_FRONTEND = BASE / "frontend" / "index.html"

# Estrutura: {nome_original: {"id": identificador, "servidores": ["A", "B"]}}
metadados = {}
# Remocoes que ainda nao foram feitas porque a replica estava fora do ar:
# [{"id": identificador, "servidor": "B"}]
remocoes_pendentes = []
trava_metadados = threading.RLock()
eventos = deque(maxlen=60)  # historico recente, exibido no frontend


def normalizar(texto):
    """Minusculas e sem acentos, para a busca por nome ('relatorio' acha 'Relatório')."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.casefold()


def alvo_replicas():
    return min(FATOR_REPLICACAO, len(SERVIDORES))


def registrar(mensagem):
    print(f"[CENTRAL] {mensagem}", flush=True)
    eventos.append({"hora": time.strftime("%H:%M:%S"), "mensagem": mensagem})


def carregar_metadados():
    """Recupera o indice local. Aceita o formato antigo (um unico 'servidor')."""
    global metadados, remocoes_pendentes
    metadados = {}
    remocoes_pendentes = []
    if ARQUIVO_METADADOS.exists():
        try:
            dados = json.loads(ARQUIVO_METADADOS.read_text(encoding="utf-8"))
            if not isinstance(dados, dict):
                raise ValueError("O arquivo de metadados precisa conter um objeto JSON.")
        except (OSError, json.JSONDecodeError, ValueError) as erro:
            raise RuntimeError(f"Nao foi possivel carregar {ARQUIVO_METADADOS}: {erro}") from erro
        for nome, info in dados.items():
            if not (isinstance(nome, str) and isinstance(info, dict) and isinstance(info.get("id"), str)):
                continue
            lista = info.get("servidores")
            if lista is None and info.get("servidor") in SERVIDORES:  # formato antigo
                lista = [info["servidor"]]
            if isinstance(lista, list):
                lista = [s for s in lista if s in SERVIDORES]
                metadados[nome] = {"id": info["id"], "servidores": lista}
    if ARQUIVO_PENDENTES.exists():
        try:
            dados = json.loads(ARQUIVO_PENDENTES.read_text(encoding="utf-8"))
            remocoes_pendentes = [p for p in dados if isinstance(p, dict)
                                  and isinstance(p.get("id"), str) and p.get("servidor") in SERVIDORES]
        except (OSError, json.JSONDecodeError):
            remocoes_pendentes = []


def _gravar_atomico(caminho, objeto):
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    temporario.write_text(json.dumps(objeto, ensure_ascii=False, indent=2), encoding="utf-8")
    temporario.replace(caminho)


def salvar_metadados():
    _gravar_atomico(ARQUIVO_METADADOS, metadados)
    _gravar_atomico(ARQUIVO_PENDENTES, remocoes_pendentes)


def requisitar_servidor(servidor, metodo, caminho, corpo=None, timeout=TIMEOUT_SEGUNDOS):
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
        with urlopen(requisicao, timeout=timeout) as resposta:
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


def ordem_servidores():
    """Prioriza o que tem menos replicas (balanceamento simples)."""
    quantidade = {nome: 0 for nome in SERVIDORES}
    for info in metadados.values():
        for s in info["servidores"]:
            if s in quantidade:
                quantidade[s] += 1
    return sorted(SERVIDORES, key=lambda nome: (quantidade[nome], nome))


# ---------------------------------------------------------------------------
# Reparo automatico (anti-entropia): roda em segundo plano no Central.
#   1) conclui remocoes pendentes em replicas que voltaram;
#   2) descobre replicas que sumiram do disco de um servidor;
#   3) copia o arquivo para um servidor que esta sem a replica.
# ---------------------------------------------------------------------------
def reparar_uma_vez():
    online = []
    for nome in SERVIDORES:
        try:
            requisitar_servidor(nome, "GET", "/status", timeout=1.5)
            online.append(nome)
        except ConnectionError:
            pass
    if not online:
        return
    with trava_metadados:
        ids_por_servidor = {}
        for nome in online:
            try:
                status, retorno = requisitar_servidor(nome, "GET", "/arquivos")
            except ConnectionError:
                continue
            if status == 200:
                ids_por_servidor[nome] = {a["id"] for a in retorno.get("arquivos", [])}
        mudou = False

        # 1) remocoes pendentes
        for pendente in list(remocoes_pendentes):
            if pendente["servidor"] not in ids_por_servidor:
                continue
            try:
                status, _ = requisitar_servidor(pendente["servidor"], "DELETE",
                                                f"/arquivos/{quote(pendente['id'], safe='')}")
            except ConnectionError:
                continue
            if status in (200, 404):
                remocoes_pendentes.remove(pendente)
                mudou = True
                registrar(f"REPARO: remocao pendente concluida no servidor {pendente['servidor']}")

        # 2) replicas registradas no indice mas ausentes no disco do servidor
        for nome, info in metadados.items():
            for srv in list(info["servidores"]):
                if srv in ids_por_servidor and info["id"] not in ids_por_servidor[srv]:
                    info["servidores"].remove(srv)
                    mudou = True
                    registrar(f"REPARO: replica de {nome} ausente no servidor {srv}; sera recriada")

        # 3) re-replicacao
        alvo = alvo_replicas()
        for nome, info in metadados.items():
            tem = info["servidores"]
            if not tem or len(tem) >= alvo:
                continue
            origem = next((s for s in tem if s in ids_por_servidor), None)
            if origem is None:
                continue
            faltam = [s for s in ordem_servidores() if s in ids_por_servidor and s not in tem]
            if not faltam:
                continue
            try:
                status, retorno = requisitar_servidor(origem, "GET", f"/arquivos/{quote(info['id'], safe='')}")
                if status != 200:
                    continue
                for destino in faltam[:alvo - len(tem)]:
                    status, ret2 = requisitar_servidor(destino, "PUT", f"/arquivos/{info['id']}",
                                                       {"conteudo_base64": retorno["conteudo_base64"]})
                    if status in (200, 201):
                        info["servidores"].append(destino)
                        mudou = True
                        registrar(f"REPARO: {nome} copiado de {origem} para {destino} (redundancia restaurada)")
            except ConnectionError:
                continue
        if mudou:
            try:
                salvar_metadados()
            except OSError as erro:
                registrar(f"REPARO: falha ao salvar metadados: {erro}")


def laco_reparo():
    while True:
        time.sleep(INTERVALO_REPARO)
        try:
            reparar_uma_vez()
        except Exception as erro:  # o reparo nunca pode derrubar o Central
            registrar(f"REPARO: erro inesperado: {erro}")


class CentralHandler(BaseHTTPRequestHandler):
    server_version = "ServidorCentral/3.0"
    silencioso = False

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.enviar_frontend()
        elif self.path == "/status":
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
        self.silencioso = operacao in ("estado", "listar", "buscar")
        if operacao == "upload":
            self.upload(dados)
        elif operacao == "listar":
            self.listar()
        elif operacao == "buscar":
            self.buscar(dados)
        elif operacao == "download":
            self.download(dados)
        elif operacao == "remover":
            self.remover(dados)
        elif operacao == "estado":
            self.estado()
        elif operacao == "reparar":
            self.reparar()
        else:
            self.send_json(400, {"status": "erro", "mensagem": "Operacao invalida. Use upload, listar, buscar, download, remover, estado ou reparar."})

    def enviar_frontend(self):
        try:
            corpo = ARQUIVO_FRONTEND.read_bytes()
        except OSError:
            self.send_json(404, {"status": "erro", "mensagem": "Frontend nao encontrado (pasta frontend/)."})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

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
            id_arquivo = uuid.uuid4().hex
            alvo = alvo_replicas()
            gravados = []
            falhas = []
            erro_http = None
            # Escreve em ate 'alvo' servidores. Se um estiver fora do ar, o upload
            # continua com as replicas disponiveis (degradado) e o reparo
            # automatico cria a replica faltante quando o servidor voltar.
            for candidato in ordem_servidores():
                if len(gravados) >= alvo:
                    break
                try:
                    status, retorno = requisitar_servidor(
                        candidato, "PUT", f"/arquivos/{id_arquivo}", {"conteudo_base64": conteudo}
                    )
                except ConnectionError as erro:
                    falhas.append(f"{candidato}: {erro}")
                    registrar(f"servidor {candidato} indisponivel, {nome} nao foi gravado nele")
                    continue
                if status in (200, 201) and retorno.get("status") == "sucesso":
                    gravados.append(candidato)
                else:
                    erro_http = (status, retorno, candidato)
            if not gravados:
                if erro_http:
                    status, retorno, candidato = erro_http
                    self.send_json(status if 400 <= status < 600 else 502,
                                   {"status": "erro", "mensagem": retorno.get("mensagem", "Falha ao armazenar arquivo."),
                                    "servidor": candidato})
                else:
                    self.send_json(502, {"status": "erro",
                                         "mensagem": "Nenhum servidor de arquivos disponivel.",
                                         "detalhe": "; ".join(falhas)})
                return
            metadados[nome] = {"id": id_arquivo, "servidores": gravados}
            try:
                salvar_metadados()
            except OSError as erro:
                # Evita deixar no armazenamento arquivos sem entrada no indice.
                for srv in gravados:
                    try:
                        requisitar_servidor(srv, "DELETE", f"/arquivos/{id_arquivo}")
                    except ConnectionError:
                        pass
                metadados.pop(nome, None)
                self.send_json(500, {"status": "erro", "mensagem": f"Nao foi possivel salvar os metadados: {erro}"})
                return

        degradado = len(gravados) < alvo
        self.send_json(201, {"resultado": {
            "status": "sucesso",
            "mensagem": "Upload realizado" + (" com redundancia reduzida." if degradado else "."),
            "nome": nome, "id": id_arquivo,
            "servidor": ",".join(gravados), "servidores": gravados,
            "replicas": len(gravados), "degradado": degradado}})
        registrar(f"upload de {nome} gravado em {len(gravados)}/{alvo} replicas ({', '.join(gravados)})"
                  + (" [DEGRADADO]" if degradado else ""))

    def listar(self, termo=None):
        alvo = alvo_replicas()
        filtro = normalizar(termo) if termo else None
        with trava_metadados:
            arquivos = [
                {"nome": nome, "id": info["id"], "servidor": ",".join(info["servidores"]),
                 "servidores": list(info["servidores"]), "replicas": len(info["servidores"]),
                 "degradado": len(info["servidores"]) < alvo}
                for nome, info in sorted(metadados.items())
                if filtro is None or filtro in normalizar(nome)
            ]
        resultado = {"status": "sucesso", "arquivos": arquivos, "alvo_replicas": alvo}
        if termo:
            resultado["termo"] = termo
        self.send_json(200, {"resultado": resultado})

    def buscar(self, dados):
        """Busca por nome: trecho do nome, sem diferenciar maiusculas/acentos."""
        termo = dados.get("termo")
        if not isinstance(termo, str) or not termo.strip():
            self.send_json(400, {"status": "erro", "mensagem": "Informe o termo de busca."})
            return
        self.listar(termo.strip())

    def download(self, dados):
        nome = dados.get("nome")
        if not isinstance(nome, str) or not nome.strip():
            self.send_json(400, {"status": "erro", "mensagem": "Informe o nome do arquivo."})
            return
        with trava_metadados:
            info = metadados.get(nome)
            replicas = list(info["servidores"]) if info else None
            id_arquivo = info["id"] if info else None
        if info is None:
            self.send_json(404, {"status": "erro", "mensagem": "Arquivo nao encontrado."})
            return
        falhas = []
        # Failover de leitura: tenta cada replica ate uma responder.
        for srv in replicas:
            try:
                status, retorno = requisitar_servidor(srv, "GET", f"/arquivos/{quote(id_arquivo, safe='')}")
            except ConnectionError as erro:
                falhas.append(f"{srv}: {erro}")
                registrar(f"servidor {srv} indisponivel, tentando outra replica de {nome}")
                continue
            if status == 200:
                resultado = {"status": "sucesso", "nome": nome, "servidor": srv,
                             "conteudo_base64": retorno.get("conteudo_base64")}
                self.send_json(200, {"resultado": resultado})
                registrar(f"download de {nome} obtido do servidor {srv}")
                return
            falhas.append(f"{srv}: HTTP {status}")
            registrar(f"replica de {nome} no servidor {srv} respondeu HTTP {status}, tentando outra")
        self.send_json(502, {"status": "erro", "mensagem": "Nenhuma replica do arquivo esta disponivel.",
                             "detalhe": "; ".join(falhas)})

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
            removidos, pendentes = [], []
            for srv in info["servidores"]:
                try:
                    status, _ = requisitar_servidor(srv, "DELETE", f"/arquivos/{quote(info['id'], safe='')}")
                except ConnectionError:
                    pendentes.append(srv)
                    continue
                (removidos if status in (200, 404) else pendentes).append(srv)
            if info["servidores"] and not removidos:
                self.send_json(502, {"status": "erro", "mensagem": "Servidor de arquivos indisponivel.",
                                     "detalhe": "nenhuma replica pode ser removida agora"})
                return
            metadados.pop(nome)
            for srv in pendentes:
                remocoes_pendentes.append({"id": info["id"], "servidor": srv})
            try:
                salvar_metadados()
            except OSError as erro:
                self.send_json(500, {"status": "erro", "mensagem": f"Arquivo removido, mas nao foi possivel atualizar os metadados: {erro}"})
                return
        self.send_json(200, {"resultado": {
            "status": "sucesso",
            "mensagem": "Arquivo removido." + (f" Remocao pendente em {', '.join(pendentes)}." if pendentes else ""),
            "nome": nome, "pendentes": pendentes}})
        registrar(f"arquivo {nome} removido de {', '.join(removidos) or '-'}"
                  + (f"; remocao PENDENTE em {', '.join(pendentes)}" if pendentes else ""))

    def estado(self):
        """Painel do frontend: disponibilidade dos servidores e resumo da redundancia."""
        servidores = []
        for nome in SERVIDORES:
            try:
                status, retorno = requisitar_servidor(nome, "GET", "/status", timeout=1.5)
                servidores.append({"nome": nome, "endereco": SERVIDORES[nome], "online": status == 200,
                                   "arquivos": retorno.get("arquivos", 0)})
            except ConnectionError:
                servidores.append({"nome": nome, "endereco": SERVIDORES[nome], "online": False, "arquivos": 0})
        alvo = alvo_replicas()
        with trava_metadados:
            total = len(metadados)
            subreplicados = sum(1 for i in metadados.values() if len(i["servidores"]) < alvo)
            pendentes = len(remocoes_pendentes)
        self.send_json(200, {"resultado": {
            "status": "sucesso", "servidores": servidores, "alvo_replicas": alvo,
            "total_arquivos": total, "subreplicados": subreplicados,
            "remocoes_pendentes": pendentes, "eventos": list(eventos)}})

    def reparar(self):
        reparar_uma_vez()
        self.estado()

    def send_json(self, status, payload):
        corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, formato, *args):
        # O polling periodico do frontend (estado/listar) nao poluem o log
        if self.silencioso:
            return
        print(f"[CENTRAL] {self.address_string()} - {formato % args}", flush=True)


def main():
    carregar_metadados()
    servidor = ThreadingHTTPServer(("127.0.0.1", 8000), CentralHandler)
    threading.Thread(target=laco_reparo, daemon=True).start()
    print("Servidor Central iniciado em http://127.0.0.1:8000", flush=True)
    print(f"Interface web: http://127.0.0.1:8000/  |  Replicas por arquivo: {alvo_replicas()}", flush=True)
    print(f"Metadados: {ARQUIVO_METADADOS}", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("Servidor Central encerrado.", flush=True)
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
