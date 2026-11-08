#!/usr/bin/env python3
"""Gera docker-compose.yml embutindo src/economia.py no command do serviço economia.

Uso: python3 tools/gerar_compose.py          (grava docker-compose.yml)
     python3 tools/gerar_compose.py --check  (falha se o compose estiver fora de sincronia)
"""
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
IMAGEM = 'python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4'

CABECALHO = '''version: "3.7"
services:
  app_proxy:
    environment:
      APP_HOST: 10.21.0.1
      APP_PORT: 8536
  economia:
    image: @IMAGEM@
    restart: unless-stopped
    privileged: true
    pid: host
    network_mode: host
    environment:
      NIC: enp64s0
      BIND: 10.21.0.1
      CPU_W: fabrica
      TURBO: "on"
      RTX: "on"
    volumes:
      - /lib/modules:/lib/modules:ro
      - /usr/bin:/host/bin:ro
      - /usr/lib/x86_64-linux-gnu:/host/lib:ro
      - ${APP_DATA_DIR}/data:/data
    entrypoint: ["python", "-u", "-c"]
    command:
      - |
'''


def gerar():
    src = (RAIZ / 'src' / 'economia.py').read_text(encoding='utf-8')
    corpo = '\n'.join(('        ' + linha) if linha.strip() else '' for linha in src.rstrip('\n').split('\n'))
    return CABECALHO.replace('@IMAGEM@', IMAGEM) + corpo + '\n'


def main():
    texto = gerar()
    alvo = RAIZ / 'docker-compose.yml'
    if '--check' in sys.argv:
        atual = alvo.read_text(encoding='utf-8') if alvo.exists() else ''
        if atual != texto:
            print('docker-compose.yml fora de sincronia com src/economia.py; rode tools/gerar_compose.py', file=sys.stderr)
            return 1
        print('docker-compose.yml sincronizado')
        return 0
    alvo.write_text(texto, encoding='utf-8')
    print('gravado %s (%d linhas)' % (alvo, texto.count('\n')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
