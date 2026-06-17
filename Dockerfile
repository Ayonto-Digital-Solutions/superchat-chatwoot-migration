FROM python:3.12-slim

# poppler-utils provides pdftotext / pdftoppm (required by the PDF parser and
# the attachment preview renderer)
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Default: show help. Real commands are passed via `docker compose run`.
CMD ["python", "-c", "print('Use: docker compose run --rm app python -m src.extract | src.to_chatwoot')"]
