# Sistema de Arquivos Distribuídos Simplificado

## Escopo da Parcial 2

O sistema possui quatro processos independentes: Cliente, Servidor Central e dois Servidores de Arquivos (A e B). A comunicação entre os componentes ocorre por HTTP/REST, com mensagens JSON.

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

## Distribuição dos arquivos

Para novos uploads, o Central conta quantos arquivos do índice pertencem a cada servidor e escolhe aquele com menos arquivos. Em caso de empate, escolhe A. A distribuição é simples e adequada ao MVP; não representa balanceamento por espaço livre ou capacidade real.

Nomes duplicados são recusados com HTTP 409. O cliente não precisa informar o servidor para listar, baixar ou remover: essas operações são resolvidas pelo Central usando os metadados.

## Limites e observações

- O corpo máximo aceito pelo Central é 14 MiB; os servidores de arquivos aceitam até 15 MiB.
- O conteúdo é codificado em Base64 para poder trafegar dentro do JSON. Por isso, o tamanho máximo do arquivo em si é menor que o limite do corpo HTTP.
- Os metadados são centralizados em um arquivo JSON local. Se esse arquivo for apagado ou corrompido, o Central perde a associação entre nomes e IDs, mesmo que os bytes ainda estejam nas pastas dos servidores.
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

Os testes manuais precisam ser executados no ambiente do grupo e suas evidências (logs e capturas de tela) devem ser anexadas à entrega. A documentação não afirma que esses testes foram executados apenas por estarem descritos aqui.
