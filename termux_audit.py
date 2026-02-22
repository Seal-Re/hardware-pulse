import os
import re


SKIP_DIRS = {
    "node_modules",
    "venv",
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    ".settings",
    "target",
    "build",
    "dist",
    "out",
    "bin",
    "obj",
    "logs",
    ".cache",
    "tmp",
    "temp",
}

INCLUDE_EXTS = {".sh", ".yml", ".yaml", ".env", ".py", ".sql"}
INCLUDE_BASENAMES = {".env.example"}


PATTERNS = {
    "forbidden_privilege_cmd": re.compile(r"(?i)\b(sudo|apt-get|\bsu\b)\b"),
    "docker_residue": re.compile(
        r"(?i)(host\.docker\.internal|host\s*:\s*db|jdbc:postgresql://db\b|postgresql://db\b|redis://redis\b|\bredis\b\s*:\s*6379)"
    ),
    # Enforce loopback for DB/Redis connection strings in runtime configs.
    # Note: these patterns are applied on raw text and may match regex strings inside code;
    # we therefore only use them for *config-like* files in code below.
    "non_loopback_pg_jdbc": re.compile(r"(?i)jdbc:postgresql://(?!127\.0\.0\.1|localhost)[^\s\"']+"),
    "non_loopback_redis_uri": re.compile(r"(?i)redis://(?!127\.0\.0\.1|localhost)[^\s\"']+"),
    # u2 must connect to adb forwarded localhost:5555.
    "u2_connect_not_127_0_0_1_5555": re.compile(r"(?i)u2\.connect\(\s*(?![\"']127\.0\.0\.1:5555[\"'])"),
}


ABS_PATH_MARKER = re.compile(r"(?i)(^|[^\w$])/(var|etc|usr)/")


def _looks_like_forbidden_abs_path_line(line: str) -> bool:
    # Allow Termux style paths that are rooted at $PREFIX.
    if "$PREFIX" in line or "${PREFIX" in line:
        return False
    if "$PREFIX_PATH" in line or "${PREFIX_PATH" in line:
        return False
    return bool(ABS_PATH_MARKER.search(line))


TIMESCALE_MARKERS = re.compile(
    r"(?i)\btimescaledb\b|\bcreate_hypertable\s*\(",
)
TIMESCALE_GUARDS = re.compile(
    # Any of these indicates a downgrade/optional mechanism.
    r"(?i)pg_available_extensions|pg_proc|proname\s*=\s*'create_hypertable'|RAISE\s+NOTICE",
)


def _is_config_like(path: str) -> bool:
    # Only flag connection-string host issues in config-like files.
    base = os.path.basename(path)
    _, ext = os.path.splitext(base)
    return ext in {".yml", ".yaml", ".env"} or base in {".env.example"}


def iter_relevant_files(root: str = "."):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn == "termux_audit.py":
                # Don't self-report regex strings as violations.
                continue
            if fn in INCLUDE_BASENAMES:
                yield os.path.join(dirpath, fn)
                continue
            _, ext = os.path.splitext(fn)
            if ext in INCLUDE_EXTS:
                yield os.path.join(dirpath, fn)


def main() -> int:
    crlf_files = []
    findings = []

    for path in iter_relevant_files("."):
        try:
            data = open(path, "rb").read()
        except Exception as e:
            findings.append((path, "read_error", str(e)))
            continue

        if b"\r\n" in data:
            crlf_files.append(path)

        text = data.decode("utf-8", errors="ignore")

        # Absolute-path check: forbid /var, /etc, /usr unless rooted at $PREFIX.
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if _looks_like_forbidden_abs_path_line(line):
                findings.append((path, "forbidden_linux_abs_path", "match"))
                break

        # SQL downgrade check: allow TimescaleDB usage only when guarded.
        if path.lower().endswith(".sql") and TIMESCALE_MARKERS.search(text):
            if not TIMESCALE_GUARDS.search(text):
                findings.append((path, "timescale_unguarded", "match"))
        for key, rgx in PATTERNS.items():
            if key in {"non_loopback_pg_jdbc", "non_loopback_redis_uri"} and not _is_config_like(path):
                continue
            if rgx.search(text):
                findings.append((path, key, "match"))

    print("CRLF_FILES")
    for p in crlf_files:
        print(p)
    print("FINDINGS")
    for p, k, msg in findings:
        print(f"{p}\t{k}\t{msg}")

    # Non-zero if we found anything actionable.
    return 1 if (crlf_files or findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
