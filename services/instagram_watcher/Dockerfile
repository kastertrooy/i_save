FROM python:3.11-slim

WORKDIR /app

COPY services/instagram_watcher/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium
RUN apt-get update \
    && apt-get install -y --no-install-recommends fluxbox novnc websockify x11vnc \
    && rm -rf /var/lib/apt/lists/*

COPY services /app/services
COPY shared /app/shared

COPY services/instagram_watcher/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
