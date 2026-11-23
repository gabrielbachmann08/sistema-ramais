"""Servidor central de ramais.

Faz duas coisas ao mesmo tempo:

1. TCP (porta 5050): recebe pedidos de cadastro/consulta de ramais. Cada
   cliente conectado e atendido por uma thread propria (ThreadingTCPServer).
   TCP foi escolhido porque um cadastro nao pode se perder nem chegar
   duplicado ou fora de ordem.

2. UDP (porta 5051): recebe o "estou vivo" (heartbeat) que cada telefone
   manda a cada poucos segundos. UDP foi escolhido porque a mensagem e
   minuscula, repetida o tempo todo, e perder uma nao tem importancia: a
   proxima chega em seguida.

Os ramais ficam em um banco SQLite dentro do diretorio de dados.

Executar:  python -m ramais.servidor
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import signal
import socket
import socketserver
import sqlite3
import threading
import time
from logging.handlers import RotatingFileHandler

# ---------------------------------------------------------------- configuracao
# Tudo vem de variaveis de ambiente para o mesmo codigo rodar no Docker,
# em uma VM ou direto na maquina.
HOST = os.environ.get("RAMAIS_HOST", "0.0.0.0")
PORTA_TCP = int(os.environ.get("RAMAIS_PORTA_TCP", "5050"))
PORTA_UDP = int(os.environ.get("RAMAIS_PORTA_UDP", "5051"))
DIR_DADOS = os.environ.get("RAMAIS_DIR_DADOS", "dados")

TAM_MAX_PEDIDO = 4096    # bytes; pedidos maiores sao recusados
TIMEOUT_CLIENTE = 60     # segundos sem receber nada ate derrubar a conexao
TEMPO_ONLINE = 15        # segundos sem heartbeat ate o ramal virar "offline"
VALIDADE_TOKEN = 3600    # segundos de validade de uma sessao
ITERACOES_HASH = 100_000

RE_NUMERO = re.compile(r"\d{3,5}")  # um ramal tem de 3 a 5 digitos

RAMAIS_EXEMPLO = [
    ("1001", "Recepcao", "Atendimento"),
    ("1002", "Ana Souza", "Financeiro"),
    ("1003", "Carlos Lima", "TI"),
    ("1004", "Sala de Reunioes", "Administrativo"),
]

log = logging.getLogger("ramais.servidor")


class ErroPedido(Exception):
    """Pedido recusado. O codigo permite ao cliente saber o motivo."""

    def __init__(self, codigo, mensagem):
        super().__init__(mensagem)
        self.codigo = codigo


# ------------------------------------------------------------------- validacao
def validar_numero(valor):
    if not isinstance(valor, str) or not RE_NUMERO.fullmatch(valor):
        raise ErroPedido("invalido", "o numero do ramal deve ter de 3 a 5 digitos")
    return valor


def validar_texto(valor, campo, minimo, maximo):
    if not isinstance(valor, str):
        raise ErroPedido("invalido", f"o campo '{campo}' e obrigatorio")
    valor = valor.strip()
    if not minimo <= len(valor) <= maximo or not valor.isprintable():
        raise ErroPedido("invalido", f"o campo '{campo}' deve ter de {minimo} a {maximo} caracteres")
    return valor


# ----------------------------------------------------------------------- senha
def gerar_hash(senha, sal):
    """A senha nunca e guardada: guarda-se o hash PBKDF2-SHA256 com sal."""
    return hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), sal, ITERACOES_HASH)


# ----------------------------------------------------------------------- banco
class Banco:
    """Acesso ao SQLite.

    Varias threads usam a mesma conexao, entao todo acesso passa por uma
    trava (Lock): so uma thread mexe no banco por vez.
    """

    def __init__(self, caminho):
        self._trava = threading.Lock()
        self._con = sqlite3.connect(caminho, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        with self._trava, self._con:
            self._con.execute(
                "CREATE TABLE IF NOT EXISTS ramais ("
                " numero TEXT PRIMARY KEY, nome TEXT NOT NULL, setor TEXT NOT NULL)")
            self._con.execute(
                "CREATE TABLE IF NOT EXISTS usuarios ("
                " usuario TEXT PRIMARY KEY, sal BLOB NOT NULL, hash BLOB NOT NULL,"
                " perfil TEXT NOT NULL)")

    def listar(self, filtro=""):
        padrao = f"%{filtro}%"
        with self._trava:
            linhas = self._con.execute(
                "SELECT numero, nome, setor FROM ramais"
                " WHERE numero LIKE ? OR nome LIKE ? OR setor LIKE ? ORDER BY numero",
                (padrao, padrao, padrao)).fetchall()
        return [dict(linha) for linha in linhas]

    def existe(self, numero):
        with self._trava:
            return self._con.execute(
                "SELECT 1 FROM ramais WHERE numero = ?", (numero,)).fetchone() is not None

    def criar(self, numero, nome, setor):
        try:
            # "with self._con" faz commit se der certo e rollback se der erro.
            with self._trava, self._con:
                self._con.execute("INSERT INTO ramais VALUES (?, ?, ?)", (numero, nome, setor))
        except sqlite3.IntegrityError:
            raise ErroPedido("conflito", f"o ramal {numero} ja existe") from None

    def atualizar(self, numero, novo_numero, nome, setor):
        try:
            with self._trava, self._con:
                cursor = self._con.execute(
                    "UPDATE ramais SET numero = ?, nome = ?, setor = ? WHERE numero = ?",
                    (novo_numero, nome, setor, numero))
        except sqlite3.IntegrityError:
            raise ErroPedido("conflito", f"o ramal {novo_numero} ja existe") from None
        if cursor.rowcount == 0:
            raise ErroPedido("nao_encontrado", f"o ramal {numero} nao existe")

    def remover(self, numero):
        with self._trava, self._con:
            cursor = self._con.execute("DELETE FROM ramais WHERE numero = ?", (numero,))
        if cursor.rowcount == 0:
            raise ErroPedido("nao_encontrado", f"o ramal {numero} nao existe")

    def buscar_usuario(self, usuario):
        with self._trava:
            return self._con.execute(
                "SELECT sal, hash, perfil FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()

    def gravar_usuario(self, usuario, senha, perfil):
        sal = secrets.token_bytes(16)
        with self._trava, self._con:
            self._con.execute("INSERT OR REPLACE INTO usuarios VALUES (?, ?, ?, ?)",
                              (usuario, sal, gerar_hash(senha, sal), perfil))

    def semear_exemplos(self):
        with self._trava, self._con:
            if self._con.execute("SELECT COUNT(*) FROM ramais").fetchone()[0] == 0:
                self._con.executemany("INSERT INTO ramais VALUES (?, ?, ?)", RAMAIS_EXEMPLO)


# --------------------------------------------------------------------- servico
class Servico:
    """Regras do sistema: sessoes, permissoes, presenca e as acoes do protocolo."""

    def __init__(self, banco):
        self.banco = banco
        self.inicio = time.time()
        self.trava = threading.Lock()  # protege os dicionarios e contadores abaixo
        self.sessoes = {}              # token -> (usuario, perfil, expira_em)
        self.presenca = {}             # numero -> (hora do ultimo heartbeat, ip)
        self.conexoes = 0              # clientes TCP conectados agora
        self.heartbeats = 0            # heartbeats UDP aceitos desde o inicio

    # --- presenca (UDP) ---
    def registrar_heartbeat(self, dados, ip):
        try:
            numero = json.loads(dados.decode("utf-8")).get("ramal")
        except (ValueError, AttributeError):
            log.warning("Heartbeat invalido vindo de %s", ip)
            return
        if not isinstance(numero, str) or not RE_NUMERO.fullmatch(numero) or not self.banco.existe(numero):
            log.warning("Heartbeat de ramal nao cadastrado (%r) vindo de %s", numero, ip)
            return
        with self.trava:
            if numero not in self.presenca:
                log.info("Ramal %s ficou online (%s)", numero, ip)
            self.presenca[numero] = (time.time(), ip)
            self.heartbeats += 1

    def _online(self, numero, agora):
        visto = self.presenca.get(numero)
        if visto and agora - visto[0] <= TEMPO_ONLINE:
            return visto[1]
        return None

    # --- sessoes ---
    def _autenticar(self, pedido):
        token = pedido.get("token")
        with self.trava:
            sessao = self.sessoes.get(token) if isinstance(token, str) else None
            if sessao and sessao[2] < time.time():
                del self.sessoes[token]
                sessao = None
        if not sessao:
            raise ErroPedido("nao_autenticado", "faca login primeiro")
        return sessao[0], sessao[1]

    def _exigir_admin(self, pedido):
        usuario, perfil = self._autenticar(pedido)
        if perfil != "admin":
            raise ErroPedido("proibido", "apenas administradores podem alterar ramais")
        return usuario

    # --- acoes do protocolo ---
    def acao_login(self, pedido, ip):
        usuario, senha = pedido.get("usuario"), pedido.get("senha")
        registro = self.banco.buscar_usuario(usuario) if isinstance(usuario, str) else None
        correta = (registro is not None and isinstance(senha, str)
                   # compare_digest compara em tempo constante (evita ataque de tempo)
                   and hmac.compare_digest(gerar_hash(senha, registro["sal"]), registro["hash"]))
        if not correta:
            log.warning("Login recusado para %r vindo de %s", usuario, ip)
            time.sleep(1)  # atrasa tentativas de adivinhar senha
            raise ErroPedido("nao_autenticado", "usuario ou senha incorretos")
        token = secrets.token_hex(24)
        with self.trava:
            self.sessoes[token] = (usuario, registro["perfil"], time.time() + VALIDADE_TOKEN)
        log.info("Login de %s (%s) vindo de %s", usuario, registro["perfil"], ip)
        return {"token": token, "usuario": usuario, "perfil": registro["perfil"]}

    def acao_listar(self, pedido, ip):
        self._autenticar(pedido)
        filtro = pedido.get("filtro", "")
        if not isinstance(filtro, str) or len(filtro) > 60:
            raise ErroPedido("invalido", "filtro invalido")
        ramais = self.banco.listar(filtro.strip())
        agora = time.time()
        with self.trava:
            for ramal in ramais:
                ip_telefone = self._online(ramal["numero"], agora)
                ramal["online"] = ip_telefone is not None
                ramal["ip"] = ip_telefone
        return ramais

    def acao_criar(self, pedido, ip):
        usuario = self._exigir_admin(pedido)
        numero = validar_numero(pedido.get("numero"))
        nome = validar_texto(pedido.get("nome"), "nome", 2, 60)
        setor = validar_texto(pedido.get("setor"), "setor", 2, 40)
        self.banco.criar(numero, nome, setor)
        log.info("%s criou o ramal %s (%s, %s)", usuario, numero, nome, setor)
        return {"numero": numero, "nome": nome, "setor": setor}

    def acao_atualizar(self, pedido, ip):
        usuario = self._exigir_admin(pedido)
        numero = validar_numero(pedido.get("numero"))
        novo_numero = validar_numero(pedido.get("novo_numero", numero))
        nome = validar_texto(pedido.get("nome"), "nome", 2, 60)
        setor = validar_texto(pedido.get("setor"), "setor", 2, 40)
        self.banco.atualizar(numero, novo_numero, nome, setor)
        if novo_numero != numero:
            with self.trava:
                self.presenca.pop(numero, None)
        log.info("%s alterou o ramal %s -> %s (%s, %s)", usuario, numero, novo_numero, nome, setor)
        return {"numero": novo_numero, "nome": nome, "setor": setor}

    def acao_remover(self, pedido, ip):
        usuario = self._exigir_admin(pedido)
        numero = validar_numero(pedido.get("numero"))
        self.banco.remover(numero)
        with self.trava:
            self.presenca.pop(numero, None)
        log.info("%s removeu o ramal %s", usuario, numero)
        return {"numero": numero}

    def acao_estado(self, pedido, ip):
        """Numeros do proprio servidor, para monitoramento."""
        self._autenticar(pedido)
        agora = time.time()
        with self.trava:
            online = sum(1 for numero in self.presenca if self._online(numero, agora))
            conexoes, heartbeats = self.conexoes, self.heartbeats
        estado = {
            "ramais": len(self.banco.listar()),
            "online": online,
            "conexoes_tcp": conexoes,
            "heartbeats_udp": heartbeats,
            "threads": threading.active_count(),
            "pid": os.getpid(),
            "ativo_ha_s": int(agora - self.inicio),
        }
        try:
            import resource  # so existe no Linux/Unix
            estado["memoria_kb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        except ImportError:
            pass
        return estado

    def acao_sair(self, pedido, ip):
        with self.trava:
            self.sessoes.pop(pedido.get("token"), None)
        return None

    def processar(self, linha, ip):
        """Recebe uma linha crua da rede e devolve o dicionario de resposta."""
        try:
            pedido = json.loads(linha.decode("utf-8"))
            if not isinstance(pedido, dict):
                raise ValueError("o pedido deve ser um objeto JSON")
            acao = pedido.get("acao")
            funcao = getattr(self, f"acao_{acao}", None) if isinstance(acao, str) else None
            if funcao is None:
                raise ErroPedido("invalido", f"acao desconhecida: {acao!r}")
            resposta = {"ok": True, "dados": funcao(pedido, ip)}
            if acao == "sair":
                resposta["fim"] = True  # avisa o Atendente para fechar a conexao
            return resposta
        except ErroPedido as erro:
            return {"ok": False, "codigo": erro.codigo, "erro": str(erro)}
        except ValueError as erro:  # JSON malformado ou texto que nao e UTF-8
            return {"ok": False, "codigo": "invalido", "erro": f"pedido malformado: {erro}"}
        except Exception:  # um erro inesperado nao pode derrubar o servidor
            log.exception("Erro interno ao processar pedido de %s", ip)
            return {"ok": False, "codigo": "erro_interno", "erro": "erro interno do servidor"}


# -------------------------------------------------------------------- rede TCP
class Atendente(socketserver.StreamRequestHandler):
    """Uma instancia (e uma thread) por cliente conectado."""

    timeout = TIMEOUT_CLIENTE  # aplicado ao socket do cliente

    def handle(self):
        servico = self.server.servico
        ip = self.client_address[0]
        with servico.trava:
            servico.conexoes += 1
        log.info("Cliente conectado: %s:%s", *self.client_address)
        try:
            while True:
                linha = self.rfile.readline(TAM_MAX_PEDIDO + 1)
                if not linha:
                    break  # o cliente fechou a conexao
                if len(linha) > TAM_MAX_PEDIDO:
                    self._responder({"ok": False, "codigo": "invalido", "erro": "pedido grande demais"})
                    break
                resposta = servico.processar(linha, ip)
                encerrar = resposta.pop("fim", False)
                self._responder(resposta)
                if encerrar:
                    break  # o cliente pediu para sair
        except socket.timeout:
            log.info("Cliente %s ficou inativo e foi desconectado", ip)
        except OSError as erro:  # conexao resetada, cabo fora etc.
            log.warning("Conexao com %s encerrada com erro: %s", ip, erro)
        finally:
            with servico.trava:
                servico.conexoes -= 1
            log.info("Cliente desconectado: %s:%s", *self.client_address)

    def _responder(self, resposta):
        self.wfile.write((json.dumps(resposta, ensure_ascii=False) + "\n").encode("utf-8"))


class ServidorTCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True  # permite reiniciar sem esperar a porta liberar
    daemon_threads = True       # threads de clientes nao impedem o encerramento


# -------------------------------------------------------------------- rede UDP
def escutar_heartbeats(servico, parar):
    """Thread unica que recebe os heartbeats dos telefones."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((HOST, PORTA_UDP))
        sock.settimeout(1)  # acorda a cada 1 s para ver se e hora de parar
        log.info("Escutando heartbeats em UDP %s:%s", HOST, PORTA_UDP)
        while not parar.is_set():
            try:
                dados, endereco = sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError as erro:
                log.warning("Erro no socket UDP: %s", erro)
                continue
            servico.registrar_heartbeat(dados, endereco[0])


