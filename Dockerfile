FROM python:3.11-slim

# Environment configuration
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLM_MODE=cache_only \
    PORT=8000 \
    HOST=0.0.0.0

WORKDIR /app

# Non-root user setup
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 -m appuser

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application codebase and committed demo assets
COPY app/ app/
COPY data/ data/
COPY demo_cache/ demo_cache/
COPY static/ static/
COPY templates/ templates/

# Give ownership of working directory to non-root user
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# SQLite on container disk is reseeded from data/ and demo_cache/ on boot via lifespan.
# /healthz returns 200 OK.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
