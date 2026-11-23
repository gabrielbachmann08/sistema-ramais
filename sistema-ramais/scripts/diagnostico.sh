#!/bin/sh
# Diagnostico do ambiente Linux de um no do Sistema de Ramais.
# Roda DENTRO de um conteiner:
#
#   docker compose exec servidor sh scripts/diagnostico.sh
#   docker compose exec painel   sh scripts/diagnostico.sh

titulo() { printf '\n===== %s =====\n' "$1"; }

titulo "Quem sou eu (usuario do processo: deve ser 'ramais', nao root)"
id

titulo "Interfaces e enderecos IP (ip addr)"
ip -brief addr

titulo "Tabela de rotas e gateway (ip route)"
ip route

titulo "Portas em escuta TCP/UDP (ss -tulnp)"
ss -tulnp

titulo "Conexoes TCP estabelecidas agora (ss -tn)"
ss -tn state established

titulo "Resolucao de nomes pelo DNS interno do Docker (dig)"
for nome in servidor painel; do
    printf '%s -> %s\n' "$nome" "$(dig +short "$nome" | tr '\n' ' ')"
done

titulo "Alcance dos outros nos (ping)"
for destino in 172.28.0.1 servidor painel; do
    if ping -c 2 -W 2 "$destino" >/dev/null 2>&1; then
        echo "OK     $destino"
    else
        echo "FALHA  $destino"
    fi
done

titulo "Caminho ate o servidor (traceroute)"
traceroute -n -w 2 -q 1 servidor 2>&1 | head -n 5

titulo "Processos e threads do Python (ps)"
# nlwp = numero de threads do processo; rss = memoria residente em KB
ps -eo pid,user,nlwp,rss,pcpu,etime,args | grep -E 'PID|python' | grep -v grep

titulo "Uso de CPU e memoria (top)"
top -b -n 1 | head -n 12

if [ -d /dados ] && [ -r /dados ]; then
    titulo "Permissoes do banco e dos logs (ls -l /dados)"
    ls -la /dados
    titulo "Ultimas linhas do log do servidor"
    tail -n 15 /dados/servidor.log 2>/dev/null
fi
