# Sistema de Arquivos Distribuídos Simplificado

## Escopo do projeto

O sistema possui quatro processos independentes: Cliente, Servidor Central e dois Servidores de Arquivos (A e B). Todos se comunicam por HTTP, trocando mensagens JSON. Entre o Cliente e o Central, todas as operações vão para um único endereço e o tipo da operação vem no próprio JSON (estilo RPC). Entre o Central e os servidores de arquivos, a comunicação segue o estilo REST, em que o método HTTP indica a operação.

O Cliente (terminal **ou interface web**) solicita upload, listagem, download e remoção ao Servidor Central. O Central mantém o índice de metadados (nome original, ID único e **lista de servidores que guardam réplicas**), decide onde gravar, encaminha as operações e **repara a redundância em segundo plano**. Os servidores A e B armazenam somente o conteúdo identificado pelo ID.

**Redundância:** cada arquivo é gravado em **duas réplicas** (A e B). Se um servidor cair, os arquivos continuam disponíveis na outra réplica, e quando o servidor volta o Central recria automaticamente o que faltava.

O índice de metadados é salvo em `metadados.json` (e as remoções pendentes em `remocoes_pendentes.json`), ao lado de `servidor_central.py`, para sobreviver à reinicialização do Central. Os arquivos permanecem nas pastas independentes `armazenamento_A/` e `armazenamento_B/`.

## Tecnologias e o que é do grupo

Python puro (biblioteca padrão: `http.server`, `urllib`, `threading`, `json`, `base64`, `uuid`) e uma página HTML/CSS/JavaScript sem dependências. A biblioteca fornece o servidor HTTP, as threads e os locks. O grupo implementou o protocolo de mensagens, o índice de metadados e sua persistência, a replicação, o failover de leitura, o reparo automático, a validação de entradas, a gravação atômica, a idempotência, o cliente e a interface web.

## Requisitos

- Python 3.9 ou superior;
- Nenhuma dependência externa (a interface web roda em qualquer navegador moderno).

## Como executar

Abra quatro terminais na pasta do projeto e mantenha cada processo em execução.

**Terminal 1 — Servidor de Arquivos A**

```bash
python servidor_arquivos.py --nome A --porta 8001
```

**Terminal 2 — Servidor de Arquivos B**

```bash
python servidor_arquivos.py --nome B --porta 8002
```

**Terminal 3 — Servidor Central**

```bash
python servidor_central.py
```

**Terminal 4 — Cliente de terminal (opcional)**

```bash
python cliente.py
```

**Interface web:** com o Central em execução, abra <http://127.0.0.1:8000/> no navegador. No topo ficam os servidores A e B (online ou fora do ar, com a quantidade de cópias de cada um) e o resumo da redundância. Na lista, cada arquivo mostra duas posições, A e B, ligadas por um traço: **traço cheio e ✓** = cópia presente nos dois; **traço tracejado e posição hachurada** = falta a cópia naquele servidor; **⏻ cinza** = a cópia existe, mas o servidor está fora do ar. Também é possível enviar (clique ou arraste), buscar pelo nome, baixar e remover arquivos. O painel "O que o Central fez" mostra em tempo real o failover, os uploads degradados e o reparo. O botão "Reparar agora" força uma rodada do reparo. A página é um único arquivo (`frontend/index.html`) com as fontes Barlow embutidas, então funciona sem internet. Encerre cada processo com `Ctrl+C`.

No cliente ou na interface web não é necessário escolher A ou B: o Central decide e consulta os metadados nas operações seguintes.

## Contrato HTTP

### Cliente → Servidor Central

Todas as operações usam `POST http://127.0.0.1:8000/comunicacao` com `Content-Type: application/json`. `GET /` devolve a interface web.

**Upload**

```json
{
  "operacao": "upload",
  "nome": "relatorio.pdf",
  "conteudo_base64": "..."
}
```

Resposta de sucesso: HTTP 201, com `resultado` contendo `status`, `nome`, `id`, `servidores` (lista onde foi gravado), `replicas` e `degradado` (`true` se alguma réplica não pôde ser gravada agora).

**Listagem**

```json
{"operacao": "listar"}
```

Resposta: HTTP 200, com `resultado.arquivos`, lista de objetos contendo `nome`, `id`, `servidores`, `replicas` e `degradado`, além de `alvo_replicas`.

**Busca por nome**

```json
{"operacao": "buscar", "termo": "relat"}
```

Resposta: HTTP 200, no mesmo formato da listagem, apenas com os arquivos cujo nome contém o trecho (sem diferenciar maiúsculas/minúsculas nem acentos: `relatorio` encontra `Relatório_Final.pdf`). Termo vazio retorna HTTP 400. A busca é feita pelo Central sobre o índice de metadados, sem consultar os servidores de arquivos.

**Download**

```json
{"operacao": "download", "nome": "relatorio.pdf"}
```

