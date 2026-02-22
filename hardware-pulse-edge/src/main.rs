use std::collections::HashMap;
use std::fs;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use chrono::Utc;
use clap::Parser;
use parking_lot::Mutex;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use tower_http::{
    limit::RequestBodyLimitLayer,
    trace::{DefaultMakeSpan, DefaultOnResponse, TraceLayer},
};
use tracing::{info, warn};
use tracing_subscriber::EnvFilter;

#[derive(Parser, Debug, Clone)]
#[command(name = "hardware-pulse-edge")]
struct Args {
    /// Bind address for HTTP server.
    #[arg(long, default_value = "127.0.0.1:8080")]
    bind: String,

    /// SQLite DB path (will be created if missing).
    #[arg(long, default_value = "data/hardware_pulse.db")]
    db_path: String,

    /// Max items per ingest request.
    #[arg(long, default_value_t = 200)]
    max_items: usize,

    /// Keyword lease seconds (inflight TTL).
    #[arg(long, default_value_t = 180)]
    lease_seconds: i64,

    /// Memwatch output log.
    #[arg(long, default_value = "logs/mem_watch.log")]
    memwatch_log: String,

    /// Memwatch sampling interval.
    #[arg(long, default_value_t = 1000)]
    memwatch_interval_ms: u64,

    /// Enable internal ETL worker.
    #[arg(long, default_value_t = true)]
    enable_etl: bool,

    /// PID file for this edge process.
    #[arg(long, default_value = "/data/data/com.termux/files/usr/var/run/hardware-pulse-edge.pid")]
    pid_file: String,

    /// PID file for the crawler process (optional). Used by memwatch.
    #[arg(long, default_value = "/data/data/com.termux/files/usr/var/run/hardware-pulse-crawler.pid")]
    crawler_pid_file: String,
}

#[derive(Clone)]
struct AppState {
    db: Db,
    args: Args,
}

#[derive(Clone)]
struct Db {
    // SQLite Connection is !Send/!Sync. Protect it behind a Mutex and keep all DB work short.
    conn: std::sync::Arc<Mutex<Connection>>,
}

#[derive(Deserialize)]
struct NextQuery {
    worker_id: Option<String>,
}

#[derive(Serialize)]
struct NextResponse {
    keyword: String,
    lease_seconds: i64,
}

#[derive(Deserialize, Debug)]
struct SeedRequest {
    keywords: Vec<String>,
    #[serde(default)]
    initial_score: Option<f64>,
}

#[derive(Deserialize, Debug)]
struct PulseRawBatch {
    keyword: String,
    #[serde(default)]
    platform: Option<String>,
    items: Vec<PulseRawItem>,
}

#[derive(Deserialize, Debug, Clone)]
struct PulseRawItem {
    title: String,
    #[serde(default)]
    price_text: Option<String>,
    #[serde(default)]
    snippet: Option<String>,
    #[serde(default)]
    ui_snapshot: Option<String>,
    #[serde(default)]
    crawled_at: Option<String>,
    #[serde(default)]
    seller_info: Option<HashMap<String, serde_json::Value>>,
}

#[derive(Serialize)]
struct IngestResponse {
    accepted: usize,
}

#[derive(Serialize)]
struct HealthResponse {
    ok: bool,
    ts: i64,
}

fn now_ts() -> i64 {
    Utc::now().timestamp()
}

fn ensure_parent_dir(path: &Path) -> Result<()> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).with_context(|| format!("create dir {:?}", parent))?;
        }
    }
    Ok(())
}

fn db_open(db_path: &Path) -> Result<Db> {
    ensure_parent_dir(db_path)?;
    let conn = Connection::open(db_path).with_context(|| format!("open sqlite {:?}", db_path))?;

    // Keep memory usage bounded on mobile devices.
    conn.pragma_update(None, "journal_mode", "WAL").ok();
    conn.pragma_update(None, "synchronous", "NORMAL").ok();
    conn.pragma_update(None, "temp_store", "MEMORY").ok();
    conn.pragma_update(None, "cache_size", -20_000).ok(); // ~20MB
    conn.busy_timeout(Duration::from_millis(500))?;

    let db = Db {
        conn: std::sync::Arc::new(Mutex::new(conn)),
    };
    db_migrate(&db)?;
    Ok(db)
}

