"""Teste de clientes simultaneos.

Abre N clientes ao mesmo tempo (uma thread e uma conexao TCP para cada um),
cada cliente faz login e depois M listagens. No fim mostra quantos pedidos
deram certo e os tempos de resposta. Serve de evidencia de que o servidor
atende varios clientes sem bloquear.

Executar no ambiente Docker:
    docker compose run --rm cliente python -m testes.teste_carga --clientes 10

A senha vem da variavel TESTE_SENHA ou e perguntada no terminal.
"""

import argparse
import getpass
import os
import statistics
import threading
import time

from ramais.protocolo import ClienteRamais, ErroConexao, ErroServidor


def um_cliente(indice, args, senha, tempos, erros, trava, largada):
    largada.wait()  # todos os clientes partem juntos
    try:
        with ClienteRamais(args.host, args.porta, timeout=10, tentativas=1) as cliente:
            cliente.pedir("login", usuario=args.usuario, senha=senha)
            for _ in range(args.pedidos):
                inicio = time.perf_counter()
                cliente.pedir("listar")
                duracao_ms = (time.perf_counter() - inicio) * 1000
                with trava:
                    tempos.append(duracao_ms)
    except (ErroConexao, ErroServidor) as erro:
        with trava:
            erros.append(f"cliente {indice}: {erro}")


def main():
    parser = argparse.ArgumentParser(description="Teste de clientes simultaneos")
    parser.add_argument("--host", default=os.environ.get("RAMAIS_SERVIDOR", "127.0.0.1"))
    parser.add_argument("--porta", type=int, default=int(os.environ.get("RAMAIS_PORTA_TCP", "5050")))
    parser.add_argument("--usuario", default="consulta")
    parser.add_argument("--clientes", type=int, default=10)
    parser.add_argument("--pedidos", type=int, default=20, help="listagens por cliente")
    args = parser.parse_args()
    senha = os.environ.get("TESTE_SENHA") or getpass.getpass(f"Senha de {args.usuario}: ")

    tempos, erros = [], []
    trava, largada = threading.Lock(), threading.Event()
    threads = [threading.Thread(target=um_cliente,
                                args=(i, args, senha, tempos, erros, trava, largada))
               for i in range(1, args.clientes + 1)]
    for thread in threads:
        thread.start()
    inicio = time.perf_counter()
    largada.set()
    for thread in threads:
        thread.join()
    total = time.perf_counter() - inicio

    esperado = args.clientes * args.pedidos
    print(f"Clientes simultaneos : {args.clientes}")
    print(f"Pedidos atendidos    : {len(tempos)} de {esperado}")
    print(f"Tempo total          : {total:.2f} s")
    if tempos:
        print(f"Tempo de resposta    : medio {statistics.mean(tempos):.1f} ms | "
              f"minimo {min(tempos):.1f} ms | maximo {max(tempos):.1f} ms")
    for erro in erros:
        print(f"ERRO {erro}")
    raise SystemExit(0 if len(tempos) == esperado else 1)


if __name__ == "__main__":
    main()
