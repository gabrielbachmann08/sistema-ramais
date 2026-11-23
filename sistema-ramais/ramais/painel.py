"""Painel web do sistema de ramais (protocolo de aplicacao: HTTP).

O painel NAO acessa o banco de dados. Ele e um servidor HTTP que recebe os
cliques do navegador e repassa cada pedido ao servidor central por TCP,
usando o mesmo protocolo do cliente de terminal:

    navegador --HTTP:8080--> painel --TCP:5050--> servidor de ramais

Cada requisicao HTTP e atendida em uma thread propria (ThreadingHTTPServer).

Executar:  python -m ramais.painel
"""

import json
import logging
import os
import signal
import threading
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from .protocolo import ClienteRamais, ErroConexao, ErroServidor

SERVIDOR = os.environ.get("RAMAIS_SERVIDOR", "127.0.0.1")
PORTA_TCP = int(os.environ.get("RAMAIS_PORTA_TCP", "5050"))
HOST = os.environ.get("PAINEL_HOST", "0.0.0.0")
PORTA_HTTP = int(os.environ.get("PAINEL_PORTA", "8080"))

TAM_MAX_CORPO = 4096
ARQUIVO_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "painel.html")

# Traducao do codigo de erro do servidor de ramais para o status HTTP.
STATUS_HTTP = {"invalido": 400, "nao_autenticado": 401, "proibido": 403,
               "nao_encontrado": 404, "conflito": 409}

log = logging.getLogger("ramais.painel")


class Painel(BaseHTTPRequestHandler):
    server_version = "PainelRamais/1.0"
    timeout = 15  # segundos; derruba navegadores que abrem conexao e nao enviam nada

    # ------------------------------------------------------------ utilitarios
    def log_message(self, formato, *args):
        # Manda o log de acesso do http.server para o logging padrao.
        log.info("%s %s", self.client_address[0], formato % args)

    def _enviar(self, status, corpo, tipo="application/json; charset=utf-8", cabecalhos=()):
        if not isinstance(corpo, bytes):
            corpo = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for nome, valor in cabecalhos:
            self.send_header(nome, valor)
        self.end_headers()
        self.wfile.write(corpo)

    def _token(self):
        """O token de sessao do servidor de ramais viaja em um cookie."""
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:  # cabecalho Cookie malformado = sem sessao
            return None
        return cookie["sessao"].value if "sessao" in cookie else None

    def _corpo_json(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        if not 0 < tamanho <= TAM_MAX_CORPO:
            raise ValueError("corpo ausente ou grande demais")
        dados = json.loads(self.rfile.read(tamanho).decode("utf-8"))
        if not isinstance(dados, dict):
            raise ValueError("o corpo deve ser um objeto JSON")
        return dados

    def _repassar(self, acao, cabecalhos=(), **campos):
        """Abre uma conexao TCP com o servidor de ramais, faz o pedido e
        devolve a resposta ao navegador ja convertida para HTTP."""
        try:
            with ClienteRamais(SERVIDOR, PORTA_TCP, timeout=4, tentativas=2,
                               espera=0.5, token=self._token()) as cliente:
                dados = cliente.pedir(acao, **campos)
            self._enviar(200, {"dados": dados}, cabecalhos=cabecalhos)
        except ErroServidor as erro:
            self._enviar(STATUS_HTTP.get(erro.codigo, 500), {"erro": str(erro)})
        except ErroConexao as erro:
            log.error("Servidor de ramais inacessivel: %s", erro)
            self._enviar(503, {"erro": "O servidor de ramais esta fora do ar. Tente novamente."})

    # ------------------------------------------------------------------ rotas
    def _rotear(self, metodo):
        url = urlsplit(self.path)
        partes = [unquote(p) for p in url.path.split("/") if p]
        try:
            if metodo == "GET" and not partes:
                with open(ARQUIVO_HTML, "rb") as arquivo:
                    return self._enviar(200, arquivo.read(), "text/html; charset=utf-8")
            if partes[:1] != ["api"]:
                return self._enviar(404, {"erro": "pagina nao encontrada"})
            rota = partes[1:]

            if metodo == "POST" and rota == ["login"]:
                return self._login(self._corpo_json())
            if metodo == "POST" and rota == ["logout"]:
                return self._repassar("sair", cabecalhos=[
                    ("Set-Cookie", "sessao=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")])
            if metodo == "GET" and rota == ["estado"]:
                return self._repassar("estado")
            if metodo == "GET" and rota == ["ramais"]:
                filtro = parse_qs(url.query).get("filtro", [""])[0]
                return self._repassar("listar", filtro=filtro)
            if metodo == "POST" and rota == ["ramais"]:
                corpo = self._corpo_json()
                return self._repassar("criar", numero=corpo.get("numero"),
                                      nome=corpo.get("nome"), setor=corpo.get("setor"))
            if metodo == "PUT" and len(rota) == 2 and rota[0] == "ramais":
                corpo = self._corpo_json()
                return self._repassar("atualizar", numero=rota[1],
                                      novo_numero=corpo.get("numero", rota[1]),
                                      nome=corpo.get("nome"), setor=corpo.get("setor"))
            if metodo == "DELETE" and len(rota) == 2 and rota[0] == "ramais":
                return self._repassar("remover", numero=rota[1])
            return self._enviar(404, {"erro": "rota nao encontrada"})
        except ValueError as erro:  # JSON invalido, Content-Length ruim etc.
            return self._enviar(400, {"erro": f"requisicao invalida: {erro}"})

    def _login(self, corpo):
        try:
            with ClienteRamais(SERVIDOR, PORTA_TCP, timeout=4, tentativas=2, espera=0.5) as cliente:
                dados = cliente.pedir("login", usuario=corpo.get("usuario"), senha=corpo.get("senha"))
        except ErroServidor as erro:
            return self._enviar(STATUS_HTTP.get(erro.codigo, 500), {"erro": str(erro)})
        except ErroConexao as erro:
            log.error("Servidor de ramais inacessivel: %s", erro)
            return self._enviar(503, {"erro": "O servidor de ramais esta fora do ar. Tente novamente."})
        # HttpOnly: o JavaScript da pagina nao consegue ler o token.
        # SameSite=Strict: outros sites nao conseguem usar a sessao (CSRF).
        cookie = f"sessao={dados['token']}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600"
        self._enviar(200, {"dados": {"usuario": dados["usuario"], "perfil": dados["perfil"]}},
                     cabecalhos=[("Set-Cookie", cookie)])

    def do_GET(self):
        self._rotear("GET")

    def do_POST(self):
        self._rotear("POST")

    def do_PUT(self):
        self._rotear("PUT")

    def do_DELETE(self):
        self._rotear("DELETE")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    parar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    signal.signal(signal.SIGINT, lambda *_: parar.set())
    with ThreadingHTTPServer((HOST, PORTA_HTTP), Painel) as servidor:
        servidor.daemon_threads = True
        threading.Thread(target=servidor.serve_forever, name="http-aceitar", daemon=True).start()
        log.info("Painel web em http://%s:%s (servidor de ramais: %s:%s)",
                 HOST, PORTA_HTTP, SERVIDOR, PORTA_TCP)
        while not parar.wait(1):
            pass
        log.info("Encerrando o painel...")
        servidor.shutdown()


if __name__ == "__main__":
    main()
