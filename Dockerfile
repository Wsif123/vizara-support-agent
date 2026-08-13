FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    execstack \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Render refuses to load shared objects that request an executable stack
# (e.g. onnxruntime wheels) -> clear the PT_GNU_STACK exec flag on all .so files
RUN find /usr/local/lib -name "*.so" -exec execstack -c {} \; || true

COPY . .

EXPOSE 8000

ENV PORT=8000

CMD ["python", "src/server.py"]
