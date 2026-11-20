# dcgen web app: web form + REST API (/api, /docs) + MCP (/mcp) in one container. No GPU, no LLM.
FROM python:3.12-slim
# Carlito is metric-compatible with Calibri: nicer PDF text (optional; falls back to DejaVu Sans)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-crosextra-carlito \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY dcgen ./dcgen
COPY examples ./examples
RUN pip install --no-cache-dir ".[bim,mcp,web]"
ENV DCGEN_HOME=/data/projects \
    DCGEN_EXAMPLES=/app/examples
# Set DCGEN_PUBLIC_URL=https://your-host so agents return clickable download links
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "dcgen.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
