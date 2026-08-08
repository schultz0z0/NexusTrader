FROM mcr.microsoft.com/dotnet/runtime:8.0-bookworm-slim AS dotnet-runtime

FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /tmp/requirements.txt

FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PATH="/opt/venv/bin:$PATH" \
    DOTNET_ROOT="/usr/share/dotnet"

COPY --from=dotnet-runtime /usr/share/dotnet /usr/share/dotnet
RUN ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet

RUN apt-get update && apt-get install -y --no-install-recommends curl libicu76 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 nexus \
    && useradd --uid 10001 --gid nexus --create-home --shell /usr/sbin/nologin nexus

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=nexus:nexus . .
RUN mkdir -p /app/storage && chown nexus:nexus /app/storage

USER nexus

CMD ["python", "main.py"]
