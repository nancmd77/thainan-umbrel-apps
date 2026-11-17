FROM ghcr.io/nightly-labs/openbot:v0.72.0@sha256:516e7cf80e606c8437b6f363c1eb7c399ca20a8cae157237cc0c941246c61db7

USER root

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        sudo \
        at-spi2-core \
        openbox \
        xdotool \
        x11-apps \
        scrot \
        python3-pil \
        python3-tk \
        zenity \
    && rm -rf /var/lib/apt/lists/* \
    && echo "openbot ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/openbot \
    && chmod 0440 /etc/sudoers.d/openbot \
    && visudo -c -f /etc/sudoers.d/openbot \
    && printf '%s\n' \
        'unset NO_AT_BRIDGE' \
        'export ACCESSIBILITY_ENABLED=1' \
        'export QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1' \
        > /etc/profile.d/a11y.sh \
    && chmod 0644 /etc/profile.d/a11y.sh \
    && sed -i '1i[ -f /etc/profile.d/a11y.sh ] && . /etc/profile.d/a11y.sh' /etc/bash.bashrc

ENV BASH_ENV=/etc/profile.d/a11y.sh
# agent-desktop (vercel-labs): computer use pela árvore AT-SPI; binário oficial da release, conferido por sha256
ARG AGENT_DESKTOP_VERSION=1.2.0
ARG AGENT_DESKTOP_SHA256=591ae7f4a38479dc045df97e27447c0c8b530441730ab6b990e27c813162af44
USER root
RUN set -eu; f=agent-desktop-v${AGENT_DESKTOP_VERSION}-x86_64-unknown-linux-gnu.tar.gz; \
    curl -fsSL -o /tmp/$f https://github.com/vercel-labs/agent-desktop/releases/download/v${AGENT_DESKTOP_VERSION}/$f; \
    echo "${AGENT_DESKTOP_SHA256}  /tmp/$f" | sha256sum -c -; \
    mkdir -p /usr/local/lib/agent-desktop; tar -xzf /tmp/$f -C /usr/local/lib/agent-desktop; rm -f /tmp/$f; \
    chmod 0755 /usr/local/lib/agent-desktop/*; \
    echo '[ -r /run/openbot/a11y.env ] && . /run/openbot/a11y.env' >> /etc/profile.d/a11y.sh
COPY agent-desktop /usr/local/bin/agent-desktop
RUN chmod 0755 /usr/local/bin/agent-desktop
COPY md /usr/local/bin/md
RUN chmod 0755 /usr/local/bin/md
COPY decide /usr/local/bin/decide
RUN chmod 0755 /usr/local/bin/decide


# omp (can1357/oh-my-pi): agente ACP do OpenBot (comando "omp acp"); binário oficial da release, conferido por sha256.
# O LiteLLM do Nonve é o provedor padrão: /usr/local/share/omp/models.yml e config.yml são copiados para
# ~/.omp/agent (HOME=/data) no primeiro uso, sem sobrescrever o que já existir. A chave (app-omp do LiteLLM)
# vem de /config/litellm-omp.key (600); nunca fica na imagem.
ARG OMP_VERSION=18.22.0
ARG OMP_SHA256=f706b27ba845834d8f8360705a2e9ba1534f3593fa24411d41358272de33642b
RUN set -eu; mkdir -p /usr/local/lib/omp; \
    curl -fsSL -o /usr/local/lib/omp/omp https://github.com/can1357/oh-my-pi/releases/download/v${OMP_VERSION}/omp-linux-x64; \
    echo "${OMP_SHA256}  /usr/local/lib/omp/omp" | sha256sum -c -; \
    chmod 0755 /usr/local/lib/omp/omp
COPY omp /usr/local/bin/omp
COPY omp-defaults/ /usr/local/share/omp/
RUN chmod 0755 /usr/local/bin/omp && chmod 0644 /usr/local/share/omp/*

# Node.js LTS + npm: o Registro ACP do OpenBot instala agentes via npm (ex.: dirac). Tarball oficial, conferido por sha256.
# Globais do npm vão pra /data/.npm-global (persistente, dono 1000), sem precisar de root.
ARG NODE_VERSION=26.13.1
ARG NODE_SHA256=518674de2b621728a5e4486f5fb3b7d2900bb3cc4aa97969edaa463db99f9778
RUN set -eu; f=node-v${NODE_VERSION}-linux-x64.tar.gz; \
    curl -fsSL -o /tmp/$f https://nodejs.org/dist/v${NODE_VERSION}/$f; \
    echo "${NODE_SHA256}  /tmp/$f" | sha256sum -c -; \
    tar -xzf /tmp/$f -C /usr/local --strip-components=1 --no-same-owner \
        --exclude=CHANGELOG.md --exclude=LICENSE --exclude=README.md; \
    rm -f /tmp/$f; node --version; npm --version; npx --version
ENV NPM_CONFIG_PREFIX=/data/.npm-global \
    PATH=/data/.npm-global/bin:$PATH
RUN printf '%s\n' 'export NPM_CONFIG_PREFIX=/data/.npm-global' 'case ":$PATH:" in *:/data/.npm-global/bin:*) ;; *) export PATH=/data/.npm-global/bin:$PATH;; esac' > /etc/profile.d/npm-global.sh \
    && chmod 0644 /etc/profile.d/npm-global.sh

USER 1000
