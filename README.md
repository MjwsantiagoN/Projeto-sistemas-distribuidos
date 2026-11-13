# Sistema de Arquivos Distribuídos Simplificado

## Escopo desta versão

Esta implementação atende ao objetivo da primeira parcial: demonstrar comunicação entre processos independentes usando HTTP/REST.

Há quatro processos:
1. Cliente;
2. Servidor Central;
3. Servidor de Arquivos A;
4. Servidor de Arquivos B.

O Cliente envia uma mensagem JSON ao Servidor Central. O Central identifica o servidor de destino (A ou B), encaminha uma nova requisição HTTP e devolve ao Cliente a resposta recebida do servidor de arquivos.

**Entrega Parcial 2:** os Servidores de Arquivos já implementam armazenamento, listagem, download e remoção de arquivos (ver seção *API dos Servidores de Arquivos*). A integração com o Servidor Central e o Cliente está em andamento.

## Requisitos

- Python 3.9 ou superior;
- Não é necessário instalar dependências externas: a implementação utiliza apenas a biblioteca padrão do Python.

## Como executar

Abra quatro terminais na pasta que contém os arquivos. Execute os comandos abaixo, mantendo cada processo em seu próprio terminal.

### Terminal 1 — Servidor de Arquivos A

```bash
python servidor_arquivos.py --nome A --porta 8001
```

### Terminal 2 — Servidor de Arquivos B

```bash
python servidor_arquivos.py --nome B --porta 8002
```

Cada servidor de arquivos cria sua própria pasta ao lado do `.py` quando é iniciado (`armazenamento_A/` e `armazenamento_B/`). Uma pasta não tem nada a ver com a outra, então o A não enxerga os arquivos do B e vice-versa.

Para aceitar conexões vindas de outro computador, precisa adicionar `--host 0.0.0.0`:

```bash
python servidor_arquivos.py --nome A --porta 8001 --host 0.0.0.0
```

### Terminal 3 — Servidor Central

```bash
python servidor_central.py
```

### Terminal 4 — Cliente

```bash
python cliente.py
```

No Cliente, escolha `A` ou `B`, digite uma mensagem e observe a resposta. Para encerrar o cliente, digite `sair`. Para encerrar cada servidor, use `Ctrl+C` no respectivo terminal.

## Protocolo e contrato HTTP

Todos os processos usam HTTP sobre TCP e trocam dados no formato JSON.

- Cliente → Central: `POST http://127.0.0.1:8000/comunicacao`
- Central → Servidor A: `POST http://127.0.0.1:8001/comunicacao`
- Central → Servidor B: `POST http://127.0.0.1:8002/comunicacao`

Exemplo de requisição enviada pelo Cliente:

```json
{
  "servidor": "A",
  "mensagem": "Teste de comunicacao"
}
```

O Central encaminha ao servidor escolhido somente a mensagem:

```json
{
  "mensagem": "Teste de comunicacao"
}
```

Exemplo de resposta do Servidor de Arquivos:

```json
{
  "status": "sucesso",
  "servidor": "A",
  "mensagem_recebida": "Teste de comunicacao"
}
```

O Servidor Central devolve essa resposta ao Cliente, identificando o destino utilizado.

## API dos Servidores de Arquivos

Quem conversa com os servidores de arquivos é o Servidor Central. Tudo é enviado em JSON, e o conteúdo dos arquivos vai em base64, porque arquivos como PDF ou imagem não são texto e não dá para colocar os bytes direto dentro do JSON.

Os servidores não sabem o nome original dos arquivos, só o ID, que é gerado pelo Central. Quem guarda a relação entre nome e ID é o Central.

Rotas do servidor de arquivos:

- `GET /status` — mostra se o servidor está no ar e quantos arquivos ele tem
- `GET /arquivos` — lista os arquivos guardados
- `PUT /arquivos/{id}` — salva um arquivo (se o ID já existir, sobrescreve)
- `GET /arquivos/{id}` — baixa um arquivo
- `DELETE /arquivos/{id}` — apaga um arquivo

Para salvar, o corpo da requisição é só o conteúdo em base64:

```json
{"conteudo_base64": "b2xhIG11bmRv"}
```

E o download devolve algo assim:

```json
{
  "status": "sucesso",
  "servidor": "A",
  "id": "teste1",
  "tamanho": 9,
  "conteudo_base64": "b2xhIG11bmRv"
}
```

As outras rotas respondem no mesmo formato, sempre com `status` e `servidor`. Quando é um arquivo novo, o `PUT` devolve 201; quando sobrescreve, devolve 200.

A rota `POST /comunicacao` da Parcial 1 continua funcionando, para o Central atual não quebrar enquanto ele é atualizado.

### Erros

Quando dá erro, a resposta vem assim:

```json
{"status": "erro", "servidor": "A", "mensagem": "descrição do erro"}
```

O servidor devolve 404 quando o arquivo (ou a rota) não existe, e 400 quando a requisição vem errada: ID inválido, JSON mal formado, sem o campo `conteudo_base64`, base64 inválido ou corpo maior que 15 MB.

