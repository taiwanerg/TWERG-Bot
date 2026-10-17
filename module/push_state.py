import json
import os
import time
from datetime import datetime, timezone

STATE_DIR = "data"
CATCHUP_SECONDS = 2 * 3600


def _path(name):
    return os.path.join(STATE_DIR, f"push_state_{name}.json")


def load(name):
    try:
        with open(_path(name), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(name, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _path(name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def fresh(epoch, limit=CATCHUP_SECONDS):
    return epoch is None or time.time() - epoch <= limit


def fresh_utc_stamp(stamp, limit=CATCHUP_SECONDS):
    try:
        epoch = datetime.strptime(str(stamp)[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return True
    return fresh(epoch, limit)
