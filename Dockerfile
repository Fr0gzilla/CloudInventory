FROM python:3.11-slim-bookworm

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt 'gunicorn>=23,<24'
COPY app/ app/
COPY collector/ collector/
COPY scripts/start.sh scripts/start.sh
RUN useradd --system --uid 10001 --create-home app && mkdir -p /app/data && chown -R 10001:10001 /app
USER 10001:10001
EXPOSE 5050
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5050/login', timeout=3)"]
ENTRYPOINT ["sh", "/app/scripts/start.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "1", "app:create_app()"]
