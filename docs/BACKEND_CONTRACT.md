# Contrato de integração — Backend LiveVault

O backend é a fonte da verdade para autenticação, canais, detecção de lives, autorização de mídia, metadados, gravações, aprovações e publicação.

## REST API esperada pelo frontend

Prefixo: `/api`

### Dashboard
`GET /dashboard`
```json
{
  "channels_total": 12,
  "channels_live": 3,
  "recordings_active": 2,
  "recordings_total": 48,
  "storage_used_bytes": 536870912000,
  "recent_recordings": []
}
```

### Canais
- `GET /channels`
- `POST /channels` body: `{"platform":"twitch","username":"canal"}`
- `PATCH /channels/{id}` body: `{"enabled":true}`
- `DELETE /channels/{id}`

Cada canal deve conter: `id`, `platform` (`twitch|youtube|kick`), `username`, `display_name`, `is_live`, `enabled`, `last_seen_at`.

### Gravações
- `GET /recordings`
- `GET /recordings/{id}`
- `POST /recordings/{id}/stop`
- `GET /recordings/{id}/download` (resposta/redirect autenticado)

Campos recomendados: `id`, `channel_id`, `channel_name`, `platform`, `title`, `status` (`queued|recording|finalizing|completed|failed|stopped`), `started_at`, `ended_at`, `duration_seconds`, `size_bytes`, `thumbnail_url`, `file_url`.

### Clipes e aprovação
- `GET /clips`
- `POST /clips`
- `PATCH /clips/{id}/approval` body: `{"status":"approved"|"rejected"}`

Nenhum conteúdo deve ser publicado sem aprovação explícita.

### Saúde
`GET /health` deve retornar `{"status":"ok"}`.

## Contrato Redis — fila de gravação

Nome da fila: `recordings:jobs` (Redis LIST; produtor usa LPUSH e worker usa BRPOP).

Payload JSON:
```json
{
  "job_id": "uuid",
  "recording_id": "uuid",
  "platform": "twitch",
  "channel_name": "nome-do-canal",
  "media_url": "https://host-permitido/stream.m3u8?token=temporario",
  "output_dir": "/recordings/{recording_id}",
  "segment_seconds": 30
}
```

`media_url` deve ser uma URL temporária obtida por integração autorizada. Não enviar credenciais OAuth ou tokens permanentes no payload.

O worker emite eventos JSON na lista `recordings:events` (LPUSH):
```json
{
  "job_id": "uuid",
  "recording_id": "uuid",
  "event": "recording.started|recording.progress|recording.completed|recording.failed|recording.stopped",
  "timestamp": "2026-01-01T12:00:00Z",
  "data": {}
}
```

O backend deve consumir `recordings:events`, atualizar o banco e notificar o frontend (polling, SSE ou WebSocket). Recomenda-se implementar ACK/retry e uma fila de falhas (DLQ) no backend antes de produção.

## Regras operacionais
1. Backend monitora Twitch, YouTube e Kick via adaptadores separados.
2. Backend detecta início/fim da live e cria/encerra a sessão.
3. Backend só publica job após validar que a captura é permitida e obter URL de mídia temporária.
4. Worker valida o payload, valida HTTPS e host permitido, executa FFmpeg e gera segmentos de 30 segundos.
5. Worker envia eventos; backend persiste status e metadados.
6. Segmentos devem ser enviados para MinIO (implementação de storage ainda pendente) e registrados no banco.
7. Backend evita jobs duplicados por `recording_id` e implementa cancelamento via chave Redis `recordings:stop:{job_id}`.
8. Backend controla autorização de acesso e não expõe URL assinada na resposta pública.

## Erros esperados
`400` payload inválido; `401` não autenticado; `403` sem permissão; `404` inexistente; `409` conflito/duplicidade; `422` validação; `500` erro interno.
