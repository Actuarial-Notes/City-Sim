# CitySim — the full interactive app (FastAPI + solver + SPA).
#
# Unlike the static GitHub Pages demo, this image can build new twins and run
# new Monte-Carlo ensembles on demand, which is what the setup screen's
# location / data-mode / run-count controls are for.
#
#   docker build -t citysim .
#   docker run --rm -p 8000:8000 -v citysim-data:/data citysim
FROM python:3.11-slim

# uvloop/httptools ship wheels; no build toolchain needed
WORKDIR /app
COPY pyproject.toml README.md ./
COPY citysim ./citysim
RUN pip install --no-cache-dir .

# twins and results are written at runtime — mount a volume here to keep them
ENV CITYSIM_DATA_DIR=/data \
    PORT=8000 \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000
# one worker: the job manager and twin cache are in-process state
CMD ["sh", "-c", "uvicorn citysim.server.app:app --host 0.0.0.0 --port ${PORT} --workers 1"]
