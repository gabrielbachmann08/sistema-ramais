# Uma unica imagem serve para todos os nos (servidor, painel, telefone e
# cliente); o que muda e o comando definido no docker-compose.yml.
FROM python:3.12-slim

# Ferramentas de diagnostico de rede e de processos usadas no monitoramento:
# ip/ss (iproute2), ping, traceroute, dig (dnsutils), tcpdump, top/ps (procps).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      iproute2 iputils-ping traceroute dnsutils tcpdump procps \
 && rm -rf /var/lib/apt/lists/*

# Minimo privilegio: o sistema roda com um usuario comum, nao como root.
RUN useradd --create-home --uid 1000 ramais \
 && mkdir /dados && chown ramais:ramais /dados && chmod 700 /dados

WORKDIR /app
COPY ramais/ ramais/
COPY testes/ testes/
COPY scripts/ scripts/

# PYTHONUNBUFFERED faz os logs aparecerem na hora em "docker compose logs".
ENV PYTHONUNBUFFERED=1 RAMAIS_DIR_DADOS=/dados
USER ramais
CMD ["python", "-m", "ramais.servidor"]
