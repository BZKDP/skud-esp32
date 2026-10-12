# -*- coding: utf-8 -*-
"""SCUD: HTTP-канал между ESP32 и веб-кнопкой.

ESP32 поллит /api/poll каждые ~1 сек и забирает команду.
Веб-кнопка (или curl) ставит pending_open=True через /api/open (X-Pin) или /api/admin/open (cookie).

Состояние полностью в памяти. БД нет.
"""

import os
import time
import asyncio
import secrets
import hmac
import hashlib
import json
import urllib.request
import urllib.parse
import base64 as _base64
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import s3lite  # лёгкий S3-клиент (записи камер)
except Exception:
    s3lite = None
from base64 import urlsafe_b64encode, urlsafe_b64decode
from pathlib import Path

from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse, Response, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


# ===== Конфиг =====
HERE = Path(__file__).resolve().parent
PUBLIC_DIR = HERE / "public"
ACCESS_STORE_PATH = HERE / "access_store.json"
PEOPLE_FILE = HERE / "people_daily.json"   # статистика людей по дням (переживает рестарт)

# Секреты берутся из окружения (см. .env.example). Пустые значения по умолчанию —
# задайте реальные перед запуском.
# DEVICE_TOKEN — для ESP32, кладётся в прошивку, шлётся в query: ?token=...
# DOOR_PIN     — для curl/внешних вызовов /api/open, шлётся в заголовке X-Pin
# ADMIN_PASSWORD — для входа в веб-кнопку
DEVICE_TOKEN = os.getenv("SKUD_DEVICE_TOKEN", "")
DOOR_PIN = os.getenv("SKUD_DOOR_PIN", "")
ADMIN_PASSWORD = os.getenv("SKUD_ADMIN_PASSWORD", "")
SESSION_SECRET = os.getenv("SKUD_SESSION_SECRET", secrets.token_hex(32))
# Токен для ESP32-CAM: она шлёт JPEG-кадры POST'ом /api/cam/push?token=...
CAM_PUSH_TOKEN = os.getenv("SKUD_CAM_TOKEN", "")

# Яндекс.Карты JS API — ключи задаются через окружение (можно несколько с авто-переключением).
MAPS_KEYS = [
    os.getenv("SKUD_YMAPS_KEY1", ""),
    os.getenv("SKUD_YMAPS_KEY2", ""),
]
MAPS_PANO_LIMIT = int(os.getenv("SKUD_YMAPS_PANO_LIMIT", "100"))   # ~100 панорам/день на ключ

# Токен для счётчика людей (Pi шлёт пересечения линии)
PEOPLE_TOKEN = os.getenv("SKUD_PEOPLE_TOKEN", "")

OPEN_DURATION_MS = 5000     # сколько держать реле открытым (для ESP32)
MAX_HISTORY = 20            # сколько последних событий хранить в памяти
MAX_ACCESS_EVENTS = 50      # сколько последних событий с клавиатуры/карт хранить в памяти
DELIVERY_TIMEOUT_S = 10.0   # сколько ждём подтверждения от poll после нажатия (был 4с) — переживаем микро-подвисы
POLL_HEALTHY_S = 5.0        # до этого считаем связь свежей
POLL_ONLINE_S = 15.0        # до этого считаем устройство доступным, но связь может быть слабой
POLL_OFFLINE_S = 30.0       # после этого показываем явный offline

BASE_PREFIX = "/skud"       # сайт за прокси на этом subpath; API роутится без префикса (он добавляется прокси)
ADMIN_COOKIE_PATH = os.getenv("SKUD_COOKIE_PATH", "/")
ALLOW_PUBLIC_WEB_OPEN = os.getenv("SKUD_ALLOW_PUBLIC_WEB_OPEN", "0") == "1"


# ===== Состояние (в памяти) =====
class State:
    pending_open: bool = False                 # дверь 1
    pending_open_expires_at: float | None = None
    pending_open2: bool = False                # дверь 2
    pending_open2_expires_at: float | None = None
    pending_open3: bool = False                # дверь 3 (замок ячейки)
    pending_open3_expires_at: float | None = None
    last_poll_at: float | None = None         # epoch
    last_open_queued_at: float | None = None  # epoch, когда последний раз поставили команду (дверь 1)
    last_open_consumed_at: float | None = None  # epoch, когда команда «съедена» поллом (дверь 1)
    last_open2_queued_at: float | None = None   # дверь 2
    last_open2_consumed_at: float | None = None  # дверь 2
    last_open3_queued_at: float | None = None   # дверь 3
    last_open3_consumed_at: float | None = None  # дверь 3
    pending_open4: bool = False                  # дверь 4 (дальняя)
    pending_open4_expires_at: float | None = None
    last_open4_queued_at: float | None = None    # дверь 4
    last_open4_consumed_at: float | None = None  # дверь 4
    open_dur: dict = {}                          # door -> длительность открытия, мс (для «открыть на минуту»)
    last_device: dict = {}
    history: list[dict] = []                  # последние события
    access_events: list[dict] = []            # последние попытки PIN/RFID
    boot_at: float = time.time()
    cam_frames: dict = {}                     # cam_name -> {"jpeg": bytes, "at": float} (ESP32-CAM)
    maps_usage: dict = {}                     # учёт лимита панорам Яндекса по ключам (сброс в полночь МСК)
    people_events: list = []                  # epoch-таймстампы появлений людей (роллинг, ~25ч)
    people_daily: dict = {}                    # {"YYYY-MM-DD": кол-во} — на диске, переживает рестарт


