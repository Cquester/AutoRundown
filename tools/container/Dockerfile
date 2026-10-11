# AutoRundown test box: the app's dependencies, Chromium for the browser
# checks, Node (tools/check_js.py) and Claude Code, so tests AND agents run
# inside a container that sees only a copy of the repo. Built and driven by
# tools/container/box.sh (in WSL); see the notes at the top of that file.
#
# Everything is installed at build time: at run time the container's
# outbound network is limited to Anthropic (init-firewall.sh), so nothing
# can be fetched then.
FROM python:3.13-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git iptables dnsutils sudo procps less \
        nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# the app and its test tools; requirements first so code changes keep this layer
COPY requirements.txt requirements-dev.txt requirements-extractor.txt /tmp/req/
RUN pip install --no-cache-dir -r /tmp/req/requirements.txt \
        -r /tmp/req/requirements-dev.txt -r /tmp/req/requirements-extractor.txt ruff \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw-browsers

RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

# an unprivileged user runs everything; it may only run the firewall as root
RUN useradd -m -s /bin/bash dev \
    && mkdir -p /work /home/dev/.claude \
    && chown dev:dev /work /home/dev/.claude \
    && echo "dev ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh" > /etc/sudoers.d/firewall \
    && chmod 0440 /etc/sudoers.d/firewall
COPY tools/container/init-firewall.sh tools/container/entrypoint.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/init-firewall.sh /usr/local/bin/entrypoint.sh

USER dev
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
