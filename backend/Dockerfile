FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
# Um único processo: o monitor de canais e o consumidor de eventos rodam como threads internas.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
