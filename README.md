
# hardware-pulse

This repo is designed to run fully on **Android Termux** (backend + Redis + crawler) without Docker.

Monorepo layout:
- `hardware-pulse-backend/`: Maven + Spring Boot backend service.
- `hardware-pulse-crawler/`: Python crawler service (uiautomator2 based Xianyu spider).

## Termux Quick Start

1) One-time environment setup (Termux)

```bash
./setup_env.sh
```

2) Configure crawler

Edit `hardware-pulse-crawler/config.yml`:
- `backend.ingest_url` default: `http://127.0.0.1:8080/api/pulse/raw`
- `redis.host/port` default: `127.0.0.1:6379`
- `device.adb` default: `127.0.0.1:5555`

3) Start everything

```bash
./start_all.sh
```

4) Stop (only backend + crawler)

```bash
./stop_all.sh
```

## Notes

- `start_all.sh` does not stop middleware; it only starts/checks Redis/PG, keeps atx-agent/uiautomator2 alive, and starts backend + crawler.
- `stop_all.sh` only stops backend + crawler.
- The crawler does **not** read environment variables. Configuration is loaded from `hardware-pulse-crawler/config.yml`.
