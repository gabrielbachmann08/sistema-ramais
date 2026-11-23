# Sistema de Ramais — Documento-resumo (RASCUNHO)

> Rascunho na ordem exigida pelo enunciado (seção 9). Tudo marcado com
> **[PREENCHER]** depende da equipe: nomes, links, capturas de tela e os
> resultados reais dos testes. Não invente resultados: rode e registre.
> Ao final, exporte para PDF.

## 1. Identificação

- **Título:** Sistema de Ramais — gerenciamento de ramais telefônicos corporativos
- **Disciplina:** PSI129A – Conectividade em Sistemas Ciberfísicos — Turma U, 2026/2
- **Integrantes:** [PREENCHER nome – matrícula] (até 4)
- **Vídeo:** [PREENCHER link]
- **Repositório:** [PREENCHER link]

## 2. Objetivo e cenário de uso

Uma empresa de pequeno ou médio porte tem dezenas de ramais telefônicos, e a
lista de "quem está em qual ramal" costuma viver em uma planilha desatualizada.
Quando alguém muda de setor ou um aparelho para de funcionar, ninguém fica
sabendo.

O Sistema de Ramais resolve isso com um cadastro central acessível pela rede
interna:

- **Equipe de TI (perfil admin):** cria, altera e remove ramais (número, nome e
  setor) pelo painel web ou pelo terminal.
- **Recepção e demais funcionários (perfil consulta):** buscam um ramal por
  número, nome ou setor, sem poder alterar nada.
- **Telefones:** cada aparelho avisa periodicamente a central de que está
  ligado; o painel mostra em tempo real quais ramais estão online e com qual IP.

Resultado esperado: uma lista única, sempre atualizada, e a identificação
imediata de aparelhos fora do ar.

## 3. Arquitetura e componentes

### 3.1 Topologia

[PREENCHER: inserir o diagrama (pode ser o do README redesenhado no draw.io).]

Rede Docker `ramais_net`, tipo bridge, definida pelo usuário.

### 3.2 Plano de endereçamento

Rede `172.28.0.0/24` — máscara `255.255.255.0` — gateway `172.28.0.1`.

| Nó | IP | Porta/protocolo | Serviço |
|---|---|---|---|
| servidor | 172.28.0.10 | 5050/TCP | Cadastro de ramais (protocolo próprio, JSON por linha) |
| servidor | 172.28.0.10 | 5051/UDP | Recebimento de heartbeat dos telefones |
| painel | 172.28.0.20 | 8080/TCP (publicada no host) | Painel web (HTTP) |
| telefone-1001 | 172.28.0.31 | origem efêmera → 5051/UDP | Simulador de telefone |
| telefone-1002 | 172.28.0.32 | origem efêmera → 5051/UDP | Simulador de telefone |
| telefone-1003 | 172.28.0.33 | origem efêmera → 5051/UDP | Simulador de telefone |
| cliente | 172.28.0.40 | origem efêmera → 5050/TCP | Cliente de terminal |

Os nomes (`servidor`, `painel`) são resolvidos pelo DNS interno do Docker (127.0.0.11).

### 3.3 Elementos

| Elemento | Sistema / versão | Software |
|---|---|---|
| Host | [PREENCHER: ex. Windows 11 + Docker Desktop x.y] | Docker Engine, Docker Compose |
| Contêineres (6) | Debian (imagem `python:3.12-slim`) | Python 3.12, iproute2, ping, traceroute, dig, tcpdump, procps |
| Banco de dados | SQLite 3 (embutido no Python) | Arquivo `/dados/ramais.db` em volume Docker |

### 3.4 Bibliotecas Python

Somente a biblioteca padrão; não há dependências externas para instalar.

