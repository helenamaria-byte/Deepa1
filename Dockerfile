# Local/self-hosted alternative to Vercel: dashboard + scheduled runs in one container.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt uvicorn
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
