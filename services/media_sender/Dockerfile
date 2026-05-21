FROM python:3.11-slim

WORKDIR /app

COPY services/media_sender/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services /app/services
COPY shared /app/shared

ENV PYTHONPATH=/app

CMD ["python", "-m", "services.media_sender.main"]
