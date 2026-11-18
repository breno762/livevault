# Imagem única: build do front (React/Vite) + back end FastAPI servindo o front.

FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
# sem VITE_API_BASE_URL: o front chama /api no mesmo endereço
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=web /web/dist ./static
ENV STATIC_DIR=/srv/static
EXPOSE 8000
# Um único processo: monitor de canais e consumidor de eventos rodam como threads internas.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