fn db_migrate(db: &Db) -> Result<()> {
    let conn = db.conn.lock();
    conn.execute_batch(
        r#"
        CREATE TABLE IF NOT EXISTS tasks (
          keyword TEXT PRIMARY KEY,
          score REAL NOT NULL,
          inflight_until INTEGER NOT NULL DEFAULT 0,
          attempts INTEGER NOT NULL DEFAULT 0,
          updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_pick ON tasks(inflight_until, score);

        CREATE TABLE IF NOT EXISTS raw_listings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          keyword TEXT NOT NULL,
          platform TEXT NOT NULL,
          title TEXT NOT NULL,
          price_text TEXT NOT NULL,
          price REAL NOT NULL,
          snippet TEXT NOT NULL,
          ui_snapshot TEXT NOT NULL,
          seller_json TEXT NOT NULL,
          crawled_at TEXT NOT NULL,
          ingested_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_raw_keyword ON raw_listings(keyword);
        CREATE INDEX IF NOT EXISTS idx_raw_ingested ON raw_listings(ingested_at);

        CREATE TABLE IF NOT EXISTS etl_queue (
          raw_id INTEGER PRIMARY KEY,
          status TEXT NOT NULL,
          locked_until INTEGER NOT NULL DEFAULT 0,
          retry_count INTEGER NOT NULL DEFAULT 0,
          last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_etl_pick ON etl_queue(status, locked_until);

        CREATE TABLE IF NOT EXISTS standard_sku (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sku_key TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS price_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sku_key TEXT NOT NULL,
          price REAL NOT NULL,
          price_text TEXT NOT NULL,
          keyword TEXT NOT NULL,
          raw_id INTEGER NOT NULL UNIQUE,
          ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_price_sku_ts ON price_history(sku_key, ts);
        "#,
    )?;
    Ok(())
}

fn parse_price(price_text: &str) -> f64 {
    // Very small and safe parser: pick first numeric token.
    let s = price_text.replace(',', " ");
    let mut buf = String::new();
    let mut started = false;
    for ch in s.chars() {
        if ch.is_ascii_digit() {
            started = true;
            buf.push(ch);
            continue;
        }
        if started && ch == '.' {
            buf.push(ch);
            continue;
        }
        if started {
            break;
        }
    }
    buf.parse::<f64>().unwrap_or(0.0)
}

fn normalize_sku_key(title: &str) -> String {
    // Minimal, deterministic key: lowercase, strip spaces, keep ascii-alnum and a few separators.
    // This is intentionally simple to keep CPU/memory low on-device.
    let mut out = String::with_capacity(title.len().min(96));
    for ch in title.chars() {
        let c = ch.to_ascii_lowercase();
        if c.is_ascii_alphanumeric() {
            out.push(c);
        } else if matches!(c, ' ' | '_' | '-' | '+') {
            if !out.ends_with('_') {
                out.push('_');
            }
        }
        if out.len() >= 96 {
            break;
        }
    }
    out.trim_matches('_').to_string()
}

fn compute_heat_delta(item_count: usize) -> f64 {
    // Keep it simple: more items => hotter.
    if item_count == 0 {
        -5.0
    } else if item_count >= 20 {
        10.0
    } else {
        2.0
    }
}

async fn health() -> impl IntoResponse {
    let resp = HealthResponse { ok: true, ts: now_ts() };
    (StatusCode::OK, Json(resp))
}

async fn seed(State(st): State<AppState>, Json(req): Json<SeedRequest>) -> impl IntoResponse {
    let init_score = req.initial_score.unwrap_or(50.0);
    let mut added = 0usize;
    let ts = now_ts();
    {
        let mut conn = st.db.conn.lock();
        let tx = match conn.transaction() {
            Ok(t) => t,
            Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
        };
        for kw in req.keywords {
            let keyword = kw.trim();
            if keyword.is_empty() {
                continue;
            }
            let changed = tx
                .execute(
                    "INSERT OR IGNORE INTO tasks(keyword, score, inflight_until, attempts, updated_at) VALUES(?1, ?2, 0, 0, ?3)",
                    params![keyword, init_score, ts],
                )
                .unwrap_or(0);
            if changed > 0 {
                added += 1;
            }
        }
        if let Err(e) = tx.commit() {
            return (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response();
        }
    }
    (StatusCode::OK, Json(serde_json::json!({"added": added}))).into_response()
}

async fn next_keyword(State(st): State<AppState>, Query(q): Query<NextQuery>) -> impl IntoResponse {
    let _worker_id = q.worker_id.unwrap_or_default();
    let ts = now_ts();
    let lease_until = ts + st.args.lease_seconds;
    let mut keyword: Option<String> = None;
    {
        let mut conn = st.db.conn.lock();
        let tx = match conn.transaction() {
            Ok(t) => t,
            Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
        };
        // Pick highest score among not inflight.
        let picked: Option<(String, f64)> = tx
            .query_row(
                "SELECT keyword, score FROM tasks WHERE inflight_until <= ?1 ORDER BY score DESC, updated_at ASC LIMIT 1",
                params![ts],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()
            .unwrap_or(None);
        if let Some((kw, _score)) = picked {
            let _ = tx.execute(
                "UPDATE tasks SET inflight_until=?2, attempts=attempts+1, updated_at=?3 WHERE keyword=?1",
                params![kw, lease_until, ts],
            );
            keyword = Some(kw);
        }
        if let Err(e) = tx.commit() {
            return (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response();
        }
    }

    match keyword {
        Some(kw) => {
            let resp = NextResponse {
                keyword: kw,
                lease_seconds: st.args.lease_seconds,
            };
            (StatusCode::OK, Json(resp)).into_response()
        }
        None => StatusCode::NO_CONTENT.into_response(),
    }
}

async fn ingest_raw(State(st): State<AppState>, Json(mut batch): Json<PulseRawBatch>) -> impl IntoResponse {
    let keyword = batch.keyword.trim().to_string();
    if keyword.is_empty() {
        return (StatusCode::BAD_REQUEST, "keyword is required").into_response();
    }

    if batch.items.len() > st.args.max_items {
        batch.items.truncate(st.args.max_items);
    }

    let platform = batch
        .platform
        .clone()
        .unwrap_or_else(|| "XIANYU".to_string())
        .trim()
        .to_uppercase();

    let ingested_at = now_ts();

    let delta = compute_heat_delta(batch.items.len());
    let mut accepted = 0usize;

    {
        let mut conn = st.db.conn.lock();
        let tx = match conn.transaction() {
            Ok(t) => t,
            Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
        };

        for it in batch.items.iter() {
            let title = it.title.trim();
            if title.is_empty() {
                continue;
            }

            let price_text = it.price_text.clone().unwrap_or_default();
            let price = parse_price(&price_text);
            let snippet = it.snippet.clone().unwrap_or_default();
            let ui_snapshot = it.ui_snapshot.clone().unwrap_or_default();
            let crawled_at = it.crawled_at.clone().unwrap_or_default();
            let seller_json = match &it.seller_info {
                Some(m) => serde_json::to_string(m).unwrap_or_else(|_| "{}".to_string()),
                None => "{}".to_string(),
            };

            let raw_id: i64 = match tx.query_row(
                "INSERT INTO raw_listings(keyword, platform, title, price_text, price, snippet, ui_snapshot, seller_json, crawled_at, ingested_at) VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10) RETURNING id",
                params![keyword, platform, title, price_text, price, snippet, ui_snapshot, seller_json, crawled_at, ingested_at],
                |row| row.get(0),
            ) {
                Ok(id) => id,
                Err(e) => {
                    warn!("insert raw_listings failed: {e}");
                    continue;
                }
            };

            let _ = tx.execute(
                "INSERT OR IGNORE INTO etl_queue(raw_id, status, locked_until, retry_count, last_error) VALUES(?1, 'PENDING', 0, 0, '')",
                params![raw_id],
            );
            accepted += 1;
        }

        // ACK+requeue keyword with updated score.
        let _ = tx.execute(
            "INSERT INTO tasks(keyword, score, inflight_until, attempts, updated_at) VALUES(?1, 50.0, 0, 0, ?2) ON CONFLICT(keyword) DO UPDATE SET inflight_until=0, score=MAX(0.0, tasks.score + ?3), updated_at=?2",
            params![keyword, ingested_at, delta],
        );

        if let Err(e) = tx.commit() {
            return (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response();
        }
    }

    (StatusCode::OK, Json(IngestResponse { accepted })).into_response()
}

async fn etl_worker_loop(st: AppState) {
    let mut ticker = tokio::time::interval(Duration::from_millis(700));
    loop {
        ticker.tick().await;
        if !st.args.enable_etl {
            continue;
        }
        if let Err(e) = etl_tick(&st) {
            warn!("etl tick failed: {e:#}");
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }
}

fn etl_tick(st: &AppState) -> Result<()> {
    let ts = now_ts();
    let mut work: Vec<i64> = Vec::new();
    {
        let mut conn = st.db.conn.lock();
        let tx = conn.transaction()?;
        {
            let mut stmt = tx.prepare(
                "SELECT raw_id FROM etl_queue WHERE status='PENDING' AND locked_until <= ?1 ORDER BY raw_id ASC LIMIT 20",
            )?;
            let rows = stmt.query_map(params![ts], |row| row.get::<_, i64>(0))?;
            for r in rows {
                if let Ok(id) = r {
                    work.push(id);
                }
            }
        } // stmt dropped before commit
        for raw_id in work.iter() {
            let _ = tx.execute(
                "UPDATE etl_queue SET status='RUNNING', locked_until=?2 WHERE raw_id=?1",
                params![raw_id, ts + 30],
            );
        }
        tx.commit()?;
    }

    if work.is_empty() {
        return Ok(());
    }

    for raw_id in work {
        if let Err(e) = etl_process_one(st, raw_id) {
            warn!("etl raw_id={raw_id} failed: {e:#}");
            let conn = st.db.conn.lock();
            let _ = conn.execute(
                "UPDATE etl_queue SET status='PENDING', locked_until=?2, retry_count=retry_count+1, last_error=?3 WHERE raw_id=?1",
                params![raw_id, ts + 10, format!("{e:#}")],
            );
        }
    }
    Ok(())
}

fn etl_process_one(st: &AppState, raw_id: i64) -> Result<()> {
    let ts = now_ts();
    let (keyword, title, price, price_text): (String, String, f64, String) = {
        let conn = st.db.conn.lock();
        conn.query_row(
            "SELECT keyword, title, price, price_text FROM raw_listings WHERE id=?1",
            params![raw_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .optional()?
        .ok_or_else(|| anyhow!("raw_listings not found"))?
    };

    let sku_key = normalize_sku_key(&title);
    if sku_key.is_empty() {
        return Err(anyhow!("empty sku_key"));
    }

    {
        let mut conn = st.db.conn.lock();
        let tx = conn.transaction()?;
        let _ = tx.execute(
            "INSERT INTO standard_sku(sku_key, display_name, updated_at) VALUES(?1, ?2, ?3) ON CONFLICT(sku_key) DO UPDATE SET updated_at=?3",
            params![sku_key, title, ts],
        );
        let _ = tx.execute(
            "INSERT OR IGNORE INTO price_history(sku_key, price, price_text, keyword, raw_id, ts) VALUES(?1, ?2, ?3, ?4, ?5, ?6)",
            params![sku_key, price, price_text, keyword, raw_id, ts],
        );
        let _ = tx.execute(
            "UPDATE etl_queue SET status='DONE', locked_until=0 WHERE raw_id=?1",
            params![raw_id],
        );
        tx.commit()?;
    }
    Ok(())
}

async fn memwatch_loop(st: AppState) {
    let path = PathBuf::from(st.args.memwatch_log.clone());
    if let Err(e) = ensure_parent_dir(&path) {
        warn!("memwatch ensure log dir failed: {e:#}");
        return;
    }

    // Buffer in memory to avoid heavy 1Hz fsync/IO. Flush every 10 lines.
    let mut buf: Vec<String> = Vec::with_capacity(16);
    let mut ticker = tokio::time::interval(Duration::from_millis(st.args.memwatch_interval_ms.max(200)));
    loop {
        ticker.tick().await;
        let line = build_mem_line(&st.args.crawler_pid_file)
            .unwrap_or_else(|e| format!("ts={} memwatch_err={}", now_ts(), e));
        buf.push(line);
        if buf.len() < 10 {
            continue;
        }
        let chunk = buf.join("\n") + "\n";
        buf.clear();

        // Best-effort append.
        if let Err(e) = append_and_trim(&path, &chunk, 5 * 1024 * 1024) {
            warn!("memwatch append failed: {e:#}");
        }
    }
}

fn build_mem_line(crawler_pid_file: &str) -> Result<String> {
    let ts = now_ts();
    let meminfo = fs::read_to_string("/proc/meminfo").context("read /proc/meminfo")?;
    let mut avail_kb = 0u64;
    let mut free_kb = 0u64;
    let mut cached_kb = 0u64;
    for line in meminfo.lines() {
        if let Some(v) = line.strip_prefix("MemAvailable:") {
            avail_kb = v.trim().split_whitespace().next().unwrap_or("0").parse().unwrap_or(0);
        } else if let Some(v) = line.strip_prefix("MemFree:") {
            free_kb = v.trim().split_whitespace().next().unwrap_or("0").parse().unwrap_or(0);
        } else if let Some(v) = line.strip_prefix("Cached:") {
            cached_kb = v.trim().split_whitespace().next().unwrap_or("0").parse().unwrap_or(0);
        }
    }

    let rss_kb = read_status_kb("/proc/self/status", "VmRSS").unwrap_or(0);

    // Optional: monitor crawler RSS by reading PID from file (created by start_all.sh).
    let crawler_rss_kb = match fs::read_to_string(crawler_pid_file) {
        Ok(s) => {
            let pid = s.trim().parse::<i64>().unwrap_or(0);
            if pid > 0 {
                read_status_kb(&format!("/proc/{}/status", pid), "VmRSS").unwrap_or(0)
            } else {
                0
            }
        }
        Err(_) => 0,
    };

    Ok(format!(
        "ts={} mem_avail_kb={} mem_free_kb={} mem_cached_kb={} edge_rss_kb={} crawler_rss_kb={}",
        ts, avail_kb, free_kb, cached_kb, rss_kb, crawler_rss_kb
    ))
}

fn read_status_kb(path: &str, key: &str) -> Result<u64> {
    let s = fs::read_to_string(path)?;
    for line in s.lines() {
        if let Some(rest) = line.strip_prefix(&format!("{}:", key)) {
            return Ok(rest.trim().split_whitespace().next().unwrap_or("0").parse().unwrap_or(0));
        }
    }
    Err(anyhow!("key not found"))
}

fn append_and_trim(path: &Path, chunk: &str, max_bytes: usize) -> Result<()> {
    use std::io::Write;

    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .with_context(|| format!("open memwatch log {:?}", path))?;
    f.write_all(chunk.as_bytes())?;

    // Trim by size (keep tail).
    let meta = f.metadata()?;
    let len = meta.len() as usize;
    if len <= max_bytes {
        return Ok(());
    }
    drop(f);

    let bytes = fs::read(path)?;
    let keep = bytes.len().saturating_sub(max_bytes);
    fs::write(path, &bytes[keep..])?;
    Ok(())
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("info".parse()?))
        .init();

    let args = Args::parse();

    // Best-effort: write our own PID for scripts.
    if !args.pid_file.is_empty() {
        let _ = ensure_parent_dir(Path::new(&args.pid_file));
        let _ = fs::write(&args.pid_file, format!("{}\n", std::process::id()));
    }
    let addr: SocketAddr = args
        .bind
        .parse()
        .with_context(|| format!("parse bind addr {}", args.bind))?;
    let db = db_open(Path::new(&args.db_path))?;
    info!("db opened at {}", args.db_path);

    let st = AppState { db, args: args.clone() };

    // Background loops.
    tokio::spawn(etl_worker_loop(st.clone()));
    tokio::spawn(memwatch_loop(st.clone()));

    let app = Router::new()
        .route("/health", get(health))
        .route("/api/spider/seed", post(seed))
        .route("/api/spider/next", get(next_keyword))
        .route("/api/pulse/raw", post(ingest_raw))
        .layer(RequestBodyLimitLayer::new(256 * 1024))
        .layer(
            TraceLayer::new_for_http()
                .make_span_with(DefaultMakeSpan::new().include_headers(false))
                .on_response(DefaultOnResponse::new().include_headers(false)),
        )
        .with_state(st);

    info!("listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .map_err(|e| anyhow!(e))?;

    Ok(())
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
    warn!("shutdown signal received");
}