def add_history(kind: str, source: str, ip: str | None = None):
    State.history.insert(0, {
        "at": time.time(),
        "kind": kind,         # 'open_queued' | 'open_consumed' | 'poll'  (poll не пишем, чтобы не засрать)
        "source": source,     # 'web' | 'api' | 'esp32'
        "ip": ip,
    })
    del State.history[MAX_HISTORY:]


def _empty_access_store() -> dict:
    return {"pins": [], "cards": []}


def load_access_store() -> dict:
    if not ACCESS_STORE_PATH.exists():
        return _empty_access_store()
    try:
        data = json.loads(ACCESS_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _empty_access_store()
    if not isinstance(data, dict):
        return _empty_access_store()
    data.setdefault("pins", [])
    data.setdefault("cards", [])
    if not isinstance(data["pins"], list):
        data["pins"] = []
    if not isinstance(data["cards"], list):
        data["cards"] = []
    return data


def save_access_store(data: dict):
    data = {
        "pins": list(data.get("pins", [])),
        "cards": list(data.get("cards", [])),
    }
    tmp = ACCESS_STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ACCESS_STORE_PATH)


def normalize_pin(code: object) -> str:
    pin = str(code or "").strip()
    if not pin.isdigit() or not 3 <= len(pin) <= 12:
        raise ValueError("PIN должен содержать 3-12 цифр")
    return pin


def normalize_card_code(code: object) -> str:
    raw = str(code or "").strip().upper()
    if raw.startswith("0X"):
        raw = raw[2:]
    cleaned = "".join(ch for ch in raw if ch not in " :-_\t\r\n")
    if not cleaned:
        raise ValueError("код карты пустой")
    if not all(ch in "0123456789ABCDEF" for ch in cleaned):
        raise ValueError("код карты должен быть decimal или hex")
    return cleaned


def card_aliases(code: object, raw_hex: object | None = None, bits: int | None = None) -> list[str]:
    aliases: list[str] = []

    def add(value: object):
        try:
            normalized = normalize_card_code(value)
        except ValueError:
            return
        if normalized and normalized not in aliases:
            aliases.append(normalized)
        try:
            if all(ch in "0123456789" for ch in normalized):
                as_hex = format(int(normalized, 10), "X")
                if as_hex not in aliases:
                    aliases.append(as_hex)
            else:
                as_dec = str(int(normalized, 16))
                if as_dec not in aliases:
                    aliases.append(as_dec)
        except ValueError:
            pass

    def add_int(value: int):
        if value < 0:
            return
        add(str(value))
        add(format(value, "X"))

    add(code)
    if raw_hex:
        add(raw_hex)

    raw_value: int | None = None
    if raw_hex not in (None, ""):
        try:
            raw_value = int(normalize_card_code(raw_hex), 16)
        except ValueError:
            raw_value = None
    if raw_value is None and code not in (None, ""):
        try:
            normalized = normalize_card_code(code)
            if all(ch in "0123456789" for ch in normalized):
                raw_value = int(normalized, 10)
            else:
                raw_value = int(normalized, 16)
        except ValueError:
            raw_value = None

    if raw_value is not None and bits:
        # Wiegand-26/34 include parity bits at the beginning and end.
        # Many EM-Marine cards have their printed number in this payload, not in the raw frame.
        if bits == 26:
            payload24 = (raw_value >> 1) & 0xFFFFFF
            add_int(payload24)
            add_int(payload24 & 0xFFFF)
        elif bits == 34:
            payload32 = (raw_value >> 1) & 0xFFFFFFFF
            add_int(payload32)
            add_int(payload32 & 0xFFFF)
        elif 24 <= bits <= 40:
            payload = (raw_value >> 1) & ((1 << min(bits - 2, 32)) - 1)
            add_int(payload)
            add_int(payload & 0xFFFFFF)
            add_int(payload & 0xFFFF)
    return aliases


def card_payload_value(code: object, raw_hex: object | None, bits: int | None) -> int | None:
    raw_value: int | None = None
    if raw_hex not in (None, ""):
        try:
            raw_value = int(normalize_card_code(raw_hex), 16)
        except ValueError:
            raw_value = None
    if raw_value is None and code not in (None, ""):
        try:
            normalized = normalize_card_code(code)
            raw_value = int(normalized, 10) if normalized.isdigit() else int(normalized, 16)
        except ValueError:
            raw_value = None
    if raw_value is None:
        return None
    if bits == 26:
        return (raw_value >> 1) & 0xFFFFFF
    if bits == 34:
        return (raw_value >> 1) & 0xFFFFFFFF
    return None


def card_entry_values(entry: dict) -> list[int]:
    values: list[int] = []
    candidates = [entry.get("match_code"), entry.get("code_hint")]
    for candidate in candidates:
        if not candidate or "..." in str(candidate):
            continue
        try:
            normalized = normalize_card_code(candidate)
        except ValueError:
            continue
        try:
            value = int(normalized, 10) if normalized.isdigit() else int(normalized, 16)
        except ValueError:
            continue
        if value not in values:
            values.append(value)
    return values


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def fuzzy_card_match(entry: dict, payload: int | None, bits: int | None) -> bool:
    if payload is None or bits not in (26, 34):
        return False
    # Long Wiegand lines can flip a few bits. Keep this narrow and require the low byte
    # to match, so this only rescues noisy reads of the same EM-Marine card.
    max_distance = 4
    mask = 0xFFFFFFFF if bits == 34 else 0xFFFFFF
    for value in card_entry_values(entry):
        comparable = value & mask
        if (payload & 0xFF) != (comparable & 0xFF):
            continue
        if hamming_distance(payload, comparable) <= max_distance:
            return True
    return False