| Biblioteca | Função no projeto | Justificativa |
|---|---|---|
| `socket` | Sockets TCP (clientes) e UDP (heartbeat) | Interface direta com a pilha TCP/IP |
| `socketserver` | `ThreadingTCPServer`: uma thread por cliente | Concorrência sem reescrever o laço de `accept` |
| `http.server` | `ThreadingHTTPServer` do painel | Servidor HTTP sem framework externo |
| `threading` | Threads, `Lock` e `Event` | Atender vários clientes e proteger dados compartilhados |
| `sqlite3` | Persistência dos ramais e usuários | Banco em arquivo, sem servidor extra |
| `json` | Formato das mensagens | Legível, fácil de depurar no tcpdump |
| `hashlib`, `hmac`, `secrets` | Hash de senha (PBKDF2), comparação segura, tokens | Não guardar senha em texto puro |
| `logging` | Logs em arquivo rotativo e na saída padrão | Registro de eventos e diagnóstico |
| `signal`, `os` | Encerramento limpo (SIGTERM), `umask`, PID | Integração com o sistema operacional |

## 4. Funcionamento

### 4.1 Fluxo de comunicação

1. O navegador envia uma requisição **HTTP** ao painel (porta 8080).
2. O painel abre uma conexão **TCP** com o servidor (porta 5050), envia o
   pedido em JSON com o token de sessão e devolve a resposta ao navegador,
   convertendo o código de erro em status HTTP (400, 401, 403, 404, 409, 503).
3. O cliente de terminal faz o mesmo caminho, sem o painel: TCP direto.
4. Em paralelo, cada telefone envia um datagrama **UDP** (porta 5051) a cada
   5 s. O servidor marca o ramal como online por 15 s a partir do último
   heartbeat.

### 4.2 Escolha dos protocolos de transporte

- **TCP no cadastro:** criar/alterar/remover um ramal não pode se perder,
  duplicar ou chegar fora de ordem. O TCP garante entrega e ordem, e a conexão
  permite várias operações em sequência.
- **UDP no heartbeat:** a mensagem tem cerca de 17 bytes e se repete a cada
  5 s. Perder uma não muda nada (são toleradas 2 perdas seguidas antes de o
  ramal virar offline). Com UDP, o servidor não precisa manter uma conexão
  aberta por telefone.
- **HTTP no painel:** protocolo de aplicação padronizado, permite usar
  qualquer navegador como cliente, sem instalar nada.

### 4.3 Formato das mensagens

Ver README, seção "Protocolo de aplicação próprio": um JSON por linha,
`{"acao": ..., "token": ..., ...}` → `{"ok": true, "dados": ...}` ou
`{"ok": false, "codigo": ..., "erro": ...}`.

### 4.4 Trechos de código principais

[PREENCHER: colar e explicar com as palavras da equipe. Sugestões:]

- `servidor.py` → classe `Atendente.handle`: laço que lê uma linha, processa e
  responde; trata `socket.timeout` e `OSError`; uma instância por thread.
- `servidor.py` → classe `Banco`: `threading.Lock` protegendo a conexão SQLite
  compartilhada entre as threads.
- `servidor.py` → `escutar_heartbeats`: thread única com `recvfrom`.
- `protocolo.py` → `ClienteRamais.pedir`: timeout, novas tentativas com espera
  crescente e reconexão.
- `painel.py` → `_repassar`: tradução HTTP ↔ protocolo TCP.

## 5. Testes e evidências

[PREENCHER a coluna "Resultado obtido" e as figuras depois de rodar cada teste.]

