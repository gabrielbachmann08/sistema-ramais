"""Cliente de terminal para gerenciar os ramais.

Fala diretamente com o servidor por TCP (porta 5050), sem passar pelo painel
web. Se o servidor cair no meio do uso, o cliente mostra o erro, espera e
tenta reconectar sozinho.

Executar:  python -m ramais.cliente
"""

import getpass
import logging
import os

from .protocolo import ClienteRamais, ErroConexao, ErroServidor

SERVIDOR = os.environ.get("RAMAIS_SERVIDOR", "127.0.0.1")
PORTA_TCP = int(os.environ.get("RAMAIS_PORTA_TCP", "5050"))

MENU = """
1) Listar ramais      2) Buscar
3) Novo ramal         4) Alterar ramal
5) Remover ramal      6) Estado do servidor
0) Sair"""


def mostrar(ramais):
    if not ramais:
        print("Nenhum ramal encontrado.")
        return
    print(f"{'RAMAL':<7}{'NOME':<28}{'SETOR':<20}SITUACAO")
    for r in ramais:
        situacao = f"online ({r['ip']})" if r["online"] else "offline"
        print(f"{r['numero']:<7}{r['nome']:<28}{r['setor']:<20}{situacao}")


def login(cliente):
    while True:
        usuario = input("Usuario: ").strip()
        senha = getpass.getpass("Senha: ")
        try:
            dados = cliente.pedir("login", usuario=usuario, senha=senha)
            print(f"Bem-vindo, {dados['usuario']} (perfil: {dados['perfil']}).")
            return
        except ErroServidor as erro:
            print(f"Erro: {erro}")


def executar(cliente, opcao):
    if opcao == "1":
        mostrar(cliente.pedir("listar"))
    elif opcao == "2":
        mostrar(cliente.pedir("listar", filtro=input("Buscar por numero, nome ou setor: ")))
    elif opcao == "3":
        dados = cliente.pedir("criar", numero=input("Numero: ").strip(),
                              nome=input("Nome: "), setor=input("Setor: "))
        print(f"Ramal {dados['numero']} criado.")
    elif opcao == "4":
        numero = input("Numero do ramal a alterar: ").strip()
        novo = input(f"Novo numero [{numero}]: ").strip() or numero
        dados = cliente.pedir("atualizar", numero=numero, novo_numero=novo,
                              nome=input("Novo nome: "), setor=input("Novo setor: "))
        print(f"Ramal {dados['numero']} atualizado.")
    elif opcao == "5":
        numero = input("Numero do ramal a remover: ").strip()
        if input(f"Remover o ramal {numero}? (s/n) ").strip().lower() == "s":
            cliente.pedir("remover", numero=numero)
            print("Ramal removido.")
    elif opcao == "6":
        for chave, valor in cliente.pedir("estado").items():
            print(f"  {chave}: {valor}")
    else:
        print("Opcao invalida.")


def main():
    # Mostra na tela os avisos de reconexao gerados pela classe ClienteRamais.
    logging.basicConfig(level=logging.WARNING, format="[aviso] %(message)s")
    print(f"Sistema de Ramais - conectando a {SERVIDOR}:{PORTA_TCP}")
    with ClienteRamais(SERVIDOR, PORTA_TCP, timeout=5, tentativas=5, espera=2) as cliente:
        try:
            login(cliente)
            while True:
                print(MENU)
                opcao = input("> ").strip()
                if opcao == "0":
                    cliente.pedir("sair")
                    break
                try:
                    executar(cliente, opcao)
                except ErroServidor as erro:
                    if erro.codigo == "nao_autenticado":
                        # Sessao expirou ou o servidor foi reiniciado.
                        print("Sua sessao expirou. Entre novamente.")
                        login(cliente)
                    else:
                        print(f"Erro: {erro}")
                except ErroConexao as erro:
                    print(f"Sem comunicacao com o servidor: {erro}")
                    print("Tente a operacao novamente em instantes.")
        except ErroConexao as erro:
            print(f"Nao foi possivel conectar: {erro}")
        except (KeyboardInterrupt, EOFError):
            print("\nEncerrando.")


if __name__ == "__main__":
    main()
