FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# Dependency layer — rebuilt only when lockfile changes
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# Source
COPY src/ src/

EXPOSE 8080

CMD ["uv", "run", "filament-assistant"]