### Decisões de implementação

O ID do arquivo só pode ter letras, números, `_` e `-`. Fizemos isso porque, sem essa checagem, dava para mandar um ID tipo `..\..\arquivo` e o servidor acabaria lendo ou gravando fora da pasta dele.

Na hora de salvar, o servidor escreve primeiro em um arquivo `.tmp` e só depois renomeia para o nome final com `os.replace`. Assim, se o processo cair no meio da escrita, não sobra um arquivo pela metade na pasta.

Como o servidor usa `ThreadingHTTPServer`, ele pode atender várias requisições ao mesmo tempo. Para evitar que duas operações mexam na pasta juntas (por exemplo, listar enquanto outra requisição apaga um arquivo), usamos um `threading.Lock`.

A gravação usa `PUT`, e mandar o mesmo arquivo com o mesmo ID duas vezes não cria uma cópia, só sobrescreve. Isso é útil caso o Central dê timeout e precise tentar de novo.

Por fim, cada requisição pode ter no máximo 15 MB, para ninguém conseguir derrubar o servidor mandando um arquivo enorme que lotaria a memória.

### Testando só os servidores de arquivos

Dá para testar os servidores de arquivos sozinhos, sem o Central e o Cliente. Com o Servidor A rodando, em outro terminal:

```bash
# status
curl -s http://127.0.0.1:8001/status

# gravar ("b2xhIG11bmRv" = "ola mundo" em base64)
curl -s -X PUT http://127.0.0.1:8001/arquivos/teste1 -H "Content-Type: application/json" -d '{"conteudo_base64": "b2xhIG11bmRv"}'

# listar
curl -s http://127.0.0.1:8001/arquivos

# baixar
curl -s http://127.0.0.1:8001/arquivos/teste1

# remover
curl -s -X DELETE http://127.0.0.1:8001/arquivos/teste1
```

No PowerShell do Windows esses comandos `curl` não funcionam do mesmo jeito, então use:

```powershell
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("ola mundo"))
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:8001/arquivos/teste1 -ContentType "application/json" -Body (@{conteudo_base64=$b64} | ConvertTo-Json)
Invoke-RestMethod http://127.0.0.1:8001/arquivos
$r = Invoke-RestMethod http://127.0.0.1:8001/arquivos/teste1
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($r.conteudo_base64))
Invoke-RestMethod -Method Delete -Uri http://127.0.0.1:8001/arquivos/teste1
```

O que testamos:

| Cenário | Resultado |
|---|---|
| Gravar, listar, baixar e remover | `201`, arquivo aparece na lista, conteúdo igual ao enviado, `200` |
| Gravar de novo com o mesmo ID | `200`, sem criar cópia |
| Arquivo binário de 1 MB | Arquivo baixado igual ao enviado |
| Baixar ou remover arquivo que não existe | `404` |
| ID malicioso (`..\..\x`) ou base64 inválido | `400` |
| 6 gravações ao mesmo tempo | Todas gravadas certinho |
| Gravar no A e listar o B | O arquivo não aparece no B |

## Testes da demonstração

1. Inicie os quatro processos e envie uma mensagem para A.
2. Envie outra mensagem para B.
3. Encerre o processo do Servidor B com `Ctrl+C` e envie uma mensagem para B. O Central deve responder com HTTP 502, indicando que não conseguiu alcançar o servidor de destino.
4. Reinicie B para restaurar a execução normal.

O teste 3 é um cenário adverso de indisponibilidade de um componente distribuído.

## Relação com os requisitos da primeira parcial

- **Arquitetura:** Cliente, Servidor Central e dois processos de Servidor de Arquivos.
- **Responsabilidades:** cada processo possui uma função delimitada.
- **Mecanismo de comunicação:** requisições HTTP POST com payload JSON.
- **Execução distribuída:** os componentes são processos independentes e podem ser executados em máquinas diferentes após ajustar os endereços configurados no código.
- **Demonstração funcional:** o Cliente recebe a resposta originada no Servidor de Arquivos por intermédio do Central.
- **Cenário adverso:** indisponibilidade de um servidor de arquivos, tratada com resposta HTTP 502.

## Observação sobre execução em computadores diferentes

Para rodar em computadores diferentes, os servidores de arquivos precisam ser iniciados com `--host 0.0.0.0`, senão eles só aceitam conexões da própria máquina. Não precisa mexer no código deles.

No `servidor_central.py` e no `cliente.py`, troque `127.0.0.1` pelo IP da máquina onde está cada servidor (dá para ver o IP com `ipconfig` no Windows).

Também é preciso liberar as portas 8000 a 8002 no firewall das máquinas que rodam servidores. No Windows, num PowerShell aberto como administrador:

```powershell
New-NetFirewallRule -DisplayName "SD Projeto" -Direction Inbound -Protocol TCP -LocalPort 8000-8002 -Action Allow
```

Para testar tudo num computador só, `127.0.0.1` já funciona.
