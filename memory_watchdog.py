"""Memory watchdog: monitors RSS and dumps heap via memray before killing the process.

Runs in a **daemon thread** so it fires even when the asyncio event loop is blocked
by synchronous / CPU-bound work.
"""

import logging
import os
import resource
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import memray

_logger = logging.getLogger("rentmate.memory_watchdog")

_RSS_LIMIT_BYTES = 8 * 1024 * 1024 * 1024  # 8 GB
_WARNING_RATIO = 0.75
_CHECK_INTERVAL_SECONDS = 30
_LOG_EVERY_N_CHECKS = 20  # ~10 min at 30s interval
_BACKSTOP_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB


def _get_rss_bytes() -> int:
    """Return current RSS in bytes by reading /proc/self/status."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        _logger.warning("Failed to read /proc/self/status", exc_info=True)
    return 0


def _fmt_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n} bytes"


def set_memory_backstop(*, limit_bytes: int = _BACKSTOP_BYTES) -> None:
    """Set RLIMIT_AS as a kernel-level hard ceiling."""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
        _logger.info("RLIMIT_AS set to %s", _fmt_bytes(limit_bytes))
    except Exception:
        _logger.warning("Could not set RLIMIT_AS", exc_info=True)


def _monitor_loop(
    data_dir: str,
    rss_limit_bytes: int,
    check_interval: int,
) -> None:
    """Blocking loop — meant to run in a daemon thread."""
    warning_threshold = int(rss_limit_bytes * _WARNING_RATIO)
    tracker: memray.Tracker | None = None
    tracker_path: Path | None = None
    check_count = 0

    _logger.info(
        "Memory watchdog started (thread): limit=%s, warning=%s, interval=%ds",
        _fmt_bytes(rss_limit_bytes),
        _fmt_bytes(warning_threshold),
        check_interval,
    )

    while True:
        time.sleep(check_interval)
        check_count += 1

        try:
            rss = _get_rss_bytes()
            if rss == 0:
                continue

            # --- kill phase ---
            if rss > rss_limit_bytes:
                _logger.critical(
                    "RSS %s exceeds limit %s — dumping heap and exiting",
                    _fmt_bytes(rss),
                    _fmt_bytes(rss_limit_bytes),
                )
                if tracker is not None:
                    try:
                        tracker.__exit__(None, None, None)
                        _logger.critical("Heap dump written to %s", tracker_path)
                    except Exception:
                        _logger.exception("Failed to flush memray tracker")
                else:
                    _logger.critical("No memray tracker active — no heap dump available")
                os._exit(1)

            # --- warning phase: start tracking ---
            if rss > warning_threshold and tracker is None:
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                tracker_path = Path(data_dir) / f"heap_{ts}.bin"
                try:
                    tracker = memray.Tracker(str(tracker_path))
                    tracker.__enter__()
                    _logger.warning(
                        "RSS %s > %s — memray tracking started → %s",
                        _fmt_bytes(rss),
                        _fmt_bytes(warning_threshold),
                        tracker_path,
                    )
                except Exception:
                    _logger.exception("Failed to start memray tracker")
                    tracker = None
                    tracker_path = None

            # --- back to normal: stop tracking, clean up ---
            if rss <= warning_threshold and tracker is not None:
                try:
                    tracker.__exit__(None, None, None)
                    if tracker_path and tracker_path.exists():
                        tracker_path.unlink()
                    _logger.info("RSS dropped below warning — memray tracking stopped, dump deleted")
                except Exception:
                    _logger.exception("Failed to stop memray tracker")
                tracker = None
                tracker_path = None

            # --- periodic logging ---
            if rss > warning_threshold:
                _logger.warning("RSS %s (%.0f%% of limit)", _fmt_bytes(rss), rss / rss_limit_bytes * 100)
            elif check_count % _LOG_EVERY_N_CHECKS == 0:
                _logger.info("RSS %s (%.0f%% of limit)", _fmt_bytes(rss), rss / rss_limit_bytes * 100)

        except Exception:
            _logger.exception("Memory monitor check failed")


def start_memory_monitor(
    data_dir: str,
    *,
    rss_limit_bytes: int = _RSS_LIMIT_BYTES,
    check_interval: int = _CHECK_INTERVAL_SECONDS,
) -> threading.Thread:
    """Launch the memory monitor in a daemon thread and return the thread handle."""
    t = threading.Thread(
        target=_monitor_loop,
        args=(data_dir, rss_limit_bytes, check_interval),
        daemon=True,
        name="memory-watchdog",
    )
    t.start()
    return t
