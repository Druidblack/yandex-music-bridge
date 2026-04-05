# Yandex Music Bridge

Standalone bridge for Yandex Music now playing.

## Endpoints
- `GET /health`
- `GET /now-playing`

## Env
- `YM_TOKEN` (required)
- `YM_API_KEY` (optional, protects local endpoints)
- `YM_PORT` (default `9980`)
- `YM_LANGUAGE` (default `ru`)
- `YM_ENABLE_YNISON` (default `true`)
- `YM_PUSH_TTL` (default `45`)
- `YM_QUEUE_CACHE_TTL` (default `15`)
- `YM_LOG_LEVEL` (default `INFO`)
