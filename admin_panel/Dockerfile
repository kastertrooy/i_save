FROM python:3.11-slim

WORKDIR /app

COPY admin_panel/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY admin_panel/ /app/admin_panel/
COPY shared/ /app/shared/
COPY alembic.ini /app/alembic.ini

CMD ["python", "admin_panel/main.py"]
