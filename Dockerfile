FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    patchelf \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# onnxruntime 1.16.x is the last version that runs without AVX-512 (free tier);
# its binary carries an executable-stack flag that Render refuses to load, so
# clear that flag on every native library before the app ever dlopens them.
RUN find /usr/local/lib -name "*.so" -exec patchelf --clear-execstack {} \; || true

COPY . .

RUN python -c "\
import sys; sys.path.insert(0, '.'); \
from src.ingest import ingest; \
ingest('vizara'); \
print('--- chroma_db populated in image ---')"

EXPOSE 8000

ENV PORT=8000

CMD ["python", "src/server.py"]