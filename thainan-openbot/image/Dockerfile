FROM ghcr.io/nightly-labs/openbot:v0.70.0@sha256:1a1687ca5d16a003def143cc8059467abd3d9fbb4cc57768a03b33f6f3f4a936

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


USER 1000
