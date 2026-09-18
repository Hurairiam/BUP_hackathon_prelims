# --------------------------------------------------------------------------- #
# GridWise Smart Campus Energy Optimization API
# Production image for FastAPI + PuLP (requires coinor-cbc for the default solver)
# --------------------------------------------------------------------------- #
FROM python:3.11-slim AS runtime

# Prevent Python from writing .pyc files and force unbuffered stdout/stderr.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install CBC solver (required by PuLP) and clean apt cache in the same layer.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        coinor-cbc \
        coinor-libcbc-dev \
 && rm -rf /var/lib/apt/lists/*

# Create a non-root user for runtime safety.
RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /app app

WORKDIR /app

# Copy requirements first to leverage Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application source.
COPY main.py schemas.py solver.py ./

# Drop privileges.
USER app

EXPOSE 8000

# Healthcheck hits /health which returns instantly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
