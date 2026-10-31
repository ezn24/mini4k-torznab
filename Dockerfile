FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py credential_crypto.py credential_tool.py ./

EXPOSE 8787
CMD ["gunicorn", "--bind", "0.0.0.0:8787", "--workers", "1", "--threads", "4", "--timeout", "240", "--access-logfile", "-", "--error-logfile", "-", "--capture-output", "app:app"]

