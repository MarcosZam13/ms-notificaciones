# ================================================================
# Dockerfile para Azure App Services
# Microservicio de Notificaciones - Python 3.11 + FastAPI
# ================================================================

# --- Etapa de construcción ---
FROM python:3.11-slim AS builder

# Instalar dependencias del sistema necesarias
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make && \
    rm -rf /var/lib/apt/lists/*

# Crear entorno virtual
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --- Etapa final ---
FROM python:3.11-slim

# Variables de entorno recomendadas por Azure App Services
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# Crear usuario no-root para ejecutar la aplicación (seguridad)
RUN groupadd --system app && \
    useradd --system --gid app --create-home app

WORKDIR /home/app

# Copiar el entorno virtual desde la etapa de construcción
COPY --from=builder /opt/venv /opt/venv

# Copiar el código de la aplicación
COPY app/ ./app/

# Copiar archivos de configuración
COPY .env.example .env.example

# El archivo de credenciales de Firebase se monta como secreto en Azure
# o se inyecta como variable de entorno. No se copia en la imagen.

# Cambiar a usuario no-root
USER app

# Exponer el puerto (Azure App Services usa PORT env var)
EXPOSE 8000

# Health check para Azure
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

# Comando de arranque
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
