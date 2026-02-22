"""HardwarePulse - WireGuard + uiautomator2 中控爬虫（闲鱼）

Industrial-grade, state-machine based crawler:
- No dump_hierarchy(), no XML parsing.
- No blind time.sleep(); use explicit UI waits.
- Two-stage scrape: list pre-filter -> detail deep dive.
- In-memory MD5 dedup; stop after 3 swipes w/o new items or end reached.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import sys
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Any

# When launched from repo root (e.g. `python hardware-pulse-crawler/crawler_wg_xianyu.py`),
# ensure this file's directory is on sys.path so `import crawler.*` works.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import requests
import uiautomator2 as u2
from urllib.parse import urlsplit

from crawler.config_loader import CrawlerConfig, load_config

CONFIG_PATH_DEFAULT = str(Path(__file__).with_name("config.yml"))

CFG: CrawlerConfig | None = None
DEVICE_ADB = "127.0.0.1:5555"
BACKEND_INGEST_URL = "http://127.0.0.1:8080/api/pulse/raw"
EDGE_BASE_URL = "http://127.0.0.1:8080"
ATX_AGENT_PATH = "/data/local/tmp/atx-agent"
XIANYU_PKG_NAME = "com.taobao.idlefish"

# Debug flags are controlled via config.yml (no env variables).
HP_DEBUG_UI = False
HP_TRACE_UI = False
HP_DUMP_PATH = ""


# Reuse HTTP connections to avoid socket/resource churn.
_HTTP = requests.Session()

# Hard caps to prevent large-object retention + oversized payloads.
MAX_DESC_CHARS = 800
MAX_SNAPSHOT_CHARS = 2000
MAX_DETAIL_DUMP_CHARS = 1200


def _load_cfg_or_die(config_path: str) -> CrawlerConfig:
    global CFG, DEVICE_ADB, BACKEND_INGEST_URL, EDGE_BASE_URL, ATX_AGENT_PATH
    global HP_DEBUG_UI, HP_TRACE_UI, HP_DUMP_PATH
    cfg = load_config(config_path)
    CFG = cfg

    DEVICE_ADB = cfg.device_adb
    BACKEND_INGEST_URL = cfg.backend_ingest_url
    EDGE_BASE_URL = _edge_base_from_ingest_url(BACKEND_INGEST_URL)
    ATX_AGENT_PATH = cfg.atx_agent_path
    HP_DEBUG_UI = bool(cfg.ui_debug)
    HP_TRACE_UI = bool(cfg.ui_trace)
    HP_DUMP_PATH = str(cfg.dump_path or "")
    return cfg


def _edge_base_from_ingest_url(ingest_url: str) -> str:
    """Derive base URL from ingest endpoint.

    Example: http://127.0.0.1:8080/api/pulse/raw -> http://127.0.0.1:8080
    """
    try:
        u = urlsplit(ingest_url)
        if not u.scheme or not u.netloc:
            return "http://127.0.0.1:8080"
        return f"{u.scheme}://{u.netloc}"
    except Exception:
        return "http://127.0.0.1:8080"


def _ui_debug(msg: str, *args: object) -> None:
    if not HP_DEBUG_UI:
        return
    try:
        logging.info("[UI-DBG] " + msg, *args)
    except Exception:
        pass


def _format_texts_sample(texts: set[str], limit: int = 18) -> str:
    # deterministic sample: shorter strings first then lexicographically
    items = sorted(texts, key=lambda s: (len(s), s))
    return " | ".join(items[:max(1, limit)])


def _dump_band_debug(d, stage: str) -> None:
    if not HP_DEBUG_UI:
        return
    try:
        cur = d.app_current()
        _ui_debug("stage=%s app=%s/%s", stage, cur.get("package"), cur.get("activity"))
    except Exception as e:
        _ui_debug("stage=%s app_current failed: %r", stage, e)

    w, h = _get_window_size(d)
    _ui_debug("stage=%s win=%sx%s", stage, w, h)
    nodes = _get_nodes_strings_bounds(d)
    top = _texts_in_vertical_band(nodes, h, 0.00, 0.35)
    bottom = _texts_in_vertical_band(nodes, h, 0.78, 1.00)
    _ui_debug("stage=%s top_sample=%s", stage, _format_texts_sample(top))
    _ui_debug("stage=%s bottom_sample=%s", stage, _format_texts_sample(bottom))


def _safe_mkdir(path: str) -> None:
    if not path:
        return
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def dump_ui_snapshot(d, stage: str, reason: str = "") -> None:
    """Dump a JSONL snapshot of current UI strings+bounds for offline debugging.

    Only enabled when HP_TRACE_UI=1 and HP_DUMP_PATH is set.
    """
    if not (HP_TRACE_UI and HP_DUMP_PATH):
        return
    _safe_mkdir(HP_DUMP_PATH)

    ts = int(time.time() * 1000)
    out_path = os.path.join(HP_DUMP_PATH, f"xianyu_ui_{time.strftime('%Y%m%d')}.jsonl")

    try:
        cur = d.app_current()
    except Exception:
        cur = {"package": "", "activity": ""}

    w, h = _get_window_size(d)
    nodes = _get_nodes_strings_bounds(d)
    top = _texts_in_vertical_band(nodes, h, 0.00, 0.35)
    bottom = _texts_in_vertical_band(nodes, h, 0.78, 1.00)

    payload = {
        "ts_ms": ts,
        "stage": stage,
        "reason": reason,
        "app": {"package": cur.get("package"), "activity": cur.get("activity")},
        "win": {"w": w, "h": h},
        "band_samples": {
            "top": _format_texts_sample(top, limit=28),
            "bottom": _format_texts_sample(bottom, limit=28),
        },
        "nodes": [
            {
                "label": s,
                "bounds": {"l": bb[0], "t": bb[1], "r": bb[2], "b": bb[3]},
                "center": {"x": (bb[0] + bb[2]) // 2, "y": (bb[1] + bb[3]) // 2},
            }
            for (s, bb) in nodes
        ],
    }

    try:
        import json

        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return

    if HP_DEBUG_UI:
        _ui_debug("dump_ui_snapshot -> %s (nodes=%d)", out_path, len(nodes))


_SEARCH_LABEL_TOKENS = {"搜索", "搜一搜", "搜宝贝", "搜同款", "查找"}


def _pick_and_click_search_entry(d) -> bool:
    """Try multiple strategies to enter Xianyu search page from home.

    Returns True if we performed a click that should open search.
    """
    # Strategy A: regex selector on description/text.
    try:
        obj = d(descriptionMatches=r".*(搜索|搜一搜|查找).*")
        if obj.exists:
            _ui_debug("search_entry strategy=A descriptionMatches")
            obj.click()
            return True
    except Exception as e:
        _ui_debug("search_entry strategy=A failed: %r", e)

    try:
        obj = d(textMatches=r".*(搜索|搜一搜|查找).*")
        if obj.exists:
            _ui_debug("search_entry strategy=B textMatches")
            obj.click()
            return True
    except Exception as e:
        _ui_debug("search_entry strategy=B failed: %r", e)

    # Strategy C: click the top EditText if present.
    try:
        w, h = _get_window_size(d)
        edit = d(className="android.widget.EditText")
        if edit.exists:
            info = edit.info
            b = info.get("bounds")
            if b:
                cy = (int(b["top"]) + int(b["bottom"])) // 2
                if cy <= int(h * 0.35):
                    _ui_debug("search_entry strategy=C EditText top")
                    edit.click()
                    return True
    except Exception as e:
        _ui_debug("search_entry strategy=C failed: %r", e)

    # Strategy D: top-band scan (text+desc) and click best candidate.
    try:
        w, h = _get_window_size(d)
        nodes = _get_nodes_strings_bounds(d)
        candidates: list[tuple[int, str, tuple[int, int, int, int]]] = []
        for s, bb in nodes:
            cy = (bb[1] + bb[3]) // 2
            if cy > int(h * 0.35):
                continue
            if not any(tok in s for tok in _SEARCH_LABEL_TOKENS):
                continue
            width = bb[2] - bb[0]
            # Prefer wider, higher elements.
            score = 1000 + min(width, w) - cy
            candidates.append((score, s, bb))

        candidates.sort(reverse=True, key=lambda x: x[0])
        if HP_DEBUG_UI and candidates:
            _ui_debug("search_entry strategy=D candidates=%s", " | ".join([f"{c[1]}@{c[2]}" for c in candidates[:5]]))

        if candidates:
            _score, s, bb = candidates[0]
            cx = (bb[0] + bb[2]) // 2
            cy = (bb[1] + bb[3]) // 2
            _ui_debug("search_entry strategy=D click '%s' at %d,%d", s, cx, cy)
            d.click(cx, cy)
            return True
    except Exception as e:
        _ui_debug("search_entry strategy=D failed: %r", e)

    return False


def _shell_input_text(d, text: str) -> bool:
    # Last-resort input: system IME injection. Space must be escaped.
    try:
        s = text.replace(" ", "%s")
        d.shell(["input", "text", s])
        return True
    except Exception as e:
        _ui_debug("shell input text failed: %r", e)
        return False


def _set_input_ime_best_effort(d, enabled: bool) -> None:
    # uiautomator2 API renamed: set_fastinput_ime -> set_input_ime
    try:
        fn = getattr(d, "set_input_ime", None)
        if callable(fn):
            fn(enabled)
            return
    except Exception as e:
        _ui_debug("set_input_ime failed: %r", e)
    try:
        fn2 = getattr(d, "set_fastinput_ime", None)
        if callable(fn2):
            fn2(enabled)
    except Exception as e:
        _ui_debug("set_fastinput_ime failed: %r", e)


_SUBMIT_SEARCH_TOKENS = {"搜索", "搜一搜", "查找", "搜索一下"}
_SYSTEM_NOISE_TOKENS = {"正在充电", "已完成", "百分之", "Android系统通知", "系统通知"}


def _click_top_submit_button(d, keyword: str) -> bool:
    """Try to find a submit/search button in the top band and click it."""
    w, h = _get_window_size(d)
    nodes = _get_nodes_strings_bounds(d)
    candidates: list[tuple[int, str, tuple[int, int, int, int]]] = []
    for s, bb in nodes:
        cy = (bb[1] + bb[3]) // 2
        if cy > int(h * 0.35):
            continue
        # Drop obvious system/statusbar noise.
        if any(tok in s for tok in _SYSTEM_NOISE_TOKENS):
            continue
        if not any(tok in s for tok in _SUBMIT_SEARCH_TOKENS):
            continue
        cx = (bb[0] + bb[2]) // 2
        width = bb[2] - bb[0]
        # Prefer right-side, smaller button-like elements, and higher.
        score = 100000
        score += int((cx / max(1, w)) * 5000)  # right is better
        score -= min(width, w)                 # smaller is better
        score -= cy                            # higher is better
        candidates.append((score, s, bb))

    candidates.sort(reverse=True, key=lambda x: x[0])
    if HP_DEBUG_UI:
        _ui_debug("submit top candidates=%s", " | ".join([f"{c[1]}@{c[2]}" for c in candidates[:10]]))

    if not candidates:
        return False
    _score, s, bb = candidates[0]
    cx = (bb[0] + bb[2]) // 2
    cy = (bb[1] + bb[3]) // 2
    _ui_debug("submit click top '%s' at %d,%d", s, cx, cy)
    try:
        d.click(cx, cy)
        return True
    except Exception as e:
        _ui_debug("submit click failed: %r", e)
        return False


def _click_suggestion_item(d, keyword: str) -> bool:
    """Click a suggestion item in the middle band as a fallback submit mechanism."""
    w, h = _get_window_size(d)
    nodes = _get_nodes_strings_bounds(d)
    mids: list[tuple[int, str, tuple[int, int, int, int]]] = []
    for s, bb in nodes:
        cy = (bb[1] + bb[3]) // 2
        if not (int(h * 0.35) <= cy <= int(h * 0.75)):
            continue
        ss = s.strip()
        if len(ss) < 2:
            continue
        if any(tok in ss for tok in {"历史搜索", "清空", "删除", "取消"}):
            continue
        if _PRICE_RE_WITH_CURRENCY.search(ss):
            continue
        # Prefer items containing keyword; otherwise prefer higher items.
        score = 0
        if keyword and keyword in ss:
            score += 10000
        score -= cy
        mids.append((score, ss, bb))

    mids.sort(reverse=True, key=lambda x: x[0])
    if HP_DEBUG_UI:
        _ui_debug("suggest candidates=%s", " | ".join([f"{c[1]}@{c[2]}" for c in mids[:10]]))
    if not mids:
        return False
    _score, s, bb = mids[0]
    cx = (bb[0] + bb[2]) // 2
    cy = (bb[1] + bb[3]) // 2
    _ui_debug("suggest click '%s' at %d,%d", s, cx, cy)
    try:
        d.click(cx, cy)
        return True
    except Exception as e:
        _ui_debug("suggest click failed: %r", e)
        return False


def input_keyword_and_submit(d, keyword: str) -> bool:
    """Type keyword into search box and submit.

    This is designed for Termux+uiautomator2 environments where ADB keyboard broadcast can fail.
    """
    w, h = _get_window_size(d)
    dump_ui_snapshot(d, stage="before_keyword_input", reason=keyword)

    # Focus top EditText.
    edit = d(className="android.widget.EditText")
    if not edit.wait(timeout=8):
        logging.error("[UI] 搜索输入框未出现")
        _dump_band_debug(d, stage="edit_not_found")
        dump_ui_snapshot(d, stage="edit_not_found", reason=keyword)
        return False

    try:
        info = edit.info
        b = info.get("bounds")
        if b:
            cy = (int(b["top"]) + int(b["bottom"])) // 2
            if cy > int(h * 0.55):
                _ui_debug("EditText appears not in top band (cy=%d)", cy)
        edit.click()
    except Exception as e:
        _ui_debug("edit focus click failed: %r", e)

    # Input strategies.
    typed = False
    try:
        _set_input_ime_best_effort(d, True)
        try:
            d.send_keys(keyword, clear=True)
            typed = True
            _ui_debug("input strategy=A input_ime send_keys")
        except Exception as e:
            _ui_debug("input strategy=A failed: %r", e)
    except Exception as e:
        _ui_debug("input ime setup failed: %r", e)

    if not typed:
        try:
            edit.clear_text()
        except Exception:
            pass
        try:
            edit.set_text(keyword)
            typed = True
            _ui_debug("input strategy=B EditText.set_text")
        except Exception as e:
            _ui_debug("input strategy=B failed: %r", e)

    if not typed:
        typed = _shell_input_text(d, keyword)
        if typed:
            _ui_debug("input strategy=C shell input text")

    dump_ui_snapshot(d, stage="after_keyword_input", reason=keyword)

    if not typed:
        logging.error("[UI] 关键词注入失败")
        return False

    # Submit strategies: prefer clicking actual UI buttons; IME actions are flaky.
    dump_ui_snapshot(d, stage="before_submit", reason=keyword)
    submitted = False

    if _click_top_submit_button(d, keyword):
        submitted = True
        _ui_debug("submit strategy=A click top submit")

    if not submitted:
        try:
            d.send_action("search")
            submitted = True
            _ui_debug("submit strategy=B send_action(search)")
        except Exception as e:
            _ui_debug("submit strategy=B failed: %r", e)

    if not submitted:
        try:
            d.press("enter")
            submitted = True
            _ui_debug("submit strategy=C press(enter)")
        except Exception as e:
            _ui_debug("submit strategy=C failed: %r", e)

    # Suggestion list click fallback (often triggers navigation to results).
    if not submitted:
        if _click_suggestion_item(d, keyword):
            submitted = True
            _ui_debug("submit strategy=D click suggestion")

    dump_ui_snapshot(d, stage="after_submit_click", reason=keyword)

    dump_ui_snapshot(d, stage="after_submit", reason=keyword)
    if not submitted:
        dump_ui_snapshot(d, stage="submit_failed", reason=keyword)
    return submitted

def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def _iso8601_now_local() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def connect_device(_ip: str):
    """
    具备底层 ADB 隧道自愈与 Agent 唤醒能力的连接器。
    """
    last_err = None
    for attempt in range(1, 4):
        try:
            logging.info(f"[ATX] 尝试连接本地 ADB 设备 (尝试 {attempt}/3)...")
            
            # 1. 强制走本地 ADB 隧道通信，规避纯 HTTP 死锁
            d = u2.connect(DEVICE_ADB)
            
            try: 
                d.implicitly_wait(10.0)
            except Exception: 
                pass
                
            # 2. 强行获取 info，校验底层 Agent RPC 是否真实存活
            info = d.info
            logging.info(f"[ATX] 设备握手成功，Agent 存活 (分辨率: {info.get('displayWidth')}x{info.get('displayHeight')})")
            return d
            
        except Exception as e:
            last_err = e
            logging.warning(f"[ATX] 连接或通信失败: {repr(e)}")
            logging.info("[ATX] 触发物理自愈：正在尝试重新映射 ADB 端口并拉起 Agent...")
            
            # 3. 自愈机制：利用 subprocess 调用系统级命令强修隧道
            try:
                import subprocess
                # 修复可能断开的本地回环
                subprocess.run(["adb", "connect", DEVICE_ADB], capture_output=True, timeout=5)
                # 强制重启挂掉的 atx-agent 守护进程
                subprocess.run(["python", "-m", "uiautomator2", "init"], capture_output=True, timeout=15)
            except Exception as init_err:
                logging.error(f"[ATX] 自愈指令执行异常: {repr(init_err)}")
                
            Event().wait(4)
            
    raise RuntimeError("无法连接 ATX-Agent。隧道被毁或 Agent 处于深度死锁。") from last_err

def start_target_app(d) -> None:
    logging.info("[APP] 确保闲鱼运行中...")
    d.app_start(XIANYU_PKG_NAME)
    if not wait_home(d, timeout=15):
        logging.warning("[APP] 首页锚点未出现，继续尝试恢复")

# Price parsing: prefer currency sign, but tolerate other common UI variants.
_PRICE_RE_WITH_CURRENCY = re.compile(r"[¥￥]\s*(\d+(?:\.\d+)?)")
_PRICE_RE_FALLBACK = re.compile(r"(?<!\d)(\d{1,6}(?:\.\d{1,2})?)(?!\d)")
_CURRENCY_ONLY_RE = re.compile(r"^[¥￥]$")
_NUM_ONLY_RE = re.compile(r"^\d+(?:\.\d{1,2})?$")

_PRICE_TEXT_BLACKLIST = {
    "人想要",
    "人浏览",
    "人看过",
    "浏览",
    "分钟前",
    "小时前",
    "天前",
    "刚刚",
    "包邮",
    "已售",
    "成交",
    "信用",
}


# ---- Density Peak + Asymmetric Semantic Defense (stdlib only) ----


_LEFT_TAIL_SEMANTIC_BLACKLIST_RE = re.compile(r"(挡板|风扇|散热|坏|尸体|配件|点不亮|不包|盲盒|外壳|空机)")


def calculate_market_peak(prices: list[float], bin_size: float = 20.0) -> float:
    """Anchor baseline price using the dominant density bin (mode bin).

    - Histogram binning: bin = int(P // bin_size) * bin_size
    - Find mode bin (highest frequency)
    - Core band: [mode-bin - bin_size, mode-bin + bin_size*2]
    - Baseline = arithmetic mean of core samples

    NOTE: This avoids global mean on multimodal distributions.
    """
    vals = [float(p) for p in prices if p is not None and p > 0]
    if not vals:
        return 0.0
    if bin_size <= 0:
        bin_size = 20.0

    bins: dict[float, int] = {}
    for p in vals:
        b = float(int(p // bin_size) * bin_size)
        bins[b] = bins.get(b, 0) + 1

    # Mode bin; if tie choose the smaller bin (physical lower peak dominates relevance).
    mode_bin = min(bins.keys())
    mode_cnt = -1
    for b, cnt in bins.items():
        if cnt > mode_cnt or (cnt == mode_cnt and b < mode_bin):
            mode_cnt = cnt
            mode_bin = b

    lo = mode_bin - bin_size
    hi = mode_bin + bin_size * 2
    core = [p for p in vals if lo <= p <= hi]
    if not core:
        s = sorted(vals)
        return float(s[len(s) // 2])

    return float(sum(core) / len(core))


def is_valid_pcdn_deal(title: str, price: float, baseline: float) -> bool:
    """Return True if listing passes asymmetric price filter with semantic defense."""
    t = (title or "").strip()
    p = float(price or 0.0)
    b = float(baseline or 0.0)
    if p <= 0 or b <= 0:
        return False

    # Right-tail hard block (bundle / premium pricing)
    if p > b * 1.3:
        return False

    # Left-tail semantic defense (accessories / dead boards)
    if p < b * 0.6:
        if _LEFT_TAIL_SEMANTIC_BLACKLIST_RE.search(t):
            logging.info("[FILTER] 价格 ¥%.2f 命中左尾语义黑名单，已丢弃 title=%s", p, t)
            return False
        return True

    return True


def _parse_price_value(price_text: str) -> float:
    if not price_text:
        return 0.0
    s = price_text.replace(",", "")
    m = _PRICE_RE_WITH_CURRENCY.search(s)
    if not m:
        # Fallback: e.g. "199元", "到手199", "199".
        m = _PRICE_RE_FALLBACK.search(s)
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except Exception:
        return 0.0


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


_SCREEN_MID_X: int | None = None


def _get_screen_mid_x(d) -> int:
    global _SCREEN_MID_X
    if _SCREEN_MID_X is not None:
        return _SCREEN_MID_X
    try:
        w, _h = d.window_size()
        _SCREEN_MID_X = max(1, int(w) // 2)
    except Exception:
        # Fallback for 1080p
        _SCREEN_MID_X = 540
    return _SCREEN_MID_X


@dataclass(frozen=True)
class VisibleCard:
    title: str
    price_text: str
    price_value: float
    click_x: int
    click_y: int
    # light snapshot for debugging
    snippet: str


def _get_nodes_text_bounds(d) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Fetch all visible nodes with text and bounds using XPath.

    NOTE: This avoids dump_hierarchy() and XML parsing.
    """
    out: list[tuple[str, tuple[int, int, int, int]]] = []
    try:
        nodes = d.xpath('//*[@text]').all()
    except Exception:
        return out
    for n in nodes:
        try:
            info = n.info
            t = (info.get("text") or "").strip()
            if not t:
                continue
            b = info.get("bounds")
            if not b:
                continue
            # bounds: {'left':..,'top':..,'right':..,'bottom':..}
            out.append((t, (int(b["left"]), int(b["top"]), int(b["right"]), int(b["bottom"])) ))
        except Exception:
            continue
    return out


def _get_nodes_strings_bounds(d) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Fetch all visible nodes with string-like labels (text + content-desc) and bounds.

    Many Xianyu anchors (bottom nav) are content-desc, not text.
    Keep it hierarchy-free (no dump_hierarchy()).
    """
    out: list[tuple[str, tuple[int, int, int, int]]] = []

    # 1) Text nodes
    try:
        nodes = d.xpath('//*[@text]').all()
    except Exception:
        nodes = []
    for n in nodes:
        try:
            info = n.info
            t = (info.get("text") or "").strip()
            if not t:
                continue
            b = info.get("bounds")
            if not b:
                continue
            out.append((t, (int(b["left"]), int(b["top"]), int(b["right"]), int(b["bottom"])) ))
        except Exception:
            continue

    # 2) Content-desc nodes
    try:
        nodes2 = d.xpath('//*[@content-desc]').all()
    except Exception:
        nodes2 = []
    for n in nodes2:
        try:
            info = n.info
            # u2 uses various keys depending on backend.
            cd = (info.get("contentDescription") or info.get("content-desc") or "").strip()
            if not cd:
                continue
            b = info.get("bounds")
            if not b:
                continue
            out.append((cd, (int(b["left"]), int(b["top"]), int(b["right"]), int(b["bottom"])) ))
        except Exception:
            continue

    # Dedup by exact string + bounds.
    uniq: dict[str, tuple[str, tuple[int, int, int, int]]] = {}
    for s, bb in out:
        k = f"{s}|{bb}"
        if k not in uniq:
            uniq[k] = (s, bb)
    return list(uniq.values())


def _get_window_size(d) -> tuple[int, int]:
    try:
        w, h = d.window_size()
        return int(w), int(h)
    except Exception:
        # Common 1080p fallback
        return 1080, 1920


def _is_xianyu_foreground(d) -> bool:
    try:
        cur = d.app_current()
        return str(cur.get("package") or "") == XIANYU_PKG_NAME
    except Exception:
        return False


def _texts_in_vertical_band(
    nodes: list[tuple[str, tuple[int, int, int, int]]],
    screen_h: int,
    y_min_ratio: float,
    y_max_ratio: float,
) -> set[str]:
    if screen_h <= 0:
        return set()
    y_min = int(screen_h * y_min_ratio)
    y_max = int(screen_h * y_max_ratio)
    out: set[str] = set()
    for text, (_l, t, _r, b) in nodes:
        cy = (t + b) // 2
        if y_min <= cy <= y_max:
            out.add(text)
    return out


def _band_contains_any_token(texts: set[str], tokens: set[str]) -> int:
    """Count how many tokens are present as substrings in the band texts."""
    hit = 0
    for tok in tokens:
        for s in texts:
            if tok in s:
                hit += 1
                break
    return hit


def _ui_has_search_entry(d) -> bool:
    try:
        return bool(
            d(descriptionMatches=".*搜索.*").exists
            or d(textMatches=".*搜索.*").exists
            or d(description="搜索").exists
            or d(text="搜索").exists
        )
    except Exception:
        return False


def ensure_xianyu_foreground(d, timeout: float = 15.0) -> bool:
    """Make sure Xianyu is in foreground; restart if it was killed/exited."""
    if _is_xianyu_foreground(d):
        return True
    logging.warning("[APP] 检测到闲鱼不在前台，尝试拉起...")
    try:
        d.app_start(XIANYU_PKG_NAME)
    except Exception:
        return False
    return wait_home(d, timeout=timeout)


def _col_id(x: int, mid_x: int) -> int:
    # Xianyu results are typically 2 columns; rough binning by screen midline.
    return 0 if x < mid_x else 1


def _choose_title_node_near(
    nodes: list[tuple[str, tuple[int, int, int, int]]],
    ref_bounds: tuple[int, int, int, int],
    mid_x: int,
) -> tuple[str, tuple[int, int, int, int]] | None:
    l, t, r, b = ref_bounds
    ref_cx = (l + r) // 2
    ref_cy = (t + b) // 2
    col = _col_id(ref_cx, mid_x)

    # Candidate title is above price, same column, and reasonably close.
    best: tuple[str, tuple[int, int, int, int]] | None = None
    best_score = -1
    for text, bb in nodes:
        if "¥" in text or "￥" in text:
            continue
        if len(text) < 4:
            continue
        ll, tt, rr, bb2 = bb
        cx = (ll + rr) // 2
        cy = (tt + bb2) // 2
        if _col_id(cx, mid_x) != col:
            continue
        if cy >= ref_cy:
            continue
        dy = ref_cy - cy
        if dy > 500:
            continue
        # Prefer longer text and closer to price.
        score = (min(len(text), 40) * 10) - dy
        if score > best_score:
            best_score = score
            best = (text, bb)
    return best


def _is_price_like_text(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    if any(tok in s for tok in _PRICE_TEXT_BLACKLIST):
        return False
    if _PRICE_RE_WITH_CURRENCY.search(s):
        return True
    # Typical variants without currency sign.
    if "元" in s and _PRICE_RE_FALLBACK.search(s):
        return True
    if "到手" in s and _PRICE_RE_FALLBACK.search(s):
        return True
    # Pure numeric nodes appear for split price rendering.
    if _NUM_ONLY_RE.match(s):
        return True
    return False


def _find_split_price_candidates(
    nodes: list[tuple[str, tuple[int, int, int, int]]],
    mid_x: int,
    screen_h: int,
) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Handle split prices like: "¥" + "199" as separate nodes."""
    currency_nodes: list[tuple[str, tuple[int, int, int, int]]] = []
    numeric_nodes: list[tuple[str, tuple[int, int, int, int]]] = []

    for text, bb in nodes:
        s = (text or "").strip()
        if not s:
            continue
        if _CURRENCY_ONLY_RE.match(s):
            currency_nodes.append((s, bb))
        elif _NUM_ONLY_RE.match(s):
            numeric_nodes.append((s, bb))

    out: list[tuple[str, tuple[int, int, int, int]]] = []
    # Pair by same column + close Y.
    for cur, cbb in currency_nodes:
        cl, ct, cr, cb = cbb
        ccx = (cl + cr) // 2
        ccy = (ct + cb) // 2
        if not (int(screen_h * 0.30) <= ccy <= int(screen_h * 0.96)):
            continue
        ccol = _col_id(ccx, mid_x)

        best: tuple[int, str, tuple[int, int, int, int]] | None = None
        for num, nbb in numeric_nodes:
            nl, nt, nr, nb = nbb
            ncx = (nl + nr) // 2
            ncy = (nt + nb) // 2
            if _col_id(ncx, mid_x) != ccol:
                continue
            dy = abs(ncy - ccy)
            if dy > 140:
                continue
            # Prefer closer number node.
            score = -dy
            if best is None or score > best[0]:
                best = (score, num, nbb)

        if best is None:
            continue
        _score, num, nbb = best
        out.append((f"{cur}{num}", nbb))
    return out


def scan_visible_cards(d) -> list[VisibleCard]:
    nodes = _get_nodes_strings_bounds(d)
    if not nodes:
        return []

    mid_x = _get_screen_mid_x(d)
    _w, h = _get_window_size(d)

    price_nodes: list[tuple[str, tuple[int, int, int, int]]] = []
    for text, bb in nodes:
        s = (text or "").strip()
        if not s:
            continue
        if not _is_price_like_text(s):
            continue
        # Keep only nodes in typical list body (avoid status bar / bottom nav).
        cy = (bb[1] + bb[3]) // 2
        if not (int(h * 0.28) <= cy <= int(h * 0.96)):
            continue
        price_nodes.append((s, bb))

    # Add split-price candidates ("¥" + "199").
    price_nodes.extend(_find_split_price_candidates(nodes, mid_x=mid_x, screen_h=h))

    cards: list[VisibleCard] = []
    for price_text, pb in price_nodes:
        title_node = _choose_title_node_near(nodes, pb, mid_x)
        if not title_node:
            continue
        title, tb = title_node
        price_value = _parse_price_value(price_text)
        if price_value <= 0 or price_value > 9.9e7:
            continue

        l, t, r, b = pb
        cx = (l + r) // 2
        # Prefer clicking the card body area between title and price.
        cy = (tb[3] + t) // 2
        cy = max(0, min(cy, int(h * 0.95)))
        snippet = f"{title} | {price_text}"
        cards.append(VisibleCard(title=title, price_text=price_text, price_value=price_value, click_x=cx, click_y=cy, snippet=snippet))

    # Dedup within screen by (title, price_text)
    uniq: dict[str, VisibleCard] = {}
    for c in cards:
        k = f"{c.title}|{c.price_text}"
        if k not in uniq:
            uniq[k] = c
    return list(uniq.values())

def fetch_next_keyword_http(worker_id: str) -> str:
    """Fetch next keyword from the edge scheduler via HTTP.

    The edge service returns:
    - 200 + JSON {keyword, lease_seconds}
    - 204 when no tasks
    """
    url = f"{EDGE_BASE_URL}/api/spider/next"
    while True:
        try:
            resp = _HTTP.get(url, params={"worker_id": worker_id}, timeout=10)
            if resp.status_code == 204:
                Event().wait(0.5)
                continue
            if resp.status_code != 200:
                logging.warning("[SCHED] next non-200 status=%d body=%s", int(resp.status_code), (resp.text or "")[:160])
                Event().wait(1.0)
                continue
            data = resp.json() if resp.content else {}
            kw = str((data or {}).get("keyword") or "").strip()
            if kw:
                return kw
            Event().wait(0.5)
        except Exception as e:
            logging.warning("[SCHED] next failed err=%r", e)
            Event().wait(1.0)


def _register_watchers(d) -> None:
    # Backward-compatible alias.
    register_watchers(d)


def register_watchers(d) -> None:
    """Register watcher rules (no start here)."""
    try:
        d.watcher("popup_common").when(textMatches=r"(跳过|更新|我知道了|青少年模式|稍后再说)").click()
        d.watcher("popup_allow").when(textMatches=r"(允许|仅在使用中允许|始终允许|同意|确定)").click()
        d.watcher("popup_close").when(textMatches=r"(关闭|取消|知道了)").click()
    except Exception:
        return


_WATCHERS_STARTED_DEVICE_IDS: set[str] = set()


def _device_id(d) -> str:
    # For atx-agent/http connect, u2 may not expose a stable serial.
    # DO NOT fall back to repr(d) because it usually embeds a memory address and will grow
    # the global dedupe set unboundedly across reconnects.
    try:
        s = getattr(d, "serial", None)
        if s:
            return str(s)
    except Exception:
        pass
    # Stable fallback for this process.
    return str(DEVICE_ADB)


def start_watchers_global_once(d) -> None:
    """Start watcher thread at most once per device id in this process."""
    dev_id = _device_id(d)
    if dev_id in _WATCHERS_STARTED_DEVICE_IDS:
        return
    try:
        # uiautomator2 3.x watcher thread.
        d.watcher.start()
    except Exception:
        # Suppress "already started" and other watcher start warnings.
        pass
    _WATCHERS_STARTED_DEVICE_IDS.add(dev_id)


def watchers_run_safely(d) -> None:
    """Best-effort one-shot popup cleanup; keep it low-frequency."""
    try:
        d.watchers.run()
    except Exception:
        pass


def wait_home(d, timeout: float) -> bool:
    # Robust home assertion: region anchors + search entry.
    # Avoid false positives like "发消息" on detail pages or "推荐" in listing titles.
    deadline = time.time() + max(0.0, timeout)
    HOME_BOTTOM = {"闲鱼", "消息", "我的"}
    HOME_TOP = {"关注", "推荐", "新发"}
    w, h = _get_window_size(d)

    while time.time() < deadline:
        watchers_run_safely(d)
        if not _is_xianyu_foreground(d):
            _ui_wait_timeout(d, 0.2)
            continue

        nodes = _get_nodes_strings_bounds(d)
        bottom_texts = _texts_in_vertical_band(nodes, h, 0.78, 1.00)
        top_texts = _texts_in_vertical_band(nodes, h, 0.00, 0.35)
        b_hit = _band_contains_any_token(bottom_texts, HOME_BOTTOM)
        t_hit = _band_contains_any_token(top_texts, HOME_TOP)
        if b_hit >= 2 and t_hit >= 2 and _ui_has_search_entry(d):
            return True

        if HP_DEBUG_UI and int(time.time() * 10) % 15 == 0:
            _ui_debug("wait_home hits bottom=%d top=%d has_search=%s", b_hit, t_hit, _ui_has_search_entry(d))

        _ui_wait_timeout(d, 0.25)
    dump_ui_snapshot(d, stage="wait_home_timeout", reason="timeout")
    return False


def _handle_ime_picker_if_present(d) -> None:
    """Best-effort: close or select IME when the system shows an input method picker."""
    try:
        # Common labels for IME picker.
        if d(textMatches=r"(选择输入法|输入法)").exists or d(descriptionMatches=r"(选择输入法|输入法)").exists:
            _ui_debug("IME picker detected")
            dump_ui_snapshot(d, stage="ime_picker_detected", reason="")
            # Prefer ADBKeyboard if visible.
            if d(textMatches=r"ADBKeyboard").exists:
                try:
                    d(textMatches=r"ADBKeyboard").click()
                    _ui_debug("IME picker select ADBKeyboard")
                    return
                except Exception:
                    pass
            # Otherwise just close it.
            try:
                d.press("back")
                _ui_debug("IME picker close via back")
            except Exception:
                pass
    except Exception:
        return


def wait_results_ready(d, timeout: float) -> bool:
    deadline = time.time() + max(0.0, timeout)
    TOP_TOKENS = {"综合", "价格", "筛选"}
    _w, h = _get_window_size(d)
    while time.time() < deadline:
        watchers_run_safely(d)
        if not _is_xianyu_foreground(d):
            _ui_wait_timeout(d, 0.2)
            continue

        nodes = _get_nodes_strings_bounds(d)
        top_texts = _texts_in_vertical_band(nodes, h, 0.00, 0.35)
        hit = _band_contains_any_token(top_texts, TOP_TOKENS)
        if hit >= 2:
            return True
        if HP_DEBUG_UI and int(time.time() * 10) % 15 == 0:
            _ui_debug("wait_results_ready top_hits=%d", hit)
        _ui_wait_timeout(d, 0.25)
    dump_ui_snapshot(d, stage="wait_results_timeout", reason="timeout")
    return False


def wait_detail_ready(d, timeout: float) -> bool:
    deadline = time.time() + max(0.0, timeout)
    ACTION_TOKENS = {"留言", "想要", "发消息", "我想要", "去聊聊", "聊一聊", "私聊", "立即沟通"}
    _w, h = _get_window_size(d)
    while time.time() < deadline:
        watchers_run_safely(d)
        if not _is_xianyu_foreground(d):
            _ui_wait_timeout(d, 0.2)
            continue
        nodes = _get_nodes_strings_bounds(d)
        bottom_texts = _texts_in_vertical_band(nodes, h, 0.70, 1.00)
        if _band_contains_any_token(bottom_texts, ACTION_TOKENS) >= 1:
            return True
        _ui_wait_timeout(d, 0.25)
    dump_ui_snapshot(d, stage="wait_detail_timeout", reason="timeout")
    return False

def try_search_in_xianyu(d, keyword: str) -> None:
    """具备状态重置和校验的精确搜索"""
    try:
        logging.info("[UI] 正在重置状态至首页...")
        watchers_run_safely(d)
        ensure_xianyu_foreground(d, timeout=15)

        dump_ui_snapshot(d, stage="home_before_recover", reason="try_search")

        # 1) Recover to home (finite back attempts)
        for _ in range(6):
            if wait_home(d, timeout=2.5):
                break
            d.press("back")
        if not wait_home(d, timeout=3):
            logging.error("[UI] 无法回到首页")
            dump_ui_snapshot(d, stage="home_not_reached", reason="try_search")
            return

        # 2) Enter search page
        if not _ui_has_search_entry(d):
            logging.error("[UI] 找不到搜索入口")
            _dump_band_debug(d, stage="no_search_entry")
            dump_ui_snapshot(d, stage="no_search_entry", reason="try_search")
            return

        dump_ui_snapshot(d, stage="home_before_search_click", reason="try_search")
        if not _pick_and_click_search_entry(d):
            logging.error("[UI] 搜索入口点击失败（未找到可点击对象）")
            _dump_band_debug(d, stage="search_entry_click_failed")
            dump_ui_snapshot(d, stage="search_entry_click_failed", reason="try_search")
            return

        # 3) Inject keyword and submit
        dump_ui_snapshot(d, stage="after_search_entry_click", reason=keyword)
        if not input_keyword_and_submit(d, keyword):
            logging.error("[UI] 输入/提交失败")
            _dump_band_debug(d, stage="input_submit_failed")
            dump_ui_snapshot(d, stage="input_submit_failed", reason=keyword)
            return

        # 4) Assert results page is ready
        if not wait_results_ready(d, timeout=20):
            logging.error("[UI] 结果页渲染超时，可能网络卡顿或无搜索结果")
            dump_ui_snapshot(d, stage="results_timeout", reason=keyword)
            return

    except Exception:
        # Full traceback for debugging.
        logging.exception("[UI] 交互发生异常")
        _dump_band_debug(d, stage="try_search_exception")
        dump_ui_snapshot(d, stage="try_search_exception", reason="exception")

def post_raw_batch(keyword: str, raw_items: list[dict[str, object]]) -> None:
    items: list[dict[str, object]] = []
    now = _iso8601_now_local()
    for c in raw_items:
        title = str(c.get("raw_title") or "")
        price_text = str(c.get("price_text") or "")
        snippet = str(c.get("snapshot") or "")
        detail = c.get("detail") or {}
        if not isinstance(detail, dict):
            detail = {}
        full_desc = str(detail.get("full_desc") or "")
        ship_from = str(detail.get("ship_from") or "")
        zhima_credit = str(detail.get("zhima_credit") or "")

        # Prevent huge UI dumps / descriptions from ballooning RAM and bandwidth.
        if len(full_desc) > MAX_DESC_CHARS:
            full_desc = full_desc[:MAX_DESC_CHARS]

        # Full JSON payload for traceability (and LLM layer if needed)
        try:
            import json

            ui_snapshot = json.dumps(
                {
                    "raw_title": title,
                    "price_text": price_text,
                    "full_desc": full_desc,
                    "ship_from": ship_from,
                    "zhima_credit": zhima_credit,
                    "crawled_at": now,
                },
                ensure_ascii=False,
            )
        except Exception:
            ui_snapshot = snippet

        if len(ui_snapshot) > MAX_SNAPSHOT_CHARS:
            ui_snapshot = ui_snapshot[:MAX_SNAPSHOT_CHARS]
        if len(snippet) > 320:
            snippet = snippet[:320]

        if title: 
            items.append({
                "title": title, 
                "price_text": price_text, 
                "snippet": snippet, 
                "ui_snapshot": ui_snapshot, 
                "crawled_at": now,
                # Deep fields (keep minimal; avoid redundant large blobs)
                "seller_info": {
                    "location": ship_from,
                    "ship_from": ship_from,
                    "zhima_credit": zhima_credit,
                },
            })
    
    if not items:
        logging.warning("[POST] 未提取到 %s 的有效商品数据，跳过上报", keyword)
        return

    start = time.time()
    logging.info("[FLUSH] POST start keyword=%s items=%d url=%s", keyword, len(items), BACKEND_INGEST_URL)

    # IMPORTANT: never let transient backend/network issues crash the crawler loop.
    # This function should be best-effort and return normally on all failures.
    try:
        # Use context manager to ensure response is closed promptly.
        with _HTTP.post(
            BACKEND_INGEST_URL,
            json={"keyword": keyword, "platform": "XIANYU", "items": items},
            timeout=20,
        ) as resp:
            cost_ms = int((time.time() - start) * 1000)
            status = int(getattr(resp, "status_code", 0) or 0)
            logging.info("[FLUSH] POST done keyword=%s status=%d cost_ms=%d", keyword, status, cost_ms)

            # Common backpressure / throttling: slow down but do not throw.
            if status in (429, 503):
                Event().wait(2.0)
                return

            if not resp.ok:
                try:
                    body = (resp.text or "")[:240]
                except Exception:
                    body = ""
                logging.warning(
                    "[FLUSH] POST non-2xx keyword=%s status=%d body=%s",
                    keyword,
                    status,
                    body,
                )
                return
            return
    except Exception as e:
        logging.warning("[FLUSH] POST request failed keyword=%s err=%r", keyword, e)
        # Backoff a bit to avoid tight crash/retry loops when backend is down.
        Event().wait(2.0)
        return


def _ui_wait_timeout(d, seconds: float) -> None:
    """Wait for a duration without using time.sleep().

    This uses an impossible XPath to burn timeout via UiObject.wait.
    """
    try:
        d.xpath('//*[@resource-id="__hardware_pulse_never__"]').wait(timeout=max(0.0, seconds))
    except Exception:
        # As a last resort, fallback to a non-sleep wait.
        Event().wait(max(0.0, seconds))


def _is_end_reached(d) -> bool:
    # Text heuristics for end-of-list
    end_markers = [
        {"textMatches": r"(没有更多|到底了|已到底|已经到底|没有更多了)"},
    ]
    for kw in end_markers:
        try:
            if d(**kw).exists:
                return True
        except Exception:
            continue
    return False


def _swipe_and_wait_list_change(d, before_keys: set[str], timeout: float) -> bool:
    # Perform swipe then wait until visible card set changes or timeout.
    d.swipe_ext("up", scale=0.55)
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        watchers_run_safely(d)
        cards = scan_visible_cards(d)
        now_keys = { _md5(f"{c.title}|{c.price_text}") for c in cards }
        if now_keys and now_keys != before_keys:
            return True
        _ui_wait_timeout(d, 0.2)
    return False


def _extract_detail_fields(d) -> dict[str, Any]:
    nodes = _get_nodes_strings_bounds(d)
    # Trigger lazy-load in some versions (small scroll, then restore).
    if len(nodes) < 20:
        try:
            d.swipe_ext("up", scale=0.15)
            d.swipe_ext("down", scale=0.15)
        except Exception:
            pass
        nodes = _get_nodes_strings_bounds(d)
    texts = [t for t, _ in nodes]
    joined = "\n".join(texts)

    zhima = ""
    m = re.search(r"芝麻信用\s*(极好|优秀|良好|一般|较差)", joined)
    if m:
        zhima = m.group(1)

    ship_from = ""
    # Common patterns: 发货地 广东 / 广东 发货
    m2 = re.search(r"发货地\s*([\u4e00-\u9fa5]{2,8})", joined)
    if m2:
        ship_from = m2.group(1)
    else:
        m3 = re.search(r"([\u4e00-\u9fa5]{2,8})\s*发货", joined)
        if m3:
            ship_from = m3.group(1)

    # Description: pick the longest text block after filtering common UI noise.
    noise = {"综合", "价格", "筛选", "订阅", "历史搜索", "闲鱼热搜", "卖闲置", "闲鱼"}
    cand = [t.strip() for t in texts if len(t.strip()) >= 12 and t.strip() not in noise and not _PRICE_RE_WITH_CURRENCY.search(t)]
    cand.sort(key=len, reverse=True)
    full_desc = cand[0] if cand else ""

    return {
        "zhima_credit": zhima,
        "ship_from": ship_from,
        "full_desc": full_desc[:MAX_DESC_CHARS] if full_desc else "",
        "detail_text_dump": (" | ".join(texts[:80]))[:MAX_DETAIL_DUMP_CHARS],
    }


def scrape_keyword_two_phase(d, keyword: str, _price_thresholds: dict[str, float]) -> int:
    """Two-phase crawl with density-peak baseline + asymmetric semantic defense.

    Returns total items successfully POSTed.
    """
    # ---- Stage 1: dry-run calibration (radar) ----
    BIN_SIZE = 20.0
    CALIBRATION_SCREENS = 4
    CALIBRATION_MIN_SAMPLES = 30

    prices: list[float] = []
    screens = 0
    while screens < CALIBRATION_SCREENS and len(prices) < CALIBRATION_MIN_SAMPLES:
        ensure_xianyu_foreground(d, timeout=15)
        if not wait_results_ready(d, timeout=10):
            break
        cards = scan_visible_cards(d)
        for c in cards:
            if c.price_value and c.price_value > 0:
                prices.append(float(c.price_value))
        screens += 1
        try:
            d.swipe_ext("up", scale=0.55)
        except Exception:
            pass
        _ui_wait_timeout(d, 0.8)

    baseline = calculate_market_peak(prices, bin_size=BIN_SIZE)
    if baseline > 0:
        logging.info("[STAT] 锚定市场主峰基准价: ¥%.2f (samples=%d bin=%.1f)", baseline, len(prices), BIN_SIZE)
    else:
        logging.warning("[STAT] 基准价锚定失败，keyword=%s (samples=%d)", keyword, len(prices))

    # ---- Physical constraint: reset UI back to the top results ----
    try:
        try_search_in_xianyu(d, keyword)
    except Exception:
        # Best-effort; next stage will recover.
        pass
    wait_results_ready(d, timeout=20)

    # ---- Stage 2: deep dive with filtering + batch flush ----
    FLUSH_BATCH = 10
    FLUSH_INTERVAL_S = 60.0

    pending_batch: list[dict[str, object]] = []
    last_flush_ts = time.time()
    total_posted = 0

    def _flush_pending(reason: str, force: bool = False) -> None:
        nonlocal last_flush_ts, total_posted
        if not pending_batch:
            return
        if not force and len(pending_batch) < FLUSH_BATCH and (time.time() - last_flush_ts) < FLUSH_INTERVAL_S:
            return
        try:
            logging.info("[FLUSH] trigger reason=%s keyword=%s items=%d", reason, keyword, len(pending_batch))
            post_raw_batch(keyword, pending_batch)
            total_posted += len(pending_batch)
            pending_batch.clear()
            last_flush_ts = time.time()
        except Exception as e:
            # I/O isolation: never crash the crawler due to POST failures.
            logging.error("[FLUSH] POST failed keyword=%s err=%r", keyword, e)
            # Prevent unbounded growth on persistent failure.
            if len(pending_batch) > 100:
                drop = len(pending_batch) - 100
                del pending_batch[:drop]
                logging.warning("[FLUSH] pending_batch capped; dropped=%d keep=%d", drop, len(pending_batch))
            last_flush_ts = time.time()

    seen: set[str] = set()
    no_new_swipes = 0
    no_card_screens = 0

    while True:
        ensure_xianyu_foreground(d, timeout=15)
        if not wait_results_ready(d, timeout=10):
            logging.warning("[UI] 列表页锚点消失，尝试恢复")
            if not try_recover_to_results(d, timeout=10):
                try_search_in_xianyu(d, keyword)
                if not wait_results_ready(d, timeout=15):
                    break

        cards = scan_visible_cards(d)
        vis_keys = {_md5(f"{c.title}|{c.price_text}") for c in cards}
        if HP_DEBUG_UI:
            _ui_debug("list scan cards=%d keys=%d pending=%d", len(cards), len(vis_keys), len(pending_batch))

        if not cards:
            no_card_screens += 1
            if no_card_screens >= 6:
                logging.warning("[STOP] 连续多屏未识别到商品卡片，结束关键词 %s", keyword)
                break
            try:
                d.swipe_ext("up", scale=0.55)
            except Exception:
                pass
            _ui_wait_timeout(d, 0.8)
            _flush_pending(reason="no_cards")
            continue
        else:
            no_card_screens = 0

        found_new_this_screen = 0
        for c in cards:
            k = _md5(f"{c.title}|{c.price_text}")
            if k in seen:
                continue
            seen.add(k)
            found_new_this_screen += 1

            p = float(c.price_value or 0.0)
            if baseline > 0:
                ok = is_valid_pcdn_deal(c.title, p, baseline)
            else:
                # If baseline fails, do not over-filter.
                ok = p > 0

            if not ok:
                if HP_DEBUG_UI and baseline > 0 and p > baseline * 1.3:
                    _ui_debug("filter right-tail drop price=%.2f base=%.2f title=%s", p, baseline, c.title)
                continue

            logging.info("[HIT] %s %s (p=%.2f base=%.2f)", c.title, c.price_text, p, baseline)

            # Isolate per-card failures: any exception here should not kill the whole keyword loop.
            try:
                d.click(c.click_x, c.click_y)
                if not wait_detail_ready(d, timeout=15):
                    logging.warning("[UI] 详情页加载超时，回退")
                    pending_batch.append({
                        "raw_title": c.title,
                        "price_text": c.price_text,
                        "snapshot": c.snippet,
                        "detail": {},
                    })
                    try:
                        d.press("back")
                    except Exception:
                        pass
                    wait_results_ready(d, timeout=10)
                    _flush_pending(reason="detail_timeout")
                    continue

                detail = _extract_detail_fields(d)
                pending_batch.append({
                    "raw_title": c.title,
                    "price_text": c.price_text,
                    "snapshot": c.snippet,
                    "detail": detail,
                })

                try:
                    d.press("back")
                except Exception:
                    pass
                if not wait_results_ready(d, timeout=15):
                    if not try_recover_to_results(d, timeout=15):
                        try_search_in_xianyu(d, keyword)

                _flush_pending(reason="batch")
            except Exception as e:
                logging.warning("[UI] single card failed; skip. keyword=%s err=%r", keyword, e)
                # Best-effort to get back to results.
                try:
                    d.press("back")
                except Exception:
                    pass
                try_recover_to_results(d, timeout=8)
                _flush_pending(reason="card_exception")
                continue

        if found_new_this_screen == 0:
            no_new_swipes += 1
        else:
            no_new_swipes = 0

        if no_new_swipes >= 3:
            logging.info("[STOP] 连续 3 次滑动未发现新商品，结束关键词 %s", keyword)
            break
        if _is_end_reached(d):
            logging.info("[STOP] 检测到列表到底，结束关键词 %s", keyword)
            break

        _swipe_and_wait_list_change(d, before_keys=vis_keys, timeout=8)
        _flush_pending(reason="interval")

    _flush_pending(reason="final", force=True)
    return total_posted


def get_threshold_for_keyword(price_thresholds: dict[str, float], keyword: str) -> float:
    # 1) exact match
    if keyword in price_thresholds:
        try:
            return float(price_thresholds[keyword])
        except Exception:
            return 1e18
    # 2) substring match (common: thresholds keyed by model like "X99")
    for k, v in price_thresholds.items():
        try:
            if k and k in keyword:
                return float(v)
        except Exception:
            continue
    return 1e18


def try_recover_to_results(d, timeout: float) -> bool:
    # Attempt to recover by backing out until results anchors are mounted.
    # Hard cap: at most 4 back presses.
    for _ in range(4):
        watchers_run_safely(d)
        if wait_results_ready(d, timeout=1.2):
            return True
        try:
            d.press("back")
        except Exception:
            pass
        # Give the UI a short chance to settle.
        wait_results_ready(d, timeout=max(0.8, timeout / 4))
    return wait_results_ready(d, timeout=timeout)

def run_feeder_forever(device_ip: str) -> None:
    while True:
        try:
            d = connect_device(device_ip)
            register_watchers(d)
            start_watchers_global_once(d)
            start_target_app(d)

            worker_id = f"{DEVICE_ADB}"
            while True:
                keyword = fetch_next_keyword_http(worker_id)
                logging.info("[SCHED] 获取新任务: %s", keyword)

                ensure_xianyu_foreground(d, timeout=15)
                try_search_in_xianyu(d, keyword)
                thresholds = load_price_thresholds()
                posted = scrape_keyword_two_phase(d, keyword, thresholds)
                logging.info("[POST] 任务 %s 提交完成，共 %d 条数据", keyword, int(posted))
        except Exception as e:
            logging.error("[DAEMON] 发生严重异常: %s，5秒后重启设备连接...", repr(e))
            Event().wait(5)


def load_price_thresholds() -> dict[str, float]:
    if CFG is None:
        # Safety fallback; should never happen because we load config at startup.
        return {"X99": 300.0}
    return dict(CFG.thresholds)

if __name__ == "__main__":
    _setup_logging()
    # Optional: allow overriding config path via CLI arg: `python crawler_wg_xianyu.py /path/to/config.yml`
    cfg_path = CONFIG_PATH_DEFAULT
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        cfg_path = sys.argv[1].strip()
    _load_cfg_or_die(cfg_path)
    try: 
        run_feeder_forever("local")
    except KeyboardInterrupt: 
        pass