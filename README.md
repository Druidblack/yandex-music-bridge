# Yandex Music Bridge

Standalone bridge for Yandex Music now playing.

An easy way to get a token. You can get a token for Yandex using https://chromewebstore.google.com/detail/yandex-music-token/lcbjeookjibfhjjopieifgjnhlegmkib

<img width="516" height="357" alt="image" src="https://github.com/user-attachments/assets/de9111ee-3ca4-4ffa-80d7-f1dadc1c5043" />

Copy the token (Скопировать токен)

All available ways to get a token https://yandex-music.readthedocs.io/en/main/token.html

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

```
services:
  yandex-music-bridge:
    image: ghcr.io/druidblack/yandex-music-bridge:latest
    container_name: yandex-music-bridge
    environment:
      - TZ=Europe/Moscow
      - YM_TOKEN=AgAAAAACO3_345345
      - YM_API_KEY=change-me
      - YM_PORT=9980
      - YM_LANGUAGE=ru
      - YM_ENABLE_YNISON=true
      - YM_PUSH_TTL=45
      - YM_QUEUE_CACHE_TTL=15
      - YM_LOG_LEVEL=INFO
    ports:
      - 9980:9980
    restart: unless-stopped
```
