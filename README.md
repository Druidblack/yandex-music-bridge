# Yandex Music Bridge

Получаем информацию о статусе воспроизведения Яндекс Музыки в браузере, от умных колонок, подключенных к той же локальной сети где работает программа и из приложения Яндекс музыка для ПК.

Это нужно для отправки информации в https://github.com/FoxxMD/multi-scrobbler

Для получения информации о воспроизведении из браузера нам нужен токен. Простой способ получения токена https://chromewebstore.google.com/detail/yandex-music-token/lcbjeookjibfhjjopieifgjnhlegmkib

<img width="516" height="357" alt="image" src="https://github.com/user-attachments/assets/de9111ee-3ca4-4ffa-80d7-f1dadc1c5043" />

Скопировать токен

Все способы получения токена https://yandex-music.readthedocs.io/en/main/token.html

Информация о воспроизведении не получается из **Яндекс Навигатора**.

YM_STEREO_GROUPS=L015A0B0076GRV|L11ZCV000SV8AV - если у вас есть стереопара, что бы не дублировалось воспроизведение.

YM_ZEROCONF_INTERFACES=192.168.1.161 - адрес хоста

YM_DESKTOP_PLAYERS - адрес и порт пк на котормо запускается приложение Яндекс музыка

Инструкция как подключить приложения Яндекс Музыка https://github.com/Druidblack/yandex-music-desktop-home-assistant

```
services:
  yandex-music-bridge:
    image: ghcr.io/druidblack/yandex-music-bridge:latest
    container_name: yandex-music-bridge
    environment:
      - YM_TOKEN=put-your-yandex-music-token-here
      - YM_API_KEY=change-me
      - YM_PORT=9980
      - YM_ENABLE_MUSIC=true
      - YM_ENABLE_YNISON=true
      - YM_ENABLE_STATIONS=true
      - YM_QUEUE_CACHE_TTL=3
      - YM_PLAYER_TTL=180
      - YM_STEREO_GROUPS=L015A0B0076GRV|L11ZCV000SV8AV
      - YM_ZEROCONF_INTERFACES=192.168.1.161
      - YM_RESTART_INTERVAL_SECONDS=3600  # 0 - disable
      - YM_DESKTOP_PLAYERS=Основной ПК@192.168.1.95:19222,Ноутбук@192.168.1.96:19222
      - YM_DESKTOP_POLL_INTERVAL=20
      - YM_DESKTOP_RETRY_INTERVAL=10
      - YM_MULTISCROBBLER_PAUSE_COMPAT=true
    network_mode: host
    restart: unless-stopped
```
