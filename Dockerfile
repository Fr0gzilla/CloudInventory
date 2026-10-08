FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN groupadd -r app && useradd -r -g app app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for Docker caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app /app/app
COPY collector /app/collector

# Dossier de la base SQLite (volume nommé) : à l'utilisateur non root, sinon la base ne peut pas être créée
RUN mkdir -p /app/data && chown app:app /app/data

# Switch to non-root user
USER app

# Production WSGI with gunicorn : une factory s'appelle « module:fonction() » (gunicorn n'a pas d'option --factory)
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:create_app()"]

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://127.0.0.1:5000/login || exit 1