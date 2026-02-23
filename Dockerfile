FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Copy Poetry configuration files
COPY pyproject.toml poetry.lock* /app/

# Create an empty README.md if pyproject.toml requires it
RUN touch README.md

# Configure Poetry to not create a virtual environment
RUN poetry config virtualenvs.create false

# Install dependencies without installing the root package
RUN poetry install --no-interaction --no-ansi --no-root --only main

# Copy application source
COPY app.py shopping_list.py models.py measurements.py utils.py ./

# recipes/ is mounted at runtime via docker compose volume;
# create the directory so the app doesn't error if the volume is empty
RUN mkdir -p recipes

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]