def access_hash(kind: str, code: str) -> str:
    normalized = code.strip().upper() if kind == "card" else code.strip()
    payload = f"{kind}:{normalized}"
    return hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def code_hint(kind: str, code: str) -> str:
    if kind == "pin":
        return "*" * max(3, len(code))
    if len(code) <= 8:
        return code
    return f"{code[:4]}...{code[-4:]}"


def parse_expires_at(body: dict) -> float | None:
    value = body.get("expires_at")
    if value not in (None, ""):
        try:
            expires_at = float(value)
            return expires_at if expires_at > 0 else None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="bad expires_at")

    ttl = body.get("ttl_minutes")
    if ttl in (None, ""):
        ttl = body.get("expires_in_min")
    if ttl in (None, ""):
        return None
    try:
        minutes = int(ttl)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="bad ttl_minutes")
    if minutes <= 0:
        return None
    if minutes > 60 * 24 * 365:
        raise HTTPException(status_code=400, detail="ttl_minutes too large")
    return time.time() + minutes * 60


def sanitize_access_entry(entry: dict) -> dict:
    now = time.time()
    expires_at = entry.get("expires_at")
    used_at = entry.get("used_at")
    enabled = bool(entry.get("enabled", True))
    expired = bool(expires_at and expires_at <= now)
    used = bool(used_at)
    return {
        "id": entry.get("id"),
        "kind": entry.get("kind"),
        "label": entry.get("label") or "",
        "code_hint": entry.get("code_hint") or "",
        "enabled": enabled,
        "one_time": bool(entry.get("one_time", False)),
        "created_at": entry.get("created_at"),
        "expires_at": expires_at,
        "used_at": used_at,
        "expired": expired,
        "usable": enabled and not expired and not used,
    }


def entry_reject_reason(entry: dict, now: float) -> str | None:
    if not entry.get("enabled", True):
        return "disabled"
    expires_at = entry.get("expires_at")
    if expires_at and expires_at <= now:
        return "expired"
    if entry.get("one_time") and entry.get("used_at"):
        return "used"
    return None


def add_access_event(
    kind: str,
    result: str,
    request: Request,
    code: str = "",
    raw_hex: str | None = None,
    bits: int | None = None,
    label: str = "",
    reason: str = "",
):
    event = {
        "at": time.time(),
        "kind": kind,
        "result": result,
        "bits": bits,
        "code_hint": code_hint(kind, code) if code else "",
        "raw_hex": raw_hex if kind == "card" else None,
        "code": code if kind == "card" else None,
        "label": label,
        "reason": reason,
        "ip": client_ip(request),
    }
    State.access_events.insert(0, event)
    del State.access_events[MAX_ACCESS_EVENTS:]


def _check_bucket(store: dict, kind: str, bucket: str, aliases: list[str], now: float) -> tuple[dict | None, str | None]:
    matched_inactive_reason: str | None = None
    hashes = {access_hash(kind, alias) for alias in aliases}
    for entry in store.get(bucket, []):
        if entry.get("code_hash") not in hashes:
            continue
        reason = entry_reject_reason(entry, now)
        if reason:
            matched_inactive_reason = reason
            continue
        return entry, None
    return None, matched_inactive_reason


def _check_fuzzy_card(store: dict, payload: int | None, bits: int | None, now: float) -> tuple[dict | None, str | None]:
    matched_inactive_reason: str | None = None
    for entry in store.get("cards", []):
        if not fuzzy_card_match(entry, payload, bits):
            continue
        reason = entry_reject_reason(entry, now)
        if reason:
            matched_inactive_reason = reason
            continue
        return entry, None
    return None, matched_inactive_reason


def check_access_rule(kind: str, code: str, raw_hex: str | None, request: Request, bits: int | None) -> dict:
    now = time.time()
    store = load_access_store()
    aliases = [code] if kind == "pin" else card_aliases(code, raw_hex, bits)
    if not aliases:
        add_access_event(kind, "denied", request, code, raw_hex, bits, reason="bad_code")
        return {"allowed": False, "reason": "bad_code"}

    matched_inactive_reason: str | None = None
    checks: list[tuple[str, str, list[str]]] = [("pin", "pins", aliases if kind == "pin" else [])]
    if kind == "card":
        checks.append(("card", "cards", aliases))
        payload = card_payload_value(code, raw_hex, bits)
        fuzzy_entry, fuzzy_reason = _check_fuzzy_card(store, payload, bits, now)
        if fuzzy_reason:
            matched_inactive_reason = fuzzy_reason
        if fuzzy_entry:
            if fuzzy_entry.get("one_time"):
                fuzzy_entry["used_at"] = now
                save_access_store(store)
            label = fuzzy_entry.get("label") or ""
            add_access_event("card", "granted", request, code, raw_hex, bits, label=label, reason="fuzzy")
            return {
                "allowed": True,
                "reason": "granted",
                "label": label,
                "entry_id": fuzzy_entry.get("id"),
                "open_ms": OPEN_DURATION_MS,
            }
        pin_aliases = [alias for alias in aliases if alias.isdigit() and 3 <= len(alias) <= 12]
        checks.append(("pin", "pins", pin_aliases))

    for entry_kind, bucket, bucket_aliases in checks:
        if not bucket_aliases:
            continue
        entry, inactive_reason = _check_bucket(store, entry_kind, bucket, bucket_aliases, now)
        if inactive_reason:
            matched_inactive_reason = inactive_reason
        if not entry:
            continue
        if entry.get("one_time"):
            entry["used_at"] = now
            save_access_store(store)

        label = entry.get("label") or ""
        add_access_event(entry_kind, "granted", request, code, raw_hex, bits, label=label)
        return {
            "allowed": True,
            "reason": "granted",
            "label": label,
            "entry_id": entry.get("id"),
            "open_ms": OPEN_DURATION_MS,
        }

    reason = matched_inactive_reason or "not_found"
    add_access_event(kind, "denied", request, code, raw_hex, bits, reason=reason)
    return {"allowed": False, "reason": reason}


