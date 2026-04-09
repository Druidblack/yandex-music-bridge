# Yandex Music Bridge

We receive the Yandex music playback status as well as from smart speakers that are on the same local network as the program being launched.

it is necessary for https://github.com/FoxxMD/multi-scrobbler

An easy way to get a token. You can get a token for Yandex using https://chromewebstore.google.com/detail/yandex-music-token/lcbjeookjibfhjjopieifgjnhlegmkib

<img width="516" height="357" alt="image" src="https://github.com/user-attachments/assets/de9111ee-3ca4-4ffa-80d7-f1dadc1c5043" />

Copy the token (Скопировать токен)

All available ways to get a token https://yandex-music.readthedocs.io/en/main/token.html

The playback status cannot be obtained from the **Yandex Navigator**.

YM_STEREO_GROUPS=L015A0B0076GRV|L11ZCV000SV8AV - if there is a stereo pair

YM_ZEROCONF_INTERFACES=192.168.1.161 - host address

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
    network_mode: host
    restart: unless-stopped
```