# ------------------------------------------------------------------------ main
def configurar_logs():
    formato = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    arquivo = RotatingFileHandler(os.path.join(DIR_DADOS, "servidor.log"),
                                  maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    tela = logging.StreamHandler()
    for destino in (arquivo, tela):
        destino.setFormatter(formato)
    logging.basicConfig(level=logging.INFO, handlers=[arquivo, tela])


def main():
    # umask 077: tudo que o servidor criar (banco e logs) nasce com permissao
    # so para o proprio usuario do servico (rw-------).
    os.umask(0o077)
    os.makedirs(DIR_DADOS, mode=0o700, exist_ok=True)
    configurar_logs()

    banco = Banco(os.path.join(DIR_DADOS, "ramais.db"))
    # Os usuarios vem do ambiente (.env); as senhas nao ficam no codigo.
    for perfil, var_usuario, var_senha in (("admin", "ADMIN_USUARIO", "ADMIN_SENHA"),
                                           ("consulta", "CONSULTA_USUARIO", "CONSULTA_SENHA")):
        usuario, senha = os.environ.get(var_usuario, perfil), os.environ.get(var_senha)
        if senha:
            banco.gravar_usuario(usuario, senha, perfil)
        else:
            log.warning("%s nao definida: usuario '%s' nao foi criado/atualizado", var_senha, usuario)
    if os.environ.get("RAMAIS_EXEMPLOS", "1") == "1":
        banco.semear_exemplos()

    servico = Servico(banco)
    parar = threading.Event()
    # SIGTERM e o sinal que "docker stop" e "systemctl stop" enviam.
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    signal.signal(signal.SIGINT, lambda *_: parar.set())

    threading.Thread(target=escutar_heartbeats, args=(servico, parar),
                     name="udp-heartbeat", daemon=True).start()
    with ServidorTCP((HOST, PORTA_TCP), Atendente) as servidor:
        servidor.servico = servico
        threading.Thread(target=servidor.serve_forever, name="tcp-aceitar", daemon=True).start()
        log.info("Servidor de ramais no ar em TCP %s:%s (pid %s)", HOST, PORTA_TCP, os.getpid())
        while not parar.wait(1):
            pass
        log.info("Encerrando o servidor...")
        servidor.shutdown()


if __name__ == "__main__":
    main()