def add_access_entry(kind: str, body: dict) -> dict:
    if kind == "pin":
        code = normalize_pin(body.get("code"))
        bucket = "pins"
    else:
        aliases = card_aliases(body.get("code"), body.get("raw_hex"), body.get("bits"))
        if not aliases:
            raise ValueError("код карты пустой")
        code = aliases[0]
        bucket = "cards"

    store = load_access_store()
    entry_hash = access_hash(kind, code)
    for entry in store.get(bucket, []):
        if entry.get("code_hash") == entry_hash:
            raise HTTPException(status_code=409, detail="такой доступ уже есть")

    now = time.time()
    entry = {
        "id": secrets.token_hex(6),
        "kind": kind,
        "label": str(body.get("label") or "").strip()[:80],
        "code_hash": entry_hash,
        "code_hint": code_hint(kind, code),
        "match_code": code if kind == "card" else None,
        "enabled": bool(body.get("enabled", True)),
        "one_time": bool(body.get("one_time", False)),
        "created_at": now,
        "expires_at": parse_expires_at(body),
        "used_at": None,
    }
    store.setdefault(bucket, []).insert(0, entry)
    save_access_store(store)
    return sanitize_access_entry(entry)


# ===== HMAC-подписанные cookies =====
def _sign(value: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(payload: dict) -> str:
    import json
    raw = urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode()).rstrip(b"=").decode()
    return f"{raw}.{_sign(raw)}"


