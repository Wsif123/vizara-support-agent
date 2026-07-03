FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python -c "
import sys; sys.path.insert(0, '.')
from src.ingest import ingest
ingest('vizara-studio')
print('--- chroma_db populated in image ---')
"

EXPOSE 8000

ENV PORT=8000

CMD ["python", "src/server.py"]
