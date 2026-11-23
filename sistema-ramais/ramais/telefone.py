"""Simulador de telefone (aparelho de ramal).

Em uma empresa real, cada telefone IP avisa a central de que esta ligado.
Aqui cada conteiner "telefone" faz isso mandando um heartbeat por UDP a cada
poucos segundos. Se o conteiner parar, o servidor deixa de receber e o ramal
aparece como offline no painel.

Executar:  RAMAL=1001 python -m ramais.telefone
"""

import json
import logging
import os
import signal
import socket
import threading

SERVIDOR = os.environ.get("RAMAIS_SERVIDOR", "127.0.0.1")
PORTA_UDP = int(os.environ.get("RAMAIS_PORTA_UDP", "5051"))
RAMAL = os.environ.get("RAMAL", "1001")
INTERVALO = float(os.environ.get("INTERVALO", "5"))

log = logging.getLogger("ramais.telefone")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    signal.signal(signal.SIGINT, lambda *_: parar.set())

    mensagem = json.dumps({"ramal": RAMAL}).encode("utf-8")
    falhando = False
    log.info("Telefone do ramal %s enviando heartbeat para %s:%s a cada %s s",
             RAMAL, SERVIDOR, PORTA_UDP, INTERVALO)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while not parar.is_set():
            try:
                # UDP nao tem conexao nem confirmacao: so envia e segue em frente.
                sock.sendto(mensagem, (SERVIDOR, PORTA_UDP))
                if falhando:
                    log.info("Rede voltou; heartbeats retomados")
                    falhando = False
            except OSError as erro:
                # Ex.: nome do servidor nao resolve (DNS) ou rede fora do ar.
                # O telefone nao desiste: tenta de novo no proximo ciclo.
                if not falhando:
                    log.warning("Nao foi possivel enviar o heartbeat: %s", erro)
                    falhando = True
            parar.wait(INTERVALO)
    log.info("Telefone do ramal %s desligado", RAMAL)


if __name__ == "__main__":
    main()