def parse_token(tok: str | None) -> dict | None:
    if not tok or "." not in tok:
        return None
    import json
    raw, sig = tok.split(".", 1)
    if not hmac.compare_digest(sig, _sign(raw)):
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        return json.loads(urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return None


def is_admin(request: Request) -> bool:
    tok = request.cookies.get("skud_adm")
    data = parse_token(tok) if tok else None
    return bool(data and data.get("admin") is True)


# ===== Утилиты =====
def client_ip(request: Request) -> str:
    # за nginx/stag-прокси
    xf = request.headers.get("x-forwarded-for")
    if xf:
        return xf.split(",")[0].strip()
    return request.client.host if request.client else "?"


def device_status() -> dict:
    now = time.time()
    last_poll_ago = (now - State.last_poll_at) if State.last_poll_at else None
    if last_poll_ago is None:
        device_state = "unknown"
    elif last_poll_ago < POLL_HEALTHY_S:
        device_state = "online"
    elif last_poll_ago < POLL_ONLINE_S:
        device_state = "weak"
    elif last_poll_ago < POLL_OFFLINE_S:
        device_state = "stale"
    else:
        device_state = "offline"

    return {
        "last_poll_at": State.last_poll_at,
        "last_poll_ago_s": round(last_poll_ago, 1) if last_poll_ago is not None else None,
        "device_online": last_poll_ago is not None and last_poll_ago < POLL_ONLINE_S,
        "device_state": device_state,
        "poll_thresholds_s": {
            "healthy": POLL_HEALTHY_S,
            "online": POLL_ONLINE_S,
            "offline": POLL_OFFLINE_S,
        },
        "device": State.last_device,
    }


def _door_consumed_at(door: int):
    if door == 2:
        return State.last_open2_consumed_at
    if door == 3:
        return State.last_open3_consumed_at
    if door == 4:
        return State.last_open4_consumed_at
    return State.last_open_consumed_at


async def wait_delivery(queued_at: float, door: int = 1) -> bool:
    deadline = time.time() + DELIVERY_TIMEOUT_S
    while time.time() < deadline:
        consumed = _door_consumed_at(door)
        if consumed and consumed >= queued_at:
            return True
        await asyncio.sleep(0.1)
    consumed = _door_consumed_at(door)
    return bool(consumed and consumed >= queued_at)


async def queue_open_and_wait(source: str, request: Request, door: int = 1, dur: int | None = None):
    queued_at = time.time()
    State.open_dur[door] = int(dur) if dur else OPEN_DURATION_MS
    if door == 2:
        State.pending_open2 = True
        State.pending_open2_expires_at = queued_at + DELIVERY_TIMEOUT_S
        State.last_open2_queued_at = queued_at
    elif door == 3:
        State.pending_open3 = True
        State.pending_open3_expires_at = queued_at + DELIVERY_TIMEOUT_S
        State.last_open3_queued_at = queued_at
    elif door == 4:
        State.pending_open4 = True
        State.pending_open4_expires_at = queued_at + DELIVERY_TIMEOUT_S
        State.last_open4_queued_at = queued_at
    else:
        State.pending_open = True
        State.pending_open_expires_at = queued_at + DELIVERY_TIMEOUT_S
        State.last_open_queued_at = queued_at
    add_history("open_queued", source, client_ip(request))

    delivered = await wait_delivery(queued_at, door)
    body = {
        "queued": True,
        "delivered": delivered,
        "door": door,
        "pending_open": State.pending_open,
        "pending_open2": State.pending_open2,
        "pending_open3": State.pending_open3,
        "pending_open4": State.pending_open4,
        "last_open_queued_at": State.last_open_queued_at,
        "last_open_consumed_at": State.last_open_consumed_at,
        **device_status(),
    }
    if delivered:
        return JSONResponse(body)

    if door == 2:
        if State.pending_open2 and State.last_open2_queued_at == queued_at:
            State.pending_open2 = False
            State.pending_open2_expires_at = None
    elif door == 3:
        if State.pending_open3 and State.last_open3_queued_at == queued_at:
            State.pending_open3 = False
            State.pending_open3_expires_at = None
    elif door == 4:
        if State.pending_open4 and State.last_open4_queued_at == queued_at:
            State.pending_open4 = False
            State.pending_open4_expires_at = None
    else:
        if State.pending_open and State.last_open_queued_at == queued_at:
            State.pending_open = False
            State.pending_open_expires_at = None

    body["pending_open"] = State.pending_open
    body["pending_open2"] = State.pending_open2
    body["pending_open3"] = State.pending_open3
    body["pending_open4"] = State.pending_open4
    body["error"] = "ESP32 не забрала команду за 4 секунды"
    return JSONResponse(body, status_code=504)


# ===== App =====
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def no_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/api/health")
async def health():
    return {"ok": True, "uptime_s": round(time.time() - State.boot_at, 1), **device_status()}


@app.get("/api/poll")
async def poll(
    token: str,
    request: Request,
    uptime_s: int | None = None,
    rssi: int | None = None,
    heap: int | None = None,
    fails: int | None = None,
):
    if token != DEVICE_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    State.last_poll_at = time.time()
    State.last_device = {
        "ip": client_ip(request),
        "uptime_s": uptime_s,
        "rssi": rssi,
        "heap": heap,
        "fails": fails,
    }
    # Дверь 1 имеет приоритет, затем дверь 2 (обе обслуживаются за соседние поллы)
    if State.pending_open:
        if State.pending_open_expires_at and time.time() > State.pending_open_expires_at:
            State.pending_open = False
            State.pending_open_expires_at = None
            add_history("open_expired", "esp32", client_ip(request))
        else:
            State.pending_open = False
            State.pending_open_expires_at = None
            State.last_open_consumed_at = time.time()
            add_history("open_consumed", "esp32", client_ip(request))
            return {"cmd": "open", "door": 1, "duration_ms": State.open_dur.get(1, OPEN_DURATION_MS)}

    if State.pending_open2:
        if State.pending_open2_expires_at and time.time() > State.pending_open2_expires_at:
            State.pending_open2 = False
            State.pending_open2_expires_at = None
            add_history("open_expired", "esp32", client_ip(request))
        else:
            State.pending_open2 = False
            State.pending_open2_expires_at = None
            State.last_open2_consumed_at = time.time()
            add_history("open_consumed", "esp32", client_ip(request))
            return {"cmd": "open", "door": 2, "duration_ms": State.open_dur.get(2, OPEN_DURATION_MS)}

    if State.pending_open3:
        if State.pending_open3_expires_at and time.time() > State.pending_open3_expires_at:
            State.pending_open3 = False
            State.pending_open3_expires_at = None
            add_history("open_expired", "esp32", client_ip(request))
        else:
            State.pending_open3 = False
            State.pending_open3_expires_at = None
            State.last_open3_consumed_at = time.time()
            add_history("open_consumed", "esp32", client_ip(request))
            return {"cmd": "open", "door": 3, "duration_ms": State.open_dur.get(3, OPEN_DURATION_MS)}

    if State.pending_open4:
        if State.pending_open4_expires_at and time.time() > State.pending_open4_expires_at:
            State.pending_open4 = False
            State.pending_open4_expires_at = None
            add_history("open_expired", "esp32", client_ip(request))
        else:
            State.pending_open4 = False
            State.pending_open4_expires_at = None
            State.last_open4_consumed_at = time.time()
            add_history("open_consumed", "esp32", client_ip(request))
            return {"cmd": "open", "door": 4, "duration_ms": State.open_dur.get(4, OPEN_DURATION_MS)}

    return {"cmd": "idle"}


@app.post("/api/open")
async def open_door(request: Request, door: int = 1):
    pin = request.headers.get("x-pin", "")
    if pin != DOOR_PIN:
        raise HTTPException(status_code=401, detail="bad pin")
    if door not in (1, 2, 3, 4):
        raise HTTPException(status_code=422, detail="bad door")
    return await queue_open_and_wait("api", request, door)


@app.post("/api/device/access")
async def device_access(token: str, request: Request):
    if token != DEVICE_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="bad json")

    kind = str(body.get("kind") or "").strip().lower()
    bits = body.get("bits")
    try:
        bits = int(bits) if bits not in (None, "") else None
    except (TypeError, ValueError):
        bits = None
    raw_hex = str(body.get("raw_hex") or "").strip() or None

    try:
        if kind == "pin":
            code = normalize_pin(body.get("code"))
        elif kind == "card":
            aliases = card_aliases(body.get("code"), raw_hex, bits)
            if not aliases:
                raise ValueError("bad card code")
            code = aliases[0]
        else:
            raise ValueError("bad kind")
    except ValueError as exc:
        add_access_event(kind or "unknown", "denied", request, str(body.get("code") or ""), raw_hex, bits, reason="bad_format")
        return {"allowed": False, "reason": str(exc)}

    result = check_access_rule(kind, code, raw_hex, request, bits)
    return {
        "allowed": bool(result.get("allowed")),
        "reason": result.get("reason"),
        "label": result.get("label", ""),
        "entry_id": result.get("entry_id"),
        "open_ms": result.get("open_ms", OPEN_DURATION_MS),
    }