Resposta: HTTP 200, com `resultado.conteudo_base64` e `resultado.servidor` (a réplica que respondeu). O Central tenta cada réplica até uma responder.

**Remoção**

```json
{"operacao": "remover", "nome": "relatorio.pdf"}
```

Resposta: HTTP 200, com confirmação e a lista `pendentes` (réplicas offline cuja remoção será concluída pelo reparo).

**Estado e reparo (usadas pela interface web)**

```json
{"operacao": "estado"}
{"operacao": "reparar"}
```

`estado` devolve a disponibilidade de cada servidor, o total de arquivos, quantos estão com redundância reduzida, remoções pendentes e os eventos recentes. `reparar` executa uma rodada do reparo e devolve o mesmo estado.

Erros são devolvidos com um status HTTP apropriado e um objeto JSON contendo `status: "erro"` e `mensagem`. O Central usa HTTP 404 para arquivo inexistente, HTTP 409 para nome já cadastrado e HTTP 502 quando nenhuma réplica/servidor necessário pode ser alcançado.

### Servidor Central → Servidores de Arquivos

O Central utiliza a API REST já implementada nos servidores:

- `GET /status` — verifica disponibilidade;
- `GET /arquivos` — lista IDs armazenados;
- `PUT /arquivos/{id}` — grava o conteúdo enviado em `conteudo_base64`;
- `GET /arquivos/{id}` — recupera o conteúdo em Base64;
- `DELETE /arquivos/{id}` — remove o arquivo.

O nome original não é enviado ao servidor de arquivos. O Central gera um ID único e mantém a associação entre nome, ID e servidores no arquivo `metadados.json`.

### Cuidados no servidor de arquivos

Na hora de implementar os servidores de arquivos, tivemos que pensar em alguns problemas que podiam acontecer.

