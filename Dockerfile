FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

# ── s6-overlay (process supervisor) ──
ARG S6_OVERLAY_VERSION=3.2.0.2
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz /tmp
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz /tmp
RUN tar -C / -Jxpf /tmp/s6-overlay-noarch.tar.xz && \
    tar -C / -Jxpf /tmp/s6-overlay-x86_64.tar.xz && \
    rm /tmp/s6-overlay-*.tar.xz

# ── System packages ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux ttyd nginx jq python3-pip python3-venv \
    build-essential procps sshpass git curl openssl sudo ca-certificates sshfs \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js (for Claude CLI) ──
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# ── Claude CLI ──
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

# ── Create user with home at /oopsbox ──
RUN useradd -m -s /bin/bash -d /oopsbox oopsbox && \
    echo "oopsbox ALL=(ALL) NOPASSWD: /usr/sbin/nginx, /usr/bin/tee" >> /etc/sudoers.d/oopsbox

# ── Dashboard (Python app) ──
RUN mkdir -p /opt/dashboard/static && chown -R oopsbox:oopsbox /opt/dashboard
USER oopsbox
RUN python3 -m venv /opt/dashboard/venv && \
    /opt/dashboard/venv/bin/pip install --no-cache-dir -q \
    fastapi "uvicorn[standard]" aiofiles paramiko python-multipart
USER root

COPY dashboard/main.py /opt/dashboard/
COPY dashboard/static/ /opt/dashboard/static/

# ── Scripts ──
COPY bin/ /oopsbox/bin/
RUN chmod +x /oopsbox/bin/*

# ── Configs ──
COPY config/tmux.conf /oopsbox/.tmux.conf
COPY config/ttyd-theme.conf /oopsbox/.config/ttyd-theme.conf
COPY config/statusline-command.sh /oopsbox/.claude/statusline-command.sh
RUN chmod +x /oopsbox/.claude/statusline-command.sh

# ── Persistent directories (volume mount points) ──
RUN mkdir -p /oopsbox/projects /oopsbox/.config/oopsbox \
    /oopsbox/.claude /oopsbox/channels && \
    chown -R oopsbox:oopsbox /oopsbox

VOLUME ["/oopsbox/projects", "/oopsbox/.config/oopsbox", "/oopsbox/.claude", "/oopsbox/channels"]

# ── nginx config ──
COPY docker/nginx.conf /etc/nginx/sites-available/oopsbox
RUN rm -f /etc/nginx/sites-enabled/default && \
    ln -sf /etc/nginx/sites-available/oopsbox /etc/nginx/sites-enabled/oopsbox && \
    printf '# no projects\nset $code_port 8100;\nset $ttyd_port 9100;\n' > /etc/nginx/rcoder-ports.conf

# ── s6-overlay services ──
COPY docker/s6-rc.d/ /etc/s6-overlay/s6-rc.d/

# ── Entrypoint ──
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── PATH + HOME ──
ENV HOME=/oopsbox
ENV PATH="/oopsbox/bin:/opt/dashboard/venv/bin:${PATH}"

EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]
