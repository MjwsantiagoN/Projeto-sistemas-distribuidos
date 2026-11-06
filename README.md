# Sistema de Arquivos Distribuídos Simplificado — Parcial 1

## Escopo desta versão

Esta implementação atende ao objetivo da primeira parcial: demonstrar comunicação entre processos independentes usando HTTP/REST.

Há quatro processos:
1. Cliente;
2. Servidor Central;
3. Servidor de Arquivos A;
4. Servidor de Arquivos B.

O Cliente envia uma mensagem JSON ao Servidor Central. O Central identifica o servidor de destino (A ou B), encaminha uma nova requisição HTTP e devolve ao Cliente a resposta recebida do servidor de arquivos.

**Esta versão não implementa armazenamento de arquivos.** Upload, download, listagem, exclusão, metadados, replicação e autenticação ficam fora do escopo desta primeira demonstração.

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

### Terminal 3 — Servidor Central

```bash
python servidor_central.py
```

### Terminal 4 — Cliente

```bash
python cliente.py
```

No Cliente, escolha `A` ou `B`, digite uma mensagem e observe a resposta. Para encerrar o cliente, digite `sair`. Para encerrar cada servidor, use `Ctrl+C no respectivo terminal`.

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

Para executar em máquinas diferentes, substitua `127.0.0.1` pelos endereços IP acessíveis de cada máquina e ajuste os endereços em `servidor_central.py` e `cliente.py`. Também será necessário permitir as portas correspondentes no firewall. Na demonstração local, `127.0.0.1` é suficiente para provar a comunicação entre processos independentes no mesmo computador.

## Evidências para anexar à entrega

Registre capturas de tela mostrando:
- os dois Servidores de Arquivos iniciados;
- o Servidor Central iniciado;
- o Cliente recebendo resposta ao testar A;
- o Cliente recebendo resposta ao testar B;
- o erro HTTP 502 quando um dos servidores estiver desligado.

O diagrama arquitetural do grupo pode ser mantido sem alterações; esta implementação demonstra o fluxo de comunicação nele representado.
