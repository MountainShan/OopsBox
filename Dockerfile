FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/oopsbox
ENV NODE_VERSION=22.14.0
ENV CLAUDE_CODE_NO_FLICKER=1

WORKDIR /oopsbox

# System packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev \
    nginx \
    tmux \
    supervisor \
    sudo \
    jq \
    sshpass \
    openssh-client \
    git \
    curl \
    wget \
    vim \
    openssl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (uid 1000 matches typical host user; home at /oopsbox)
# Ubuntu 24.04 base image ships an 'ubuntu' user at uid 1000 — remove it first
RUN userdel -r ubuntu 2>/dev/null || true && \
    useradd -u 1000 -d /oopsbox -s /bin/bash oopsbox && \
    echo 'oopsbox ALL=(root) NOPASSWD: /usr/sbin/nginx -s reload' \
      > /etc/sudoers.d/oopsbox && \
    chmod 440 /etc/sudoers.d/oopsbox

# Install ttyd
RUN curl -L -o /usr/local/bin/ttyd \
    https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 \
    && chmod +x /usr/local/bin/ttyd

# Install Node.js (for Claude Code)
RUN curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz \
    | tar -xz -C /usr/local --strip-components=1

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Copy application
COPY dashboard/ /oopsbox/dashboard/
COPY bin/ /oopsbox/bin/
COPY config/ /oopsbox/config/
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/nginx-ssl.conf /etc/nginx/nginx-ssl.conf
COPY docker/supervisord.conf /etc/supervisor/conf.d/oopsbox.conf
COPY docker/entrypoint.sh /oopsbox/entrypoint.sh

RUN chmod +x /oopsbox/bin/*.sh /oopsbox/entrypoint.sh
RUN mkdir -p /etc/nginx/conf.d && \
    touch /etc/nginx/conf.d/oopsbox-projects.conf && \
    chown oopsbox:oopsbox /etc/nginx/conf.d/oopsbox-projects.conf

# nginx: remove default config
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf

EXPOSE 80 443

ENTRYPOINT ["/oopsbox/entrypoint.sh"]