| ID | Objetivo | Procedimento | Resultado esperado | Resultado obtido | Evidência |
|---|---|---|---|---|---|
| T01 | CRUD pelo painel | Logar como admin; criar, editar e remover o ramal 1005 | Lista reflete cada operação; log registra quem fez | | Fig. |
| T02 | Clientes simultâneos | `docker compose run --rm cliente python -m testes.teste_carga --clientes 10` | 200 de 200 pedidos atendidos, sem bloqueio | | Fig. |
| T03 | Servidor fora do ar | `docker compose stop servidor`; usar painel e cliente; depois `start` | Painel mostra aviso (HTTP 503); cliente tenta reconectar; tudo volta após o `start` | | Fig. |
| T04 | Presença por UDP | `docker compose stop telefone-1002` | Ramal 1002 fica offline em até 15 s; volta com `start` | | Fig. |
| T05 | Permissão por perfil | Logar como `consulta` no cliente e tentar criar ramal | Erro "apenas administradores..." (código `proibido`) | | Fig. |
| T06 | Validação de entrada | Criar ramal com número `12a` e com número já existente | Erros `invalido` e `conflito`; nada gravado | | Fig. |
| T07 | Login incorreto | Senha errada no painel | HTTP 401; log "Login recusado" com o IP | | Fig. |
| T08 | Persistência | `docker compose restart servidor` | Ramais cadastrados continuam lá | | Fig. |
| T09 | Diagnóstico do Linux | `docker compose exec servidor sh scripts/diagnostico.sh` | IP 172.28.0.10, portas 5050/TCP e 5051/UDP em escuta, processo do usuário `ramais` | | Fig. |
| T10 | Captura de tráfego | `tcpdump` nas portas 5050 e 5051 durante um cadastro | JSON do pedido visível no TCP; heartbeats no UDP | | Fig. |

## 6. Problemas encontrados e soluções

[PREENCHER com o que realmente aconteceu com a equipe durante a montagem —
como foi diagnosticado (qual comando/log mostrou) e como foi resolvido.
Anotem enquanto montam o ambiente; é o que o professor quer ver aqui.]

## 7. Aprendizados

[PREENCHER: o que a equipe aprendeu e a contribuição de cada integrante.]

| Integrante | Contribuição |
|---|---|
| | |

## 8. Conclusões e trabalhos futuros

[PREENCHER a conclusão após os testes.] Limitações e melhorias possíveis:

- Cifrar o tráfego com TLS (hoje a senha trafega em texto puro na rede interna).
- Autenticar o heartbeat UDP (ex.: HMAC com chave por telefone), pois hoje
  qualquer nó da rede pode se passar por um ramal.
- Tela de cadastro de usuários (hoje vêm do arquivo `.env`).
- Histórico de alterações consultável pelo painel e alerta (e-mail/SMTP)
  quando um ramal fica offline.
- Integração com uma central telefônica real (ex.: Asterisk).

## 9. Bibliografia

- KUROSE, James F.; ROSS, Keith W. **Redes de computadores e a Internet**: uma abordagem top-down. 6. ed. São Paulo: Pearson Education do Brasil, 2014.
- TANENBAUM, Andrew S.; BOS, Herbert. **Sistemas operacionais modernos**. 4. ed. São Paulo: Pearson Education do Brasil, 2016.
- SILBERSCHATZ, Abraham. **Fundamentos de sistemas operacionais**. 9. ed. Rio de Janeiro: LTC, 2015.
- PYTHON SOFTWARE FOUNDATION. **socket — Low-level networking interface**. Disponível em: https://docs.python.org/3/library/socket.html. Acesso em: [PREENCHER data].
- PYTHON SOFTWARE FOUNDATION. **socketserver — A framework for network servers**. Disponível em: https://docs.python.org/3/library/socketserver.html. Acesso em: [PREENCHER data].
- PYTHON SOFTWARE FOUNDATION. **http.server — HTTP servers**. Disponível em: https://docs.python.org/3/library/http.server.html. Acesso em: [PREENCHER data].
- DOCKER INC. **Networking overview**. Disponível em: https://docs.docker.com/engine/network/. Acesso em: [PREENCHER data].

## 10. Declaração de uso de IA generativa

[AJUSTAR para descrever fielmente o uso que a equipe fez.]

"Durante a preparação deste projeto (código-fonte, arquivos de configuração e
rascunho deste documento), os autores usaram Claude (Anthropic), via Claude
Code, para gerar a versão inicial do código e da documentação. Após usar essa
ferramenta, os autores revisaram e editaram o conteúdo conforme necessário e
assumem total responsabilidade pelo conteúdo."
