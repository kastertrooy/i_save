FROM python:3.11-slim

WORKDIR /app

COPY services/browser_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir playwright
RUN playwright install chromium
RUN playwright install-deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fluxbox \
        novnc \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY services/ /app/services/
COPY shared/ /app/shared/
COPY alembic.ini /app/alembic.ini
RUN chmod +x /app/services/browser_service/entrypoint.sh

EXPOSE 8000 6080

CMD ["/app/services/browser_service/entrypoint.sh"]
