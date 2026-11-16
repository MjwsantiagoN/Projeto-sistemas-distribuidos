# Sistema de Arquivos Distribuídos Simplificado

## Escopo do projeto

O sistema possui quatro processos independentes: Cliente, Servidor Central e dois Servidores de Arquivos (A e B). Todos se comunicam por HTTP, trocando mensagens JSON. Entre o Cliente e o Central, todas as operações vão para um único endereço e o tipo da operação vem no próprio JSON (estilo RPC). Entre o Central e os servidores de arquivos, a comunicação segue o estilo REST, em que o método HTTP indica a operação.

O Cliente solicita upload, listagem, download e remoção ao Servidor Central. O Central mantém o índice de metadados (nome original, ID único e servidor responsável), escolhe o destino de novos arquivos e encaminha as operações aos servidores de arquivos. Os servidores A e B armazenam somente o conteúdo identificado pelo ID.

O índice de metadados é salvo em `metadados.json`, ao lado de `servidor_central.py`, para sobreviver à reinicialização do Central. Os arquivos permanecem nas pastas independentes `armazenamento_A/` e `armazenamento_B/`.

## Requisitos

- Python 3.9 ou superior;
- Nenhuma dependência externa.

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

Cada servidor de arquivos cria sua pasta (`armazenamento_A/` ou `armazenamento_B/`) quando é iniciado, se ela ainda não existir.

**Terminal 3 — Servidor Central**

```bash
python servidor_central.py
```

**Terminal 4 — Cliente**

```bash
python cliente.py
```

No cliente, escolha uma operação no menu. Não é necessário escolher A ou B: o Central decide onde armazenar cada novo arquivo e consulta os metadados nas operações seguintes. Encerre cada servidor com `Ctrl+C`.

## Contrato HTTP

### Cliente → Servidor Central

Todas as operações usam `POST http://127.0.0.1:8000/comunicacao` com `Content-Type: application/json`.

**Upload**

```json
{
  "operacao": "upload",
  "nome": "relatorio.pdf",
  "conteudo_base64": "..."
}
```

Resposta de sucesso: HTTP 201, com `resultado` contendo `status`, `nome`, `id` e `servidor`.

**Listagem**

```json
{"operacao": "listar"}
```

Resposta: HTTP 200, com `resultado.arquivos`, uma lista de objetos contendo `nome`, `id` e `servidor`.

**Download**

```json
{"operacao": "download", "nome": "relatorio.pdf"}
```

Resposta: HTTP 200, com `resultado.conteudo_base64` e o nome original.

**Remoção**

```json
{"operacao": "remover", "nome": "relatorio.pdf"}
```

Resposta: HTTP 200, com confirmação em `resultado`.

Erros são devolvidos com um status HTTP apropriado e um objeto JSON contendo `status: "erro"` e `mensagem`. O Central usa HTTP 404 para arquivo inexistente, HTTP 409 para nome já cadastrado e HTTP 502 quando não consegue alcançar um servidor de arquivos.

### Servidor Central → Servidores de Arquivos

O Central utiliza a API REST já implementada nos servidores:

- `GET /status` — verifica disponibilidade;
- `GET /arquivos` — lista IDs armazenados;
- `PUT /arquivos/{id}` — grava o conteúdo enviado em `conteudo_base64`;
- `GET /arquivos/{id}` — recupera o conteúdo em Base64;
- `DELETE /arquivos/{id}` — remove o arquivo.

O nome original não é enviado ao servidor de arquivos. O Central gera um ID único e mantém a associação entre nome, ID e servidor no arquivo `metadados.json`.

### Cuidados no servidor de arquivos

Na hora de implementar os servidores de arquivos, tivemos que pensar em alguns problemas que podiam acontecer.

