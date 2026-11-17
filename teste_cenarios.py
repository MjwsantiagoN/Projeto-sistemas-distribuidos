"""Testes automatizados de cenarios do sistema de arquivos distribuido.

Sobe os processos REAIS (2 servidores de arquivos + Central com replicacao e frontend) numa copia
temporaria do projeto, executa os cenarios e grava evidencias em
./evidencias/ (log do teste e logs de cada processo).

Uso:  python teste_cenarios.py
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

AQUI = Path(__file__).resolve().parent
EVID = AQUI / "evidencias"
CENTRAL = "http://127.0.0.1:8000/comunicacao"
ARQ = {"A": "http://127.0.0.1:8001", "B": "http://127.0.0.1:8002"}

resultados = []
linhas_log = []


def log(msg=""):
    print(msg, flush=True)
    linhas_log.append(msg)


def chamar(url, corpo=None, metodo=None, timeout=15):
    dados = None if corpo is None else json.dumps(corpo).encode()
    req = Request(url, data=dados, method=metodo or ("POST" if dados else "GET"),
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except json.JSONDecodeError:
            return e.code, {}


def op(**kw):
    return chamar(CENTRAL, kw)


def b64(b):
    return base64.b64encode(b).decode()


def checar(nome, condicao, detalhe=""):
    resultados.append((nome, bool(condicao)))
    log(f"  [{'PASSOU' if condicao else 'FALHOU'}] {nome}" + (f"  -> {detalhe}" if detalhe else ""))


class Sistema:
    def __init__(self, raiz):
        self.raiz = raiz
        self.procs = {}
        self.logs = {}

    def iniciar(self, quem):
        cmds = {
            "A": ["servidor_arquivos.py", "--nome", "A", "--porta", "8001"],
            "B": ["servidor_arquivos.py", "--nome", "B", "--porta", "8002"],
            "C": ["servidor_central.py"],
        }
        arq = open(EVID / f"processo_{quem}.log", "a", encoding="utf-8")
        self.logs[quem] = arq
        self.procs[quem] = subprocess.Popen(
            [sys.executable, "-u", *cmds[quem]], cwd=self.raiz, stdout=arq, stderr=subprocess.STDOUT)
        alvo = {"A": ARQ["A"] + "/status", "B": ARQ["B"] + "/status",
                "C": "http://127.0.0.1:8000/status"}[quem]
        for _ in range(50):
            try:
                urlopen(alvo, timeout=1).read()
                return
            except (URLError, OSError):
                time.sleep(0.1)
        raise RuntimeError(f"processo {quem} nao subiu")

    def parar(self, quem):
        p = self.procs.pop(quem, None)
        if p:
            p.terminate()
            p.wait(5)
        self.logs[quem].write(f"--- processo {quem} encerrado pelo teste ---\n")
        self.logs[quem].flush()

    def parar_tudo(self):
        for q in list(self.procs):
            self.parar(q)
        for f in self.logs.values():
            f.close()


def main():
    if EVID.exists():
        shutil.rmtree(EVID)
    EVID.mkdir()
    raiz = Path(tempfile.mkdtemp(prefix="sd_teste_"))
    for f in ("servidor_arquivos.py", "servidor_central.py"):
        shutil.copy(AQUI / f, raiz / f)
    shutil.copytree(AQUI / "frontend", raiz / "frontend")
    s = Sistema(raiz)
    try:
        for q in ("A", "B", "C"):
            s.iniciar(q)
        log("=" * 70)
        log("TESTES DO SISTEMA DE ARQUIVOS DISTRIBUIDOS")
        log(time.strftime("Execucao: %Y-%m-%d %H:%M:%S"))
        log("=" * 70)

        # ---------- Cenario 1: fluxo normal ----------
        log("\nCENARIO 1 - Fluxo normal (upload, listar, download, remover)")
        original = os.urandom(200_000)
        h0 = hashlib.sha256(original).hexdigest()
        st, r = op(operacao="upload", nome="relatorio.bin", conteudo_base64=b64(original))
        checar("Upload retorna HTTP 201", st == 201, f"servidor={r['resultado']['servidor']}")
        st, r = op(operacao="listar")
        checar("Listagem contem o arquivo uma unica vez",
               [a["nome"] for a in r["resultado"]["arquivos"]] == ["relatorio.bin"])
        st, r = op(operacao="download", nome="relatorio.bin")
        baixado = base64.b64decode(r["resultado"]["conteudo_base64"])
        checar("Download retorna conteudo identico (SHA-256)", hashlib.sha256(baixado).hexdigest() == h0,
               h0[:16] + "...")
        st, r = op(operacao="remover", nome="relatorio.bin")
        checar("Remocao retorna HTTP 200", st == 200)
        st, r = op(operacao="listar")
        checar("Listagem vazia apos remocao", r["resultado"]["arquivos"] == [])

        # ---------- Cenario 2: erros de uso ----------
        log("\nCENARIO 2 - Erros de uso")
        st, _ = op(operacao="download", nome="nao_existe.txt")
        checar("Download de nome inexistente -> 404", st == 404)
        st, _ = op(operacao="remover", nome="nao_existe.txt")
        checar("Remocao de nome inexistente -> 404", st == 404)
        op(operacao="upload", nome="dup.txt", conteudo_base64=b64(b"x"))
        st, _ = op(operacao="upload", nome="dup.txt", conteudo_base64=b64(b"y"))
        checar("Upload de nome duplicado -> 409", st == 409)
        op(operacao="remover", nome="dup.txt")
        st, _ = op(operacao="upload", nome="../fora.txt", conteudo_base64=b64(b"x"))
        checar("Nome com '../' recusado pelo Central -> 400", st == 400)
        st, _ = chamar(ARQ["A"] + "/arquivos/..%2F..%2Fcentral", {"conteudo_base64": b64(b"x")}, "PUT")
        checar("Path traversal no servidor de arquivos recusado -> 400", st == 400)

        # ---------- Cenario 3: concorrencia ----------
        log("\nCENARIO 3 - Concorrencia (adverso)")
        with ThreadPoolExecutor(20) as ex:
            codigos = list(ex.map(
                lambda i: op(operacao="upload", nome="disputado.txt", conteudo_base64=b64(f"v{i}".encode()))[0],
                range(20)))
        checar("20 uploads simultaneos do MESMO nome: exatamente 1 sucesso (201) e 19 conflitos (409)",
               codigos.count(201) == 1 and codigos.count(409) == 19,
               f"201={codigos.count(201)}, 409={codigos.count(409)}")
        por_srv = {k: len(chamar(ARQ[k] + "/arquivos")[1]["arquivos"]) for k in "AB"}
        checar("Sem copias extras: exatamente 1 replica em cada servidor (2 no total)",
               por_srv == {"A": 1, "B": 1}, str(por_srv))
        op(operacao="remover", nome="disputado.txt")

        with ThreadPoolExecutor(20) as ex:
            codigos = list(ex.map(
                lambda i: op(operacao="upload", nome=f"par_{i:02d}.txt", conteudo_base64=b64(f"c{i}".encode()))[0],
                range(20)))
        checar("20 uploads simultaneos de nomes DISTINTOS: todos 201", codigos.count(201) == 20)
        arqs = op(operacao="listar")[1]["resultado"]["arquivos"]
        checar("Todos os 20 arquivos tem 2 replicas (A e B)", all(a["servidores"] == ["A", "B"] or sorted(a["servidores"]) == ["A", "B"] for a in arqs))
        por_srv = {k: len(chamar(ARQ[k] + "/arquivos")[1]["arquivos"]) for k in "AB"}
        checar("Cada servidor guarda os 20 arquivos", por_srv == {"A": 20, "B": 20}, str(por_srv))
        for a in arqs:
            op(operacao="remover", nome=a["nome"])

        # ---------- Cenario 4: redundancia - queda de uma replica ----------
        log("\nCENARIO 4 - Redundancia: queda do servidor B (adverso)")
        op(operacao="upload", nome="doc.txt", conteudo_base64=b64(b"conteudo do documento"))
        info = {a["nome"]: a for a in op(operacao="listar")[1]["resultado"]["arquivos"]}
        checar("Arquivo gravado em DUAS replicas (A e B)", sorted(info["doc.txt"]["servidores"]) == ["A", "B"])
        log("  >> Encerrando o processo do Servidor de Arquivos B")
        s.parar("B")
        st, r = op(operacao="download", nome="doc.txt")
        checar("Download continua funcionando com B fora (lido da replica em A) -> 200",
               st == 200 and base64.b64decode(r["resultado"]["conteudo_base64"]) == b"conteudo do documento"
               and r["resultado"]["servidor"] == "A", f"servidor={r['resultado'].get('servidor')}")
        st, r = op(operacao="upload", nome="novo_degradado.txt", conteudo_base64=b64(b"gravado so em A"))
        checar("Upload com B fora e aceito de forma degradada (1 replica) -> 201",
               st == 201 and r["resultado"]["degradado"] and r["resultado"]["servidores"] == ["A"])
        st, r = op(operacao="remover", nome="doc.txt")
        checar("Remocao com B fora: 200 e remocao pendente registrada em B",
               st == 200 and r["resultado"]["pendentes"] == ["B"], str(r["resultado"].get("pendentes")))
        log("  >> Reiniciando B (recuperacao + reparo automatico)")
        s.iniciar("B")
        reparado = False
        for _ in range(40):
            time.sleep(0.5)
            e = op(operacao="estado")[1]["resultado"]
            if e["subreplicados"] == 0 and e["remocoes_pendentes"] == 0:
                reparado = True
                break
        checar("Reparo automatico restaurou a redundancia e concluiu a remocao pendente", reparado)
        ids_B = [a["id"] for a in chamar(ARQ["B"] + "/arquivos")[1]["arquivos"]]
        lst = {a["nome"]: a for a in op(operacao="listar")[1]["resultado"]["arquivos"]}
        checar("'novo_degradado.txt' agora existe tambem em B",
               lst["novo_degradado.txt"]["id"] in ids_B and sorted(lst["novo_degradado.txt"]["servidores"]) == ["A", "B"])
        checar("Arquivo removido durante a queda NAO sobrou em B (sem orfao)", info["doc.txt"]["id"] not in ids_B)

        # ---------- Cenario 5: replica perdida/corrompida ----------
        log("\nCENARIO 5 - Redundancia: replica apagada do disco de A")
        id_nd = lst["novo_degradado.txt"]["id"]
        chamar(ARQ["A"] + f"/arquivos/{id_nd}", None, "DELETE")
        st, r = op(operacao="download", nome="novo_degradado.txt")
        checar("Download cai para a replica de B quando A perdeu o arquivo -> 200",
               st == 200 and r["resultado"]["servidor"] == "B")
        recriado = False
        for _ in range(40):
            time.sleep(0.5)
            if id_nd in [a["id"] for a in chamar(ARQ["A"] + "/arquivos")[1]["arquivos"]]:
                recriado = True
                break
        checar("Reparo detectou a replica ausente e recriou em A", recriado)

        # ---------- Cenario 6: todos fora ----------
        log("\nCENARIO 6 - Todos os servidores de arquivos fora (adverso)")
        s.parar("A")
        s.parar("B")
        st, r = op(operacao="upload", nome="sem_servidor.txt", conteudo_base64=b64(b"z"))
        checar("Upload sem nenhum servidor disponivel -> 502", st == 502, r.get("mensagem", ""))
        st, _ = op(operacao="download", nome="novo_degradado.txt")
        checar("Download sem nenhuma replica disponivel -> 502", st == 502)
        st, _ = op(operacao="remover", nome="novo_degradado.txt")
        checar("Remocao sem nenhuma replica alcancavel -> 502", st == 502)
        nomes = [a["nome"] for a in op(operacao="listar")[1]["resultado"]["arquivos"]]
        checar("Metadados preservados (arquivo continua listado, upload falho nao deixou entrada)",
               "novo_degradado.txt" in nomes and "sem_servidor.txt" not in nomes)
        s.iniciar("A")
        s.iniciar("B")

        # ---------- Cenario 7: persistencia dos metadados ----------
        log("\nCENARIO 7 - Reinicio do Servidor Central")
        antes = op(operacao="listar")[1]["resultado"]["arquivos"]
        s.parar("C")
        s.iniciar("C")
        depois = op(operacao="listar")[1]["resultado"]["arquivos"]
        checar("Metadados (inclusive lista de replicas) identicos apos reiniciar o Central",
               antes == depois and len(antes) >= 1, f"{len(depois)} arquivo(s)")
        st, r = op(operacao="download", nome="novo_degradado.txt")
        checar("Download funciona apos reinicio do Central", st == 200)

        # ---------- Cenario 8: idempotencia do PUT ----------
        log("\nCENARIO 8 - Idempotencia da gravacao (reenvio apos timeout)")
        corpo = {"conteudo_base64": b64(b"mesmo conteudo")}
        st1, _ = chamar(ARQ["A"] + "/arquivos/id-teste-123", corpo, "PUT")
        st2, _ = chamar(ARQ["A"] + "/arquivos/id-teste-123", corpo, "PUT")
        ids = [a["id"] for a in chamar(ARQ["A"] + "/arquivos")[1]["arquivos"]]
        checar("PUT repetido: 1o -> 201 (criado), 2o -> 200 (sobrescrito)", (st1, st2) == (201, 200), f"{st1},{st2}")
        checar("PUT repetido nao duplica o arquivo", ids.count("id-teste-123") == 1)
        chamar(ARQ["A"] + "/arquivos/id-teste-123", None, "DELETE")

        # ---------- Cenario 9: busca por nome ----------
        log("\nCENARIO 9 - Busca por nome")
        for n in ("Relatório_Final.pdf", "relatorio_parcial.txt", "fotos.zip"):
            op(operacao="upload", nome=n, conteudo_base64=b64(b"x"))
        st, r = op(operacao="buscar", termo="RELATORIO")
        achados = sorted(a["nome"] for a in r["resultado"]["arquivos"])
        checar("Busca por trecho, sem diferenciar maiusculas/acentos",
               st == 200 and achados == ["Relatório_Final.pdf", "relatorio_parcial.txt"], str(achados))
        st, r = op(operacao="buscar", termo="inexistente_xyz")
        checar("Busca sem resultado devolve lista vazia (200)", st == 200 and r["resultado"]["arquivos"] == [])
        st, _ = op(operacao="buscar", termo="  ")
        checar("Busca com termo vazio -> 400", st == 400)
        for n in ("Relatório_Final.pdf", "relatorio_parcial.txt", "fotos.zip"):
            op(operacao="remover", nome=n)

        # ---------- Cenario 10: frontend ----------
        log("\nCENARIO 10 - Interface web")
        with urlopen("http://127.0.0.1:8000/", timeout=5) as r:
            html = r.read().decode()
            checar("GET / devolve a pagina do frontend", r.status == 200 and "Arquivos distribu" in html)
        e = op(operacao="estado")[1]["resultado"]
        checar("Operacao 'estado' informa A e B online", all(x["online"] for x in e["servidores"]) and len(e["servidores"]) == 2)
    finally:
        s.parar_tudo()
        shutil.rmtree(raiz, ignore_errors=True)

    ok = sum(1 for _, v in resultados if v)
    log("\n" + "=" * 70)
    log(f"RESUMO: {ok}/{len(resultados)} verificacoes passaram")
    log("=" * 70)
    (EVID / "resultado_testes.log").write_text("\n".join(linhas_log) + "\n", encoding="utf-8")
    sys.exit(0 if ok == len(resultados) else 1)


if __name__ == "__main__":
    main()