# ===== Веб-кнопка (cookie-сессия админа) =====
ADMIN_COOKIE_MAX_AGE = 60 * 60 * 24 * 7   # 7 дней; скользящее — продлевается при каждом заходе


def set_admin_cookie(resp: Response) -> None:
    tok = make_token({"admin": True, "t": int(time.time())})
    resp.set_cookie(
        "skud_adm", tok,
        max_age=ADMIN_COOKIE_MAX_AGE, httponly=True, samesite="lax", path=ADMIN_COOKIE_PATH,
    )


@app.post("/api/admin/login")
async def admin_login(request: Request):
    body = await request.json()
    if str(body.get("password", "")) != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Неверный пароль")
    resp = JSONResponse({"ok": True})
    set_admin_cookie(resp)
    return resp


@app.post("/api/admin/logout")
async def admin_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("skud_adm", path=ADMIN_COOKIE_PATH)
    if ADMIN_COOKIE_PATH != BASE_PREFIX:
        resp.delete_cookie("skud_adm", path=BASE_PREFIX)
    return resp


@app.post("/api/admin/open")
async def admin_open(request: Request, door: int = 1, dur: int | None = None):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    if door not in (1, 2, 3, 4):
        raise HTTPException(status_code=422, detail="bad door")
    if dur is not None:
        dur = max(1000, min(int(dur), 120000))   # 1с..2мин — защита
    return await queue_open_and_wait("web", request, door, dur)


# DEMO: открытие без авторизации (для демо-кнопки, потом убрать или защитить)
@app.post("/api/web/open")
async def web_open(request: Request, door: int = 1):
    if not ALLOW_PUBLIC_WEB_OPEN and not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    if door not in (1, 2, 3, 4):
        raise HTTPException(status_code=422, detail="bad door")
    return await queue_open_and_wait("web", request, door)


@app.get("/api/web/status")
async def web_status():
    return {
        "pending_open": State.pending_open,
        "pending_open_expires_at": State.pending_open_expires_at,
        "last_open_queued_at": State.last_open_queued_at,
        "last_open_consumed_at": State.last_open_consumed_at,
        **device_status(),
        "uptime_s": round(time.time() - State.boot_at, 1),
    }