**1. Ler ou gravar fora da pasta.** O ID do arquivo vem na URL, então alguém poderia mandar um ID como `..\..\arquivo`. No Windows, o `..\` significa "voltar uma pasta", e o servidor acabaria mexendo em arquivos fora da pasta de armazenamento. Esse tipo de ataque se chama *path traversal*. Para evitar isso, o servidor só aceita IDs com letras, números, `_` e `-`, qualquer outra coisa é recusada com erro 400.

**2. Arquivo corrompido se o servidor cair.** Se o servidor fosse desligado no meio da gravação, o arquivo podia ficar pela metade. Para resolver, o servidor grava primeiro num arquivo temporário (`.tmp`) e só no final renomeia para o nome certo. Como renomear é uma operação que acontece de uma vez só, ou o arquivo aparece completo, ou não aparece.

**3. Duas requisições mexendo na pasta ao mesmo tempo.** O servidor usa `ThreadingHTTPServer`, que atende cada requisição numa thread separada. Isso é bom para desempenho, mas duas threads poderiam mexer na pasta juntas (por exemplo, uma listando enquanto outra apaga um arquivo), o que é chamado de condição de corrida. Para evitar isso, usamos uma trava (`threading.Lock`) que garante exclusão mútua: enquanto uma thread está mexendo na pasta, as outras esperam a vez.

**4. Repetir uma gravação.** Se o Central mandar um arquivo e a resposta demorar demais (timeout), ele não tem como saber se a gravação funcionou ou não. Por isso fizemos a gravação com `PUT`: gravar de novo o mesmo ID só sobrescreve o arquivo, sem criar uma cópia. Isso não faz perder nada, porque numa repetição o conteúdo enviado é o mesmo, e arquivos diferentes nunca têm o mesmo ID (o servidor central gera um ID aleatório e único para cada upload). Assim o servidor central pode tentar de novo sem medo de duplicar nada.

## Distribuição dos arquivos

Para novos uploads, o Central conta quantos arquivos do índice pertencem a cada servidor e escolhe aquele com menos arquivos. Em caso de empate, escolhe A. A distribuição é simples e adequada ao MVP; não representa balanceamento por espaço livre ou capacidade real.

Se o servidor escolhido estiver fora do ar, o Central tenta o outro. Assim, enquanto pelo menos um servidor de arquivos estiver funcionando, o upload continua sendo aceito. Se nenhum responder, o Central devolve HTTP 502 avisando que não há servidor disponível.

Nomes duplicados são recusados com HTTP 409. O cliente não precisa informar o servidor para listar, baixar ou remover: essas operações são resolvidas pelo Central usando os metadados.

## Limites e observações

- O corpo máximo aceito pelo Central é 14 MiB; os servidores de arquivos aceitam até 15 MiB.
- O conteúdo é codificado em Base64 para poder trafegar dentro do JSON. Por isso, o tamanho máximo do arquivo em si é menor que o limite do corpo HTTP.
- Os metadados são centralizados em um arquivo JSON local. Se esse arquivo for apagado ou corrompido, o Central perde a associação entre nomes e IDs, mesmo que os bytes ainda estejam nas pastas dos servidores.
- Não há replicação: cada arquivo fica em um único servidor. Se esse servidor estiver fora do ar, o arquivo não pode ser baixado nem removido até ele voltar.
- O Central e o cliente estão configurados inicialmente para `127.0.0.1`. Para execução em máquinas diferentes, configure os endereços IP no código e inicie os servidores com `--host 0.0.0.0`; libere as portas correspondentes no firewall.

## Testes manuais recomendados

1. Inicie os quatro processos.
2. Faça upload de um arquivo e confira a confirmação com o servidor de destino.
3. Liste os arquivos e confirme que o nome aparece uma única vez.
4. Baixe o arquivo para outro diretório e compare o conteúdo com o original.
5. Remova o arquivo e confirme que ele não aparece mais na listagem.
6. Tente baixar ou remover um nome inexistente; o Central deve responder HTTP 404.
7. Tente enviar novamente um nome já cadastrado; o Central deve responder HTTP 409.
8. Encerre um servidor de arquivos e tente operar sobre um arquivo nele armazenado; o Central deve responder HTTP 502 sem apagar os metadados.
9. Reinicie o Central e liste os arquivos; os metadados devem continuar disponíveis.
10. Faça uploads até o B ter o mesmo número de arquivos que o A, ou menos. Encerre o B e faça um novo upload: ele deve ser gravado no A, e o log do Central deve mostrar `servidor B indisponivel, tentando o proximo`. Encerre também o A e tente outro upload; o Central deve responder que nenhum servidor de arquivos está disponível.