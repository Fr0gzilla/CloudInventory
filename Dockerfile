FROM python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY app/ app/
COPY collector/ collector/
COPY scripts/start.sh scripts/start.sh
RUN useradd --system --uid 10001 --create-home app && mkdir -p /app/data && chown -R 10001:10001 /app
USER 10001:10001
EXPOSE 5050
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5050/login', timeout=3)"]
ENTRYPOINT ["sh", "/app/scripts/start.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "1", "app:create_app()"]
