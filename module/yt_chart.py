"""YouTube 直播觀看人數的本機歷史資料與深色圖表。"""

import io
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

MPL_CONFIG_PATH = Path("data") / ".matplotlib"
MPL_CONFIG_PATH.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CONFIG_PATH.resolve()))
os.environ.setdefault("XDG_CACHE_HOME", str(MPL_CONFIG_PATH.resolve()))

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


DATABASE_PATH = Path("data") / "youtube_viewers.db"
HISTORY_RETENTION_DAYS = 7
CHART_HISTORY_HOURS = 12
TAIPEI_TZ = timezone(timedelta(hours=8))


def _create_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS viewer_history (
                observed_at INTEGER PRIMARY KEY,
                viewers INTEGER NOT NULL CHECK (viewers >= 0)
            )
        """)


def record_viewer_count(viewers: int, observed_at: float | None = None):
    """保存一筆有效觀看人數；同一秒的資料以最新值覆蓋。"""
    _create_database()
    timestamp = int(observed_at if observed_at is not None else datetime.now().timestamp())
    cutoff = timestamp - HISTORY_RETENTION_DAYS * 24 * 60 * 60
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute("INSERT OR REPLACE INTO viewer_history VALUES (?, ?)", (timestamp, viewers))
        connection.execute("DELETE FROM viewer_history WHERE observed_at < ?", (cutoff,))


def get_viewer_history(hours: int = CHART_HISTORY_HOURS, now: float | None = None):
    """取得由舊至新的觀看人數資料。"""
    _create_database()
    timestamp = int(now if now is not None else datetime.now().timestamp())
    cutoff = timestamp - hours * 60 * 60
    with sqlite3.connect(DATABASE_PATH) as connection:
        return connection.execute(
            "SELECT observed_at, viewers FROM viewer_history WHERE observed_at >= ? ORDER BY observed_at", (cutoff,)
        ).fetchall()


def render_viewer_chart(history: list[tuple[int, int]]) -> bytes | None:
    """產生適合 Discord 顯示的 PNG；資料不足時不產生圖表。"""
    if len(history) < 2:
        return None

    times = [datetime.fromtimestamp(timestamp, TAIPEI_TZ) for timestamp, _ in history]
    viewers = [value for _, value in history]
    minimum, maximum = min(viewers), max(viewers)
    padding = max((maximum - minimum) * 0.18, maximum * 0.04, 1)
    lower, upper = max(0, minimum - padding), maximum + padding

    figure, axis = plt.subplots(figsize=(8.8, 4.1), dpi=150)
    figure.patch.set_facecolor("#101010")
    axis.set_facecolor("#101010")
    axis.fill_between(times, viewers, lower, color="#1d3246", alpha=0.88)
    axis.plot(times, viewers, color="#4d97e9", linewidth=2)
    axis.set_ylim(lower, upper)
    axis.grid(axis="y", color="#505050", linewidth=0.85, alpha=0.85)
    axis.set_axisbelow(True)
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.tick_params(axis="both", colors="#f1f1f1", labelsize=10, length=0, pad=8)
    axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=5))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%I:%M %p", tz=TAIPEI_TZ))
    axis.yaxis.set_major_locator(plt.MaxNLocator(nbins=5, integer=True))
    axis.margins(x=0)
    figure.subplots_adjust(left=0.08, right=0.94, top=0.96, bottom=0.2)

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    plt.close(figure)
    return buffer.getvalue()
