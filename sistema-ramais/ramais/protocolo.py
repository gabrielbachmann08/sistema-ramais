"""Lado cliente do protocolo de ramais.

O protocolo e bem simples: cada mensagem e um objeto JSON em UMA linha,
terminada por "\\n", trafegando sobre TCP.

    pedido   -> {"acao": "listar", "token": "...", "filtro": "ana"}
    resposta -> {"ok": true,  "dados": [...]}
             ou {"ok": false, "codigo": "proibido", "erro": "mensagem"}

Esta classe e usada pelo painel web, pelo cliente de terminal e pelo teste
de carga, para que todos falem com o servidor do mesmo jeito.
"""

import json
import logging
import socket
import time

# Maior resposta aceita do servidor (uma listagem grande cabe com folga).
TAM_MAX_RESPOSTA = 1024 * 1024

log = logging.getLogger("ramais.protocolo")


class ErroConexao(Exception):
    """Nao foi possivel falar com o servidor (rede fora, timeout, recusado)."""


class ErroServidor(Exception):
    """O servidor respondeu, mas recusou o pedido (ok = false)."""

    def __init__(self, codigo, mensagem):
        super().__init__(mensagem)
        self.codigo = codigo


class ClienteRamais:
    """Conexao TCP com o servidor de ramais, com timeout e reconexao."""

    def __init__(self, host, porta, timeout=5.0, tentativas=3, espera=2.0, token=None):
        self.host = host
        self.porta = porta
        self.timeout = timeout        # segundos para conectar e para cada resposta
        self.tentativas = tentativas  # quantas vezes tentar antes de desistir
        self.espera = espera          # pausa base entre tentativas (cresce a cada falha)
        self.token = token            # token de sessao recebido no login
        self._sock = None
        self._arq = None

    # Permite usar "with ClienteRamais(...) as c:" e fechar o socket sozinho.
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()

    def _conectar(self):
        self._sock = socket.create_connection((self.host, self.porta), timeout=self.timeout)
        # makefile permite ler a resposta linha a linha com readline().
        self._arq = self._sock.makefile("rb")

    def fechar(self):
        for recurso in (self._arq, self._sock):
            try:
                if recurso is not None:
                    recurso.close()
            except OSError:
                pass
        self._sock = None
        self._arq = None

    def pedir(self, acao, **campos):
        """Envia um pedido e devolve o campo "dados" da resposta.

        Levanta ErroServidor se o servidor recusar o pedido e ErroConexao se,
        depois de todas as tentativas, nao houver comunicacao.
        """
        pedido = {"acao": acao, **campos}
        if self.token and acao != "login":
            pedido["token"] = self.token
        dados = (json.dumps(pedido, ensure_ascii=False) + "\n").encode("utf-8")

        ultimo_erro = None
        for tentativa in range(1, self.tentativas + 1):
            try:
                if self._sock is None:
                    self._conectar()
                self._sock.sendall(dados)
                linha = self._arq.readline(TAM_MAX_RESPOSTA)
                if not linha:
                    # readline vazio = o outro lado fechou a conexao.
                    raise ConnectionError("o servidor fechou a conexao")
                resposta = json.loads(linha.decode("utf-8"))
                break
            except (OSError, ValueError) as erro:
                # OSError cobre timeout, conexao recusada, rede fora e DNS;
                # ValueError cobre resposta que nao e JSON valido.
                ultimo_erro = erro
                self.fechar()
                log.warning("Falha de comunicacao com %s:%s (tentativa %d de %d): %s",
                            self.host, self.porta, tentativa, self.tentativas, erro)
                if tentativa < self.tentativas:
                    time.sleep(self.espera * tentativa)
        else:
            # O "else" do for so executa se nenhuma tentativa deu "break".
            raise ErroConexao(f"servidor {self.host}:{self.porta} indisponivel: {ultimo_erro}")

        if not resposta.get("ok"):
            raise ErroServidor(resposta.get("codigo", "erro"), resposta.get("erro", "erro desconhecido"))
        if acao == "login":
            self.token = resposta["dados"]["token"]
        elif acao == "sair":
            # O servidor fecha a conexao depois de "sair"; fechamos deste lado tambem.
            self.token = None
            self.fechar()
        return resposta.get("dados")
