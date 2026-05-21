FROM python:3.11-slim

WORKDIR /app

COPY services/media_downloader/requirements.txt ./requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

COPY services /app/services
COPY shared /app/shared

ENV PYTHONPATH=/app

CMD ["python", "-m", "services.media_downloader.main"]
