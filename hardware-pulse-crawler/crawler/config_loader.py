from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CrawlerConfig:
    redis_host: str
    redis_port: int
    backend_ingest_url: str
    device_adb: str
    atx_agent_path: str
    thresholds: dict[str, float]
    ui_debug: bool
    ui_trace: bool
    dump_path: str


def _as_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def load_config(path: str | Path) -> CrawlerConfig:
    """Load config from YAML.

    This intentionally does NOT read environment variables.
    """
    try:
        import yaml
    except Exception as e:  # pragma: no cover
        raise RuntimeError("PyYAML is required (pip install -r requirements.txt)") from e

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config.yml not found: {p}")

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("config.yml root must be a mapping")

    redis_cfg = data.get("redis") or {}
    backend_cfg = data.get("backend") or {}
    device_cfg = data.get("device") or {}
    debug_cfg = data.get("debug") or {}
    thresholds_cfg = data.get("thresholds") or {}

    if not isinstance(redis_cfg, dict):
        redis_cfg = {}
    if not isinstance(backend_cfg, dict):
        backend_cfg = {}
    if not isinstance(device_cfg, dict):
        device_cfg = {}
    if not isinstance(debug_cfg, dict):
        debug_cfg = {}
    if not isinstance(thresholds_cfg, dict):
        thresholds_cfg = {}

    thresholds: dict[str, float] = {}
    for k, v in thresholds_cfg.items():
        if k is None:
            continue
        try:
            thresholds[str(k)] = float(v)
        except Exception:
            continue
    if not thresholds:
        thresholds = {"X99": 300.0}

    return CrawlerConfig(
        redis_host=str(redis_cfg.get("host") or "127.0.0.1"),
        redis_port=int(redis_cfg.get("port") or 6379),
        backend_ingest_url=str(backend_cfg.get("ingest_url") or "http://127.0.0.1:8080/api/pulse/raw"),
        device_adb=str(device_cfg.get("adb") or "127.0.0.1:5555"),
        atx_agent_path=str(device_cfg.get("atx_agent_path") or "/data/local/tmp/atx-agent"),
        thresholds=thresholds,
        ui_debug=_as_bool(debug_cfg.get("ui_debug"), default=False),
        ui_trace=_as_bool(debug_cfg.get("ui_trace"), default=False),
        dump_path=str(debug_cfg.get("dump_path") or ""),
    )