**1. Ler ou gravar fora da pasta.** O ID do arquivo vem na URL, então alguém poderia mandar um ID como `..\..\arquivo`. No Windows, o `..\` significa "voltar uma pasta", e o servidor acabaria mexendo em arquivos fora da pasta de armazenamento. Esse tipo de ataque se chama *path traversal*. Para evitar isso, o servidor só aceita IDs com letras, números, `_` e `-`, qualquer outra coisa é recusada com erro 400.

**2. Arquivo corrompido se o servidor cair.** Se o servidor fosse desligado no meio da gravação, o arquivo podia ficar pela metade. Para resolver, o servidor grava primeiro num arquivo temporário (`.tmp`) e só no final renomeia para o nome certo. Como renomear é uma operação que acontece de uma vez só, ou o arquivo aparece completo, ou não aparece.

**3. Duas requisições mexendo na pasta ao mesmo tempo.** O servidor usa `ThreadingHTTPServer`, que atende cada requisição numa thread separada. Isso é bom para desempenho, mas duas threads poderiam mexer na pasta juntas (por exemplo, uma listando enquanto outra apaga um arquivo), o que é chamado de condição de corrida. Para evitar isso, usamos uma trava (`threading.Lock`) que garante exclusão mútua: enquanto uma thread está mexendo na pasta, as outras esperam a vez.

**4. Repetir uma gravação.** Se o Central mandar um arquivo e a resposta demorar demais (timeout), ele não tem como saber se a gravação funcionou ou não. Por isso fizemos a gravação com `PUT`: gravar de novo o mesmo ID só sobrescreve o arquivo, sem criar uma cópia. Isso não faz perder nada, porque numa repetição o conteúdo enviado é o mesmo, e arquivos diferentes nunca têm o mesmo ID (o servidor central gera um ID aleatório e único para cada upload). Assim o servidor central pode tentar de novo sem medo de duplicar nada.

## Redundância (replicação)

**Escrita.** O Central grava o arquivo em até `FATOR_REPLICACAO` servidores (2 por padrão, ou seja, A e B). Se um servidor estiver fora do ar, o upload **continua** com as réplicas disponíveis e é marcado como *degradado*; só falha (HTTP 502) se nenhum servidor responder. Opta-se por disponibilidade: o usuário não fica impedido de gravar por causa de uma única queda.

**Leitura (failover).** O download tenta cada réplica registrada no índice até uma responder. Um servidor offline, ou um servidor que perdeu o arquivo no disco, não impede a leitura enquanto outra réplica estiver disponível.

**Remoção.** O Central remove de todas as réplicas alcançáveis. Se alguma estiver offline, registra uma *remoção pendente* (persistida em `remocoes_pendentes.json`) que é concluída quando o servidor voltar, evitando arquivos órfãos. Se nenhuma réplica puder ser alcançada, responde 502 e mantém o índice.

**Reparo automático (anti-entropia).** Uma thread do Central roda a cada `INTERVALO_REPARO` segundos (3 s) e: (1) conclui remoções pendentes; (2) compara o índice com o conteúdo real de cada servidor online e descarta réplicas que sumiram do disco; (3) copia o arquivo de uma réplica saudável para o servidor que está sem ela, restaurando a redundância. A interface web mostra esse processo em tempo real.

**Consistência.** O índice é protegido por exclusão mútua (`RLock`): verificar o nome e registrá-lo acontecem na mesma seção crítica, então uploads simultâneos do mesmo nome resultam em um sucesso e os demais HTTP 409. O índice só passa a conter o arquivo depois de gravado em pelo menos uma réplica.

## Limites e observações

- O corpo máximo aceito pelo Central é 14 MiB; os servidores de arquivos aceitam até 15 MiB.
- O conteúdo é codificado em Base64 para poder trafegar dentro do JSON. Por isso, o tamanho máximo do arquivo em si (≈ 10 MiB) é menor que o limite do corpo HTTP.
- O Central é um ponto único de falha, e os metadados ficam em um arquivo JSON local. Se esse arquivo for apagado ou corrompido, o Central perde a associação entre nomes e IDs, mesmo que os bytes ainda estejam nas pastas dos servidores.
- Se o Central cair entre gravar nos servidores e registrar o índice, pode sobrar uma réplica órfã (sem entrada no índice). Não há reconciliação de órfãos.
- Não há autenticação nem criptografia (TLS).
- O Central e o cliente estão configurados inicialmente para `127.0.0.1`. Para execução em máquinas diferentes, configure os endereços IP em `SERVIDORES` (no Central) e inicie os servidores com `--host 0.0.0.0`; libere as portas correspondentes no firewall.

## Testes automatizados

O script `teste_cenarios.py` sobe os processos reais (A, B e Central) em uma cópia temporária do projeto, executa os cenários e grava as evidências em `evidencias/`:

```bash
python teste_cenarios.py
```

Cenários cobertos (37 verificações): fluxo normal com conferência de SHA-256; erros 404/409/400 e path traversal; **concorrência** (20 uploads simultâneos do mesmo nome → 1 sucesso e 19 conflitos; 20 nomes distintos, todos com 2 réplicas); **queda do servidor B** (leitura pela réplica de A, upload degradado, remoção pendente, reparo automático ao B voltar); **réplica apagada do disco** (leitura por outra réplica e recriação); todos os servidores fora (502 sem corromper o índice); reinício do Central; idempotência do `PUT`; busca por nome; interface web. As portas 8000–8002 devem estar livres. O código de saída é 0 quando tudo passa.

Evidências: `evidencias/resultado_testes.log`, `evidencias/processo_A/B/C.log` e capturas da interface web (`frontend_servidor_B_fora.png`, `frontend_apos_reparo.png`).

## Roteiro da demonstração (interface web)

1. Inicie A, B e o Central e abra <http://127.0.0.1:8000/>. Os dois servidores aparecem *online*.
2. Envie um arquivo: ele aparece com réplicas **A ✓ B ✓** e redundância *Completa (2/2)*.
3. Baixe o arquivo e confira o conteúdo; o evento mostra de qual servidor veio.
4. Encerre o Servidor B (`Ctrl+C`). O painel marca B como *offline*. Baixe o arquivo de novo: continua funcionando, lido de A.
5. Envie outro arquivo com B fora: fica *Degradada (1/2)* e o log mostra `[DEGRADADO]`.
6. Suba o B novamente. Em até ~3 s o reparo copia o arquivo para B e a redundância volta a *Completa*; o log mostra `REPARO: ... copiado de A para B`.
7. (Opcional) Apague manualmente um arquivo da pasta `armazenamento_A/`: o download ainda funciona (via B) e o reparo o recria.

## Testes manuais pelo terminal

1. Inicie os quatro processos.
2. Faça upload de um arquivo e confira a confirmação com os servidores (`A,B`).
3. Liste os arquivos e confirme que o nome aparece uma única vez. Use a opção 5 (Buscar por nome) com um trecho do nome, em maiúsculas ou sem acento, e confirme o resultado filtrado.
4. Baixe o arquivo para outro diretório e compare o conteúdo com o original.
5. Remova o arquivo e confirme que ele não aparece mais na listagem.
6. Tente baixar ou remover um nome inexistente; o Central deve responder HTTP 404.
7. Tente enviar novamente um nome já cadastrado; o Central deve responder HTTP 409.
8. Encerre o servidor B e baixe um arquivo: deve funcionar (réplica de A) e o log do Central mostra `servidor B indisponivel, tentando outra replica`.
9. Com B fora, faça um upload: o cliente avisa "Redundância reduzida". Reinicie o B e veja no log do Central `REPARO: ... copiado de A para B`.
10. Reinicie o Central e liste os arquivos; os metadados (incluindo as réplicas) devem continuar disponíveis.
11. Encerre A e B e tente um upload; o Central deve responder que nenhum servidor de arquivos está disponível (HTTP 502).
