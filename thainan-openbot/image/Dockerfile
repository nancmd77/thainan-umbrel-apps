FROM ghcr.io/nightly-labs/openbot:v0.61.0@sha256:7eae38c2354f770bbf924657a908576e23c9bd3387efbf8e7fd52c059fe51a42

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
COPY md /usr/local/bin/md
RUN chmod 0755 /usr/local/bin/md
COPY decide /usr/local/bin/decide
RUN chmod 0755 /usr/local/bin/decide


USER 1000
