FROM python:3.15-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY gunicorn.conf.py .

EXPOSE 8000

CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
