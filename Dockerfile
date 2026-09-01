# SAMVEDNA backend.
#
# Vercel cannot host this service, and the reasons are worth stating rather than
# discovering: openSMILE is a native library, faster-whisper is CTranslate2 plus
# several hundred megabytes of weights, the console feed is a long-lived
# WebSocket, and the audit ledger needs a persistent database whose UNIQUE
# constraint provides the chain's concurrency control. A request-scoped
# serverless function breaks the last of those silently, which is the worst way
# for an accountability record to fail.
#
# So the backend runs as a container and the console is served from Vercel.

FROM python:3.11-slim

# libsndfile is what soundfile binds to; ffmpeg handles container formats other
# than WAV. openSMILE needs neither — it ships libSMILEapi.so inside its wheel.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY services/ ./services/
COPY scripts/ ./scripts/
COPY evidence/ ./evidence/
COPY Makefile pytest.ini README.md DECISIONS.md ./

# Never run as root. A service handling victim disclosures should not be one
# container escape away from the host.
RUN useradd --create-home --uid 10001 samvedna \
    && chown -R samvedna:samvedna /app
USER samvedna

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    HF_HOME=/home/samvedna/.cache/huggingface

EXPOSE 8000

# /health reports the readiness verdict as well as liveness, so an orchestrator
# restarting on failure and an operator asking "may this take live calls" read
# the same endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "uvicorn services.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
