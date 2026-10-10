# HardwarePulse

A Termux deployment for tracking second-hand hardware prices on Xianyu (闲鱼) from an Android phone, without root and without Docker: a Rust HTTP/ingest edge with a SQLite keyword scheduler, and a Python `uiautomator2` crawler that drives the real Xianyu app.

The repository also contains a Spring Boot backend (LLM ETL into PostgreSQL, Redis ZSET scheduler, TimescaleDB-optional DDL). See [Two halves](#two-halves) — the lifecycle scripts start only the edge and the crawler.

## Components

### Rust edge — `hardware-pulse-edge/`

`src/main.rs` (680 lines), edition 2021, `axum` 0.7 + `rusqlite` (bundled). Binds `127.0.0.1:8080` and serves four routes:

| Route | Behaviour |
|---|---|
| `GET /health` | `{ok, ts}` |
| `POST /api/spider/seed` | Insert keywords at an initial score (default 50) |
| `GET /api/spider/next` | Lease the highest-scored keyword whose `inflight_until` has passed; returns `{keyword, lease_seconds}` or 204 when idle |
| `POST /api/pulse/raw` | Truncate the batch to `--max-items` (default 200), store each listing, mark it `PENDING` in `etl_queue`, and adjust the keyword's score by items returned |

- SQLite runs in WAL with a 20 MB cache, `synchronous=NORMAL` and a 500 ms busy timeout. Tables: `tasks`, `raw_listings`, `etl_queue`, `standard_sku`, `price_history`.
- A built-in ETL loop ticks every 700 ms: it claims up to 20 `PENDING` rows for 30 s, computes a `sku_key` with `normalize_sku_key` (lowercased ASCII alphanumerics, spaces to underscores, 96 chars max), upserts `standard_sku` and inserts a `price_history` row. Failures go back to `PENDING` with the error text and a retry count. This normalisation is deterministic string work, not an LLM call.
- `memwatch` samples `/proc/meminfo` and its own `VmRSS` (plus the crawler's, read from a PID file) every second, buffers 10 samples, then flushes to `logs/mem_watch.log` and trims the file to 5 MB.
- `--bind`, `--db-path`, `--max-items`, `--lease-seconds` (default 180), `--memwatch-log`, `--memwatch-interval-ms`, `--enable-etl`, `--pid-file`, `--crawler-pid-file` are all CLI flags; there is no config file.

### Python crawler — `hardware-pulse-crawler/crawler_wg_xianyu.py`

1,648 lines. Requires `uiautomator2` and an `atx-agent` on the device; connects over ADB at `127.0.0.1:5555`.

- No `dump_hierarchy()`, no XML parsing, no blind `time.sleep()`. It reads visible node text and bounds and polls explicit waits (`wait_home`, `wait_results_ready`, `wait_detail_ready`, `_swipe_and_wait_list_change`).
- Keywords are leased over HTTP from the edge (`GET /api/spider/next`); result batches are posted to `POST /api/pulse/raw`. Redis is not used by this entry point.
- Two-stage scrape: scan the visible cards, then open a detail page for the full description, shipping origin and Zhima credit.
- In-memory MD5 dedup keyed on title plus price text; the keyword is abandoned after 3 consecutive swipes with no new items, or when the end-of-list marker appears.
- Payload caps keep memory flat: description 800 chars, UI snapshot 2,000 chars, detail dump 1,200 chars.
- `config.yml` holds the ADB address, the ingest URL, debug flags, and a `thresholds` map of keyword to price ceiling (the values shipped are `9999.0` placeholders).
- `main.py` and `crawler/spiders/xianyu.py` are a legacy Playwright spider against `goofish.com` that pushes to a Redis list. It is not what `start_all.sh` runs.

### Spring Boot backend — `hardware-pulse-backend/`

Java 17, Spring Boot 3.2.5, 30 source files. Web + JPA + Redis starters, PostgreSQL driver.

- `ListingConsumer` pops JSON messages from the Redis list `queue.raw_listings.unprocessed` and hands them to `EtlPipelineService`, which saves the `raw_listings` row, calls the LLM parser, skips listings flagged `is_valid_hardware=false`, upserts a `standard_sku` by `(brand, model_name)`, and appends a `price_history` row.
- `LLMParserService` posts to an OpenAI-compatible `/chat/completions` with `response_format: json_object`. Config defaults to `https://api.deepseek.com` / `deepseek-chat`; the key comes from `APP_LLM_API_KEY`. The system prompt asks for `brand` (Chinese to English), a `category` from GPU/CPU/SSD/RAM, `model_series`, `chipset`, integer `vram`, a `condition` from NEW/OPEN_BOX/USED/MINING/JUNK, `is_valid_hardware`, and `reject_reason`. Malformed JSON is rejected and re-queued.
- `SpiderSchedulerService` is a Redis ZSET keyword scheduler (`spider:tasks`, `spider:tasks:inflight`, `spider:entities:known`, `spider:tasks:last_score`) driven by three Lua scripts. `SpiderSeedRunner` seeds five keywords at startup. `SpiderInflightWatchdog` requeues keywords whose inflight timestamp is older than 10 minutes, every 5 minutes, with a small score penalty.
- `PulseRawController` exposes `POST /api/pulse/raw` on port 8080 — the same path and port as the Rust edge.
- `init-scripts/01_schema.sql` creates `raw_listings`, `standard_skus`, `price_history` (converted to a TimescaleDB hypertable when `create_hypertable` is available, otherwise a plain table), `pcdn_roi_metrics` and `xianyu_snipes`.

### Scripts and tooling

- `setup_env.sh` — installs `postgresql redis openjdk-17 python` via `pkg`, runs `initdb`, sets flash-friendly PostgreSQL options, creates the role and database, and applies `init-scripts/01_schema.sql`.
- `start_all.sh` — kills stale processes, launches the edge binary, waits for `/health`, seeds keywords parsed out of `config.yml`, then launches the crawler. It requires the edge binary to already exist at `hardware-pulse-edge/target/release/hardware-pulse-edge`; it does not build it, and it does not start PostgreSQL, Redis or the Java backend.
- `stop_all.sh` — stops the crawler and edge.
- `termux_audit.py` — hygiene linter over the shell/YAML/Python/SQL files; flags `sudo`/`apt-get`/`su`, Docker-style hostnames, non-loopback database URLs, absolute `/var`, `/etc`, `/usr` paths outside `$PREFIX`, and TimescaleDB use without a fallback guard.

## Getting started

Prerequisite: an Android phone with developer mode, Termux from F-Droid, and Termux:API.

```bash
git clone https://github.com/Seal-Re/hardware-pulse.git && cd hardware-pulse

cp .env.example .env      # SPRING_DATASOURCE_* and APP_LLM_API_KEY — only needed for the Java backend

# Build the edge once (inside Termux)
pkg install -y rust
cd hardware-pulse-edge && cargo build --release && cd ..

# Start the edge and the crawler
./start_all.sh

# Logs
tail -f logs/edge.log logs/crawler.log logs/mem_watch.log
```

To run the full Java stack as well, run `./setup_env.sh`, then start Redis and PostgreSQL, build with `mvn package` in `hardware-pulse-backend/`, and run the jar. Note that it binds the same port and path as the edge, so only one of the two should be running at a time.

## Two halves

The Termux path and the Java path are currently separate:

- The live path is edge (SQLite) + crawler, both started by `start_all.sh`.
- The Java backend needs Redis and PostgreSQL and is started by neither `start_all.sh` nor `setup_env.sh`; nothing moves rows from the edge's SQLite database into Redis or PostgreSQL, so the LLM ETL does not receive data from the live crawler.
- `xianyu_snipes` and `pcdn_roi_metrics` exist in the DDL but nothing writes to them.

## Limitations

- There is no user-facing client: no mobile app, no web dashboard, and no notification or alerting (all roadmap items).
- The `Redis ZSET scheduler` described in the header comment of the crawler entry point and in `main.py` is not what the entry point uses; it leases keywords over HTTP.
- The LLM prompt constrains `category` to GPU/CPU/SSD/RAM only, so motherboard and chassis listings fall outside the canonical model.
- The Rust edge and the Spring Boot backend can collide on `127.0.0.1:8080`.

## License

MIT. See `LICENSE`.
