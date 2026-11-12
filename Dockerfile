
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ ./src/
COPY configs/ ./configs/

RUN pip install --no-cache-dir .

COPY tests/ ./tests/

ENV CYPHER_CONFIG=offline

CMD ["python", "-m", "pipeline.main"]