@app.get("/api/admin/status")
async def admin_status(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    now = time.time()
    resp = JSONResponse({
        "pending_open": State.pending_open,
        "pending_open_expires_at": State.pending_open_expires_at,
        **device_status(),
        "last_open_queued_at": State.last_open_queued_at,
        "last_open_consumed_at": State.last_open_consumed_at,
        "history": State.history,
        "uptime_s": round(now - State.boot_at, 1),
    })
    # Скользящая сессия: если токен старше часа — продлеваем cookie ещё на 7 дней.
    # status дёргается при каждом открытии сайта и раз в 3с, так что «активные» не разлогинятся.
    data = parse_token(request.cookies.get("skud_adm"))
    if data and (now - float(data.get("t", 0)) > 3600):
        set_admin_cookie(resp)
    return resp


@app.get("/api/admin/access")
async def admin_access_list(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    store = load_access_store()
    return {
        "pins": [sanitize_access_entry(entry) for entry in store.get("pins", [])],
        "cards": [sanitize_access_entry(entry) for entry in store.get("cards", [])],
        "events": State.access_events,
    }


@app.post("/api/admin/access/pin")
async def admin_access_add_pin(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    body = await request.json()
    try:
        entry = add_access_entry("pin", body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "entry": entry}


@app.post("/api/admin/access/card")
async def admin_access_add_card(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    body = await request.json()
    try:
        entry = add_access_entry("card", body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "entry": entry}


@app.delete("/api/admin/access/{kind}/{entry_id}")
async def admin_access_delete(kind: str, entry_id: str, request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    bucket = {"pin": "pins", "pins": "pins", "card": "cards", "cards": "cards"}.get(kind)
    if not bucket:
        raise HTTPException(status_code=404, detail="bad kind")
    store = load_access_store()
    before = len(store.get(bucket, []))
    store[bucket] = [entry for entry in store.get(bucket, []) if entry.get("id") != entry_id]
    if len(store[bucket]) == before:
        raise HTTPException(status_code=404, detail="not found")
    save_access_store(store)
    return {"ok": True}


# ===== Прокси HLS уличной камеры (только для залогиненных) =====
# Камера пушит RTMP -> локальный MediaMTX (127.0.0.1:8898) -> здесь отдаём в браузер.
HLS_BASE = "http://127.0.0.1:8898"
# MediaMTX теперь требует токен на чтение (анонимного чтения нет) — прокси ходит как camview.
_HLS_USER = os.getenv("SKUD_MTX_USER", "camview")
_HLS_PASS = os.getenv("SKUD_MTX_PASS", "")
_HLS_AUTH = "Basic " + _base64.b64encode(f"{_HLS_USER}:{_HLS_PASS}".encode()).decode()


@app.get("/cam/{path:path}")
async def cam_proxy(path: str, request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=404)
    url = f"{HLS_BASE}/{path}"

    def fetch():
        req = urllib.request.Request(url, headers={"Authorization": _HLS_AUTH})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read(), r.headers.get("Content-Type", "application/octet-stream")

    try:
        data, ctype = await asyncio.to_thread(fetch)
    except Exception:
        raise HTTPException(status_code=502, detail="cam offline")
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "no-store"})


# ===== WebRTC сигналинг (WHEP) — проксируем на локальный MediaMTX, под логином =====
WHEP_BASE = "http://127.0.0.1:8889"


@app.post("/whep/{cam}")
async def whep_proxy(cam: str, request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    if not all(c.isalnum() or c in "-_" for c in cam):
        raise HTTPException(status_code=404)
    offer = await request.body()
    url = f"{WHEP_BASE}/{cam}/whep"

    def fwd():
        req = urllib.request.Request(
            url, data=offer, method="POST",
            headers={"Content-Type": "application/sdp", "Authorization": _HLS_AUTH},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read(), r.headers.get("Content-Type", "application/sdp")

    try:
        body, ctype = await asyncio.to_thread(fwd)
    except Exception:
        raise HTTPException(status_code=502, detail="webrtc offline")
    return Response(content=body, status_code=201, media_type="application/sdp")


# ===== ESP32-CAM: камера сама шлёт JPEG-кадры (POST), раздаём как MJPEG =====
@app.post("/api/cam/push")
async def cam_push(request: Request, token: str, cam: str = "indoor"):
    if token != CAM_PUSH_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty frame")
    State.cam_frames[cam] = {"jpeg": body, "at": time.time()}
    return {"ok": True, "bytes": len(body)}


@app.get("/api/cam/{cam}/frame.jpg")
async def cam_frame(cam: str, request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    f = State.cam_frames.get(cam)
    if not f:
        raise HTTPException(status_code=404, detail="no frame")
    return Response(content=f["jpeg"], media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/cam/{cam}/mjpeg")
async def cam_mjpeg(cam: str, request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")

    async def gen():
        last_at = 0.0
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            f = State.cam_frames.get(cam)
            if f and f["at"] != last_at:
                last_at = f["at"]
                idle = 0
                jpg = f["jpeg"]
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
            else:
                idle += 1
                if idle > 600:  # ~60с без новых кадров — закрываем (камера офлайн)
                    break
            await asyncio.sleep(0.08)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


# ===== Яндекс.Карты: учёт лимита панорам + авто-переключение 2 ключей =====
def _msk_date() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))


def _maps_reset_if_needed():
    today = _msk_date()
    u = State.maps_usage
    if not u or u.get("date") != today:
        State.maps_usage = {
            "date": today,
            "active": 0,
            "keys": [{"pano": 0, "geo": 0, "exhausted": False} for _ in MAPS_KEYS],
        }


def _maps_pick_active() -> int:
    u = State.maps_usage
    for i, k in enumerate(u["keys"]):
        if not k["exhausted"] and k["pano"] < MAPS_PANO_LIMIT:
            u["active"] = i
            return i
    u["active"] = 0
    return 0


def _maps_state_dict() -> dict:
    _maps_reset_if_needed()
    idx = _maps_pick_active()
    u = State.maps_usage
    all_exhausted = all(k["exhausted"] or k["pano"] >= MAPS_PANO_LIMIT for k in u["keys"])
    return {
        "active_idx": idx,
        "active_key": None if all_exhausted else MAPS_KEYS[idx],
        "limit": MAPS_PANO_LIMIT,
        "all_exhausted": all_exhausted,
        "date": u["date"],
        "keys": [
            {
                "pano": k["pano"],
                "geo": k["geo"],
                "remaining": max(0, MAPS_PANO_LIMIT - k["pano"]),
                "exhausted": k["exhausted"] or k["pano"] >= MAPS_PANO_LIMIT,
            }
            for k in u["keys"]
        ],
    }


@app.get("/api/maps/state")
async def maps_state(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    return _maps_state_dict()


@app.get("/api/maps/geocode")
async def maps_geocode(q: str, request: Request):
    # Адрес -> координаты через OpenStreetMap Nominatim (бесплатно, без ключа).
    # Панорамы при этом остаются на Яндексе (для них хватает JS API ключа).
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="empty query")
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({
        "q": q, "format": "json", "limit": 1,
        "accept-language": "ru", "countrycodes": "ru,by,kz,kg,am",
    })

    def fetch():
        req = urllib.request.Request(url, headers={"User-Agent": "skud-courier-navigator/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode())

    try:
        data = await asyncio.to_thread(fetch)
    except Exception:
        raise HTTPException(status_code=502, detail="geocode error")
    if not data:
        return {"found": False}
    it = data[0]
    try:
        return {"found": True, "lat": float(it["lat"]), "lon": float(it["lon"]),
                "name": it.get("display_name", "")}
    except (KeyError, TypeError, ValueError):
        return {"found": False}


@app.post("/api/maps/use")
async def maps_use(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    try:
        body = await request.json()
    except Exception:
        body = {}
    _maps_reset_if_needed()
    u = State.maps_usage
    idx = u.get("active", 0)
    k = u["keys"][idx]
    try:
        k["pano"] += max(0, int(body.get("panoramas", 0) or 0))
        k["geo"] += max(0, int(body.get("geocodes", 0) or 0))
    except (TypeError, ValueError):
        pass
    if body.get("exhausted"):
        k["exhausted"] = True
    return _maps_state_dict()


# ===== Счётчик людей (статистика сохраняется на диск по дням) =====
def _load_people_daily() -> dict:
    try:
        d = json.loads(PEOPLE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_people_daily():
    try:
        tmp = PEOPLE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(State.people_daily, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PEOPLE_FILE)
    except Exception:
        pass


State.people_daily = _load_people_daily()


@app.post("/api/people/count")
async def people_count(request: Request, token: str):
    if token != PEOPLE_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        n = int(body.get("n", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    now = time.time()
    if n > 0:
        State.people_events.extend([now] * min(n, 200))
        day = _msk_date()
        State.people_daily[day] = State.people_daily.get(day, 0) + n
        _save_people_daily()
    cutoff = now - 25 * 3600
    if State.people_events and State.people_events[0] < cutoff:
        State.people_events = [t for t in State.people_events if t >= cutoff]
    return {"ok": True, "today": State.people_daily.get(_msk_date(), 0)}


def _people_stats() -> dict:
    now = time.time()
    ev = State.people_events
    series = []
    for h in range(11, -1, -1):
        a, b = now - (h + 1) * 3600, now - h * 3600
        series.append(sum(1 for t in ev if a <= t < b))
    return {
        "per_min": sum(1 for t in ev if t >= now - 60),
        "last_hour": sum(1 for t in ev if t >= now - 3600),
        "today": State.people_daily.get(_msk_date(), 0),
        "total_24h": sum(1 for t in ev if t >= now - 86400),
        "series_hourly": series,
    }


@app.get("/api/people/stats")
async def people_stats(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    return _people_stats()


@app.get("/api/people/history")
async def people_history(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    items = sorted(State.people_daily.items())[-30:]
    return {"daily": [{"date": d, "count": c} for d, c in items],
            "today": State.people_daily.get(_msk_date(), 0)}


# ===== Записи камер в S3 (просмотр) =====
REC_CAMS = ("outdoor", "sklad", "pack")


@app.get("/api/rec/dates")
async def rec_dates(request: Request, cam: str):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    if cam not in REC_CAMS:
        raise HTTPException(status_code=422, detail="bad cam")
    try:
        objs = s3lite.list_objects("rec/%s/" % cam)
    except Exception as e:
        raise HTTPException(status_code=502, detail="s3 error: %s" % e)
    dates = sorted({o["key"].split("/")[-1][:10] for o in objs if o["key"].endswith(".mp4")}, reverse=True)
    return {"cam": cam, "dates": dates}


@app.get("/api/rec/list")
async def rec_list(request: Request, cam: str, date: str):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    if cam not in REC_CAMS:
        raise HTTPException(status_code=422, detail="bad cam")
    try:
        objs = s3lite.list_objects("rec/%s/%s" % (cam, date))
    except Exception as e:
        raise HTTPException(status_code=502, detail="s3 error: %s" % e)
    items = []
    for o in objs:
        if not o["key"].endswith(".mp4"):
            continue
        fn = o["key"].split("/")[-1]
        tm = fn[11:19].replace("-", ":") if len(fn) >= 19 else fn
        items.append({"key": o["key"], "time": tm, "mb": round(o["size"] / 1048576.0, 1)})
    items.sort(key=lambda x: x["key"])
    return {"cam": cam, "date": date, "count": len(items), "items": items}


@app.get("/api/rec/url")
async def rec_url(request: Request, key: str):
    # Отдаём СТАБИЛЬНУЮ presigned-ссылку (2ч) — плеер ставит её прямо в video.src,
    # браузер шлёт range-запросы напрямую в S3 (перемотка работает, сервер не грузим).
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    if s3lite is None:
        raise HTTPException(status_code=503, detail="s3 unavailable")
    if not key.startswith("rec/") or ".." in key:
        raise HTTPException(status_code=422, detail="bad key")
    return {"url": s3lite.presign_get(key, 7200)}


@app.get("/api/rec/stream")
def rec_stream(request: Request, key: str):
    # Проксируем сегмент из S3 с ЗАГОЛОВКОМ Accept-Ranges — тогда браузер «прыгает» по файлу
    # (range-запросы) и старт мгновенный даже для больших сегментов. sync-эндпоинт → threadpool.
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    if s3lite is None:
        raise HTTPException(status_code=503, detail="s3 unavailable")
    if not key.startswith("rec/") or ".." in key:
        raise HTTPException(status_code=422, detail="bad key")
    rng = request.headers.get("range")
    try:
        up = s3lite.open_range(key, rng)
    except Exception as e:
        code = getattr(e, "code", 502)
        raise HTTPException(status_code=code if isinstance(code, int) else 502, detail="s3 error")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    if up.headers.get("Content-Range"):
        headers["Content-Range"] = up.headers.get("Content-Range")
    if up.headers.get("Content-Length"):
        headers["Content-Length"] = up.headers.get("Content-Length")
    status = up.status

    def gen():
        try:
            while True:
                chunk = up.read(262144)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                up.close()
            except Exception:
                pass
    return StreamingResponse(gen(), status_code=status, headers=headers, media_type="video/mp4")


@app.get("/api/rec/play")
async def rec_play(request: Request, key: str):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="not authorized")
    if s3lite is None:
        raise HTTPException(status_code=503, detail="s3 unavailable")
    if not key.startswith("rec/") or ".." in key:
        raise HTTPException(status_code=422, detail="bad key")
    return RedirectResponse(s3lite.presign_get(key, 3600), status_code=302)


# ===== Статика веб-фронта =====
@app.get("/")
async def root():
    return FileResponse(PUBLIC_DIR / "index.html")


# раздаём ассеты по точечным путям (preferable над mounting StaticFiles на /)
@app.get("/{path:path}")
async def static_files(path: str):
    if ".." in path or path.startswith("/"):
        raise HTTPException(404)
    p = PUBLIC_DIR / path
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)
