FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg tesseract-ocr libimage-exiftool-perl fonts-dejavu-core libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pramaan ./pramaan
COPY scripts ./scripts
COPY tests ./tests
COPY models ./models

ENV PRAMAAN_DATA=/data PRAMAAN_MODELS=/app/models PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "pramaan.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
