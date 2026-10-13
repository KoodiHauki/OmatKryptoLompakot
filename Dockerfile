FROM python:3.13-slim

WORKDIR /app

# Asennetaan tarvittavat järjestelmäriippuvuudet
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Kopioidaan projektimääritykset ja asennetaan riippuvuudet
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Kopioidaan lähdekoodi ja konfiguraatiot
COPY config.yaml .
COPY run.py .
COPY src/ src/

# Luodaan oletushakemisto SQLite-tietokannalle
RUN mkdir -p /root/.local/share/omatkryptolompakot

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["python", "run.py"]
