// СКУД на ESP32 + 4-канальное реле (активно низким).
// D13 → IN1. HIGH = реле в покое = замок (NC, fail-safe) заперт.
// LOW = реле щёлкает = замок открыт.
//
// Каждую секунду опрашивает сервер. Если сервер отдаёт {"cmd":"open"} —
// открывает на duration_ms (по умолчанию 5000) и снова запирает.
// Локальные Serial-команды open / lock тоже работают (для отладки).

#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClient.h>
#include <ArduinoOTA.h>

// ===== Конфиг =====
// Заполните значения перед прошивкой (или вынесите в отдельный secrets.h, не коммитя его).
const char* WIFI_SSID = "<WIFI_SSID>";
const char* WIFI_PSK  = "<WIFI_PASSWORD>";

const char* POLL_BASE = "http://<SERVER_HOST>:8097/api/poll";
const char* ACCESS_URL = "http://<SERVER_HOST>:8097/api/device/access";
const char* DEVICE_TOKEN = "<DEVICE_TOKEN>";

const uint8_t  RELAY_PIN          = 13;   // дверь 1 → реле IN1 (маглок, NC)
// ВНИМАНИЕ: проводку K2/K3 перепутали местами — поправлено софтом (свап пинов):
const uint8_t  RELAY2_PIN         = 26;   // дверь 2 (Внешняя, маглок) — физически на K3/GPIO26
const uint8_t  RELAY3_PIN         = 27;   // дверь 3 (Ячейка, электрозамок NO) — физически на K2/GPIO27
const uint8_t  RELAY4_PIN         = 25;   // дверь 4 (дальняя) → реле IN4/K4
const uint8_t  BTN4_PIN           = 33;   // кнопка выхода двери 4 (на GND, INPUT_PULLUP)
const uint8_t  WIEGAND_D0_PIN     = 18;
const uint8_t  WIEGAND_D1_PIN     = 19;
const unsigned long DEFAULT_OPEN_MS = 5000;
const unsigned long CELL_OPEN_MS = 1500;   // замок ячейки: короткий импульс питания (электрозамку 5с не нужно)
const unsigned long POLL_INTERVAL_MS = 1000;
const unsigned long HTTP_TIMEOUT_MS  = 2500;
const unsigned long ACCESS_HTTP_TIMEOUT_MS = 3500;
const unsigned long POLL_STALE_RESTART_MS = 120000;
const unsigned long PERIODIC_RESTART_MS = 6UL * 60UL * 60UL * 1000UL;
const unsigned long WIEGAND_FRAME_TIMEOUT_US = 80000;
const unsigned long WIEGAND_GLITCH_FILTER_US = 120;
const unsigned long PIN_INPUT_TIMEOUT_MS = 8000;
const unsigned long ACCESS_LOCKOUT_MS = 30000;
const uint8_t MAX_PIN_LEN = 12;
const uint8_t MAX_BAD_ACCESS_ATTEMPTS = 5;
const bool WIEGAND_DEBUG = true;  // первый этап: включено, чтобы увидеть формат панели

const char* OTA_HOSTNAME = "skud-esp32";
const char* OTA_PASSWORD = "<OTA_PASSWORD>";

// 1 = «serial-раб» для Raspberry Pi моста: Wi-Fi/poll/OTA/self-heal выключены,
//     плата только слушает Serial (open/open2/lock/lock2) и щёлкает реле.
// 0 = автономный режим с Wi-Fi + поллингом сервера + OTA (старое поведение).
#define SERIAL_ONLY 1

// ===== Состояние =====
unsigned long lastPollAt = 0;
unsigned long lastPollOkAt = 0;
unsigned long lastWifiLogAt = 0;
unsigned long lastWifiKickAt = 0;
unsigned long bootAt = 0;
bool wifiWasConnected = false;
bool otaInitialized = false;
bool otaInProgress = false;
String pinBuffer = "";
unsigned long pinLastInputAt = 0;
unsigned long accessBlockedUntil = 0;
uint8_t badAccessAttempts = 0;

volatile uint64_t wgData = 0;
volatile uint8_t wgBits = 0;
volatile unsigned long wgLastPulseUs = 0;
volatile unsigned long wgLastAcceptedPulseUs = 0;
volatile bool wgOverflow = false;

// ===== Реле =====
// door: 1 → RELAY_PIN (дверь 1), 2 → RELAY2_PIN (дверь 2). Любое другое → дверь 1.
uint8_t relayPinFor(uint8_t door) {
  if (door == 2) return RELAY2_PIN;
  if (door == 3) return RELAY3_PIN;
  if (door == 4) return RELAY4_PIN;
  return RELAY_PIN;
}

// Неблокирующее авто-закрытие: момент millis(), когда закрыть дверь d (0 = не запланировано).
// Индексы 1..4. Благодаря этому открытие на 60с НЕ вешает плату (другие двери отзывчивы).
unsigned long doorCloseAt[5] = {0, 0, 0, 0, 0};

void lockDoor(uint8_t door) {
  digitalWrite(relayPinFor(door), HIGH);
  if (door >= 1 && door <= 4) doorCloseAt[door] = 0;
  Serial.print("Дверь ");
  Serial.print(door);
  Serial.println(": заперто");
}

void openDoorNum(uint8_t door, unsigned long ms) {
  if (door < 1 || door > 4) door = 1;
  uint8_t pin = relayPinFor(door);
  digitalWrite(pin, LOW);                  // открыто
  unsigned long at = millis() + ms;
  if (at == 0) at = 1;                      // 0 зарезервирован под «не запланировано»
  doorCloseAt[door] = at;
  Serial.print("Открываю дверь ");
  Serial.print(door);
  Serial.print(" на ");
  Serial.print(ms);
  Serial.println(" мс (неблок.)");
}

// Кнопка выхода двери 4: нажал -> открыл локально (мгновенно, без сервера). С антидребезгом.
void handleButton4() {
  static bool stable = HIGH, last = HIGH;
  static unsigned long tChange = 0;
  bool r = digitalRead(BTN4_PIN);
  unsigned long now = millis();
  if (r != last) { last = r; tChange = now; }
  if (now - tChange > 40 && r != stable) {
    stable = r;
    if (stable == LOW) {   // нажата (на GND)
      Serial.println("Кнопка выхода: дверь 4");
      openDoorNum(4, DEFAULT_OPEN_MS);
    }
  }
}

// Закрываем двери по таймеру — вызывать в loop().
void handleDoorTimers() {
  unsigned long now = millis();
  for (uint8_t d = 1; d <= 4; d++) {
    if (doorCloseAt[d] != 0 && (long)(now - doorCloseAt[d]) >= 0) {
      digitalWrite(relayPinFor(d), HIGH);
      doorCloseAt[d] = 0;
      Serial.print("Дверь ");
      Serial.print(d);
      Serial.println(": заперто (таймер)");
    }
  }
}

// Обратная совместимость: считыватель/Serial по умолчанию открывают дверь 1.
void lockNow() {
  lockDoor(1);
}

void openDoor(unsigned long ms) {
  openDoorNum(1, ms);
}

// ===== Wiegand =====
void IRAM_ATTR wiegandD0ISR() {
  unsigned long now = micros();
  if ((unsigned long)(now - wgLastAcceptedPulseUs) < WIEGAND_GLITCH_FILTER_US) return;
  wgLastAcceptedPulseUs = now;
  if (wgBits < 64) {
    wgData <<= 1;
    wgBits++;
  } else {
    wgOverflow = true;
  }
  wgLastPulseUs = now;
}

void IRAM_ATTR wiegandD1ISR() {
  unsigned long now = micros();
  if ((unsigned long)(now - wgLastAcceptedPulseUs) < WIEGAND_GLITCH_FILTER_US) return;
  wgLastAcceptedPulseUs = now;
  if (wgBits < 64) {
    wgData = (wgData << 1) | 1ULL;
    wgBits++;
  } else {
    wgOverflow = true;
  }
  wgLastPulseUs = now;
}

String uint64ToDec(uint64_t value) {
  if (value == 0) return "0";
  char buf[21];
  buf[20] = '\0';
  int pos = 20;
  while (value > 0 && pos > 0) {
    buf[--pos] = char('0' + (value % 10));
    value /= 10;
  }
  return String(&buf[pos]);
}

String uint64ToHex(uint64_t value) {
  if (value == 0) return "0";
  const char* digits = "0123456789ABCDEF";
  char buf[17];
  buf[16] = '\0';
  int pos = 16;
  while (value > 0 && pos > 0) {
    buf[--pos] = digits[value & 0x0F];
    value >>= 4;
  }
  return String(&buf[pos]);
}

String jsonEscape(const String& value) {
  String out;
  out.reserve(value.length() + 4);
  for (size_t i = 0; i < value.length(); i++) {
    char c = value[i];
    if (c == '"' || c == '\\') {
      out += '\\';
      out += c;
    } else if ((uint8_t)c >= 0x20) {
      out += c;
    }
  }
  return out;
}

char mapWiegandKey(uint8_t key) {
  if (key <= 9) return char('0' + key);
  if (key == 10 || key == 14) return '*';
  if (key == 11 || key == 15) return '#';
  return 0;
}

char decodeWiegandKey(uint64_t raw, uint8_t bits) {
  if (bits == 4) {
    return mapWiegandKey((uint8_t)(raw & 0x0F));
  }
  if (bits == 8) {
    uint8_t value = (uint8_t)(raw & 0xFF);
    uint8_t key = (value >> 4) & 0x0F;
    uint8_t inv = value & 0x0F;
    if ((key ^ inv) == 0x0F) return mapWiegandKey(key);
  }
  return 0;
}

void wiegandBegin() {
  pinMode(WIEGAND_D0_PIN, INPUT_PULLUP);
  pinMode(WIEGAND_D1_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(WIEGAND_D0_PIN), wiegandD0ISR, FALLING);
  attachInterrupt(digitalPinToInterrupt(WIEGAND_D1_PIN), wiegandD1ISR, FALLING);
  Serial.print("Wiegand initialized: D0=GPIO");
  Serial.print(WIEGAND_D0_PIN);
  Serial.print(", D1=GPIO");
  Serial.println(WIEGAND_D1_PIN);
}

void resetAccessFailures() {
  badAccessAttempts = 0;
  accessBlockedUntil = 0;
}

bool accessIsBlocked() {
  unsigned long now = millis();
  if (accessBlockedUntil == 0) return false;
  if ((long)(now - accessBlockedUntil) >= 0) {
    accessBlockedUntil = 0;
    badAccessAttempts = 0;
    Serial.println("ACCESS: lockout ended");
    return false;
  }
  return true;
}

void recordAccessDenied() {
  badAccessAttempts++;
  Serial.print("ACCESS: denied, bad attempts=");
  Serial.println(badAccessAttempts);
  if (badAccessAttempts >= MAX_BAD_ACCESS_ATTEMPTS) {
    accessBlockedUntil = millis() + ACCESS_LOCKOUT_MS;
    Serial.println("ACCESS: too many failures, blocked for 30 seconds");
  }
}

long parseJsonLong(const String& body, const char* name, long fallback) {
  String key = String("\"") + name + "\"";
  int p = body.indexOf(key);
  if (p < 0) return fallback;
  int colon = body.indexOf(':', p);
  if (colon < 0) return fallback;
  String tail = body.substring(colon + 1);
  tail.trim();
  long value = tail.toInt();
  return value > 0 ? value : fallback;
}

bool parseAllowedResponse(const String& body, unsigned long& outMs) {
  String compact = body;
  compact.replace(" ", "");
  compact.replace("\n", "");
  compact.replace("\r", "");
  bool allowed = compact.indexOf("\"allowed\":true") >= 0;
  outMs = (unsigned long)parseJsonLong(compact, "open_ms", DEFAULT_OPEN_MS);
  if (outMs == 0 || outMs > 60000) outMs = DEFAULT_OPEN_MS;
  return allowed;
}

bool checkAccessOnline(const char* kind, const String& code, uint8_t bits, const String& rawHex, unsigned long& openMs) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("ACCESS: Wi-Fi offline, cannot check credential");
    return false;
  }

  String url = String(ACCESS_URL) + "?token=" + DEVICE_TOKEN;
  String body = "{";
  body += "\"kind\":\"";
  body += kind;
  body += "\",\"code\":\"";
  body += jsonEscape(code);
  body += "\",\"bits\":";
  body += String(bits);
  if (rawHex.length() > 0) {
    body += ",\"raw_hex\":\"";
    body += jsonEscape(rawHex);
    body += "\"";
  }
  body += "}";

  WiFiClient client;
  HTTPClient http;
  http.setTimeout(ACCESS_HTTP_TIMEOUT_MS);
  http.setConnectTimeout(ACCESS_HTTP_TIMEOUT_MS);
  http.setReuse(false);
  if (!http.begin(client, url)) {
    Serial.println("ACCESS: HTTP begin() failed");
    client.stop();
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Connection", "close");
  int httpCode = http.POST(body);
  if (httpCode != 200) {
    Serial.print("ACCESS: HTTP fail code=");
    Serial.println(httpCode);
    http.end();
    client.stop();
    return false;
  }
  String response = http.getString();
  bool allowed = parseAllowedResponse(response, openMs);
  http.end();
  client.stop();
  return allowed;
}

void submitCredential(const char* kind, const String& code, uint8_t bits, const String& rawHex) {
  if (accessIsBlocked()) {
    Serial.println("ACCESS: blocked, credential ignored");
    return;
  }

  unsigned long openMs = DEFAULT_OPEN_MS;
  bool allowed = checkAccessOnline(kind, code, bits, rawHex, openMs);
  if (allowed) {
    Serial.print("ACCESS: ");
    Serial.print(kind);
    Serial.println(" accepted");
    resetAccessFailures();
    openDoor(openMs);
  } else {
    Serial.print("ACCESS: ");
    Serial.print(kind);
    Serial.println(" rejected");
    recordAccessDenied();
  }
}

void submitPin() {
  if (pinBuffer.length() == 0) return;
  String pin = pinBuffer;
  pinBuffer = "";
  if (WIEGAND_DEBUG) {
    Serial.print("PIN submit: ");
    Serial.println(pin);
  } else {
    Serial.print("PIN submit, len=");
    Serial.println(pin.length());
  }
  submitCredential("pin", pin, 0, "");
}

void handleKeypadKey(char key) {
  Serial.print("Keypad input detected: ");
  Serial.println(key);
  if (key == '*') {
    pinBuffer = "";
    Serial.println("PIN buffer cleared");
    return;
  }
  if (key == '#') {
    submitPin();
    return;
  }
  if (key >= '0' && key <= '9') {
    if (pinBuffer.length() >= MAX_PIN_LEN) {
      pinBuffer = "";
      Serial.println("PIN buffer overflow, cleared");
    }
    pinBuffer += key;
    pinLastInputAt = millis();
    if (WIEGAND_DEBUG) {
      Serial.print("PIN buffer: ");
      Serial.println(pinBuffer);
    } else {
      Serial.print("PIN buffer len=");
      Serial.println(pinBuffer.length());
    }
  }
}

void processWiegandFrame(uint64_t raw, uint8_t bits, bool overflow) {
  String rawDec = uint64ToDec(raw);
  String rawHex = uint64ToHex(raw);
  Serial.print("Reader event received: bits=");
  Serial.print(bits);
  Serial.print(" raw_dec=");
  Serial.print(rawDec);
  Serial.print(" raw_hex=0x");
  Serial.print(rawHex);
  if (overflow) Serial.print(" overflow=1");
  Serial.println();

  char key = decodeWiegandKey(raw, bits);
  if (key) {
    handleKeypadKey(key);
    return;
  }

  if (bits == 26 || bits == 34) {
    Serial.println("Card detected");
    submitCredential("card", rawDec, bits, rawHex);
    return;
  }

  Serial.println("Wiegand: ignored frame, expected keypad 4/8 bit or card 26/34 bit");
}

void handleWiegand() {
  uint64_t raw;
  uint8_t bits;
  unsigned long lastPulse;
  bool overflow;

  noInterrupts();
  raw = wgData;
  bits = wgBits;
  lastPulse = wgLastPulseUs;
  overflow = wgOverflow;
  interrupts();

  if (bits == 0) return;
  if ((unsigned long)(micros() - lastPulse) < WIEGAND_FRAME_TIMEOUT_US) return;

  noInterrupts();
  if (bits != wgBits || lastPulse != wgLastPulseUs) {
    interrupts();
    return;
  }
  wgData = 0;
  wgBits = 0;
  wgOverflow = false;
  interrupts();

  processWiegandFrame(raw, bits, overflow);
}

void handlePinTimeout() {
  if (pinBuffer.length() == 0) return;
  if (millis() - pinLastInputAt > PIN_INPUT_TIMEOUT_MS) {
    pinBuffer = "";
    Serial.println("PIN input timeout, buffer cleared");
  }
}

// ===== Wi-Fi =====
void wifiBegin() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.setSleep(false);           // главное: убираем modem-sleep, иначе пакеты теряются
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  WiFi.begin(WIFI_SSID, WIFI_PSK);
  Serial.print("Wi-Fi: подключаюсь к ");
  Serial.println(WIFI_SSID);
}

void otaBegin() {
  if (otaInitialized) return;
  ArduinoOTA.setHostname(OTA_HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.onStart([]() {
    otaInProgress = true;
    // Перед прошивкой защёлкиваем оба замка и логируем
    digitalWrite(RELAY_PIN, HIGH);
    digitalWrite(RELAY2_PIN, HIGH);
    Serial.println("OTA: старт");
  });
  ArduinoOTA.onEnd([]() {
    Serial.println("\nOTA: готово, перезагрузка");
  });
  ArduinoOTA.onProgress([](unsigned int p, unsigned int t) {
    static unsigned int lastPct = 255;
    unsigned int pct = (p * 100) / t;
    if (pct != lastPct && pct % 10 == 0) {
      Serial.print("OTA: ");
      Serial.print(pct);
      Serial.println("%");
      lastPct = pct;
    }
  });
  ArduinoOTA.onError([](ota_error_t e) {
    otaInProgress = false;
    Serial.print("OTA: ошибка ");
    Serial.println((int)e);
  });
  ArduinoOTA.begin();
  Serial.print("OTA: слушаю на ");
  Serial.print(OTA_HOSTNAME);
  Serial.println(".local:3232");
  otaInitialized = true;
}

void wifiEnsure() {
  wl_status_t st = WiFi.status();
  if (st == WL_CONNECTED) {
    if (!wifiWasConnected) {
      Serial.print("Wi-Fi: подключено, IP=");
      Serial.println(WiFi.localIP());
      wifiWasConnected = true;
      otaBegin();
    }
    return;
  }
  if (wifiWasConnected) {
    Serial.println("Wi-Fi: соединение потеряно");
    wifiWasConnected = false;
    lastWifiKickAt = millis();
  }
  if (millis() - lastWifiLogAt > 5000) {
    Serial.print("Wi-Fi: жду подключения... status=");
    Serial.println((int)st);
    lastWifiLogAt = millis();
  }
  // Не дёргаем reconnect постоянно — это перебивает текущую попытку.
  // Если 30 сек никакого прогресса (FAILED/NO_SSID) — даём пинок.
  if ((st == WL_CONNECT_FAILED || st == WL_NO_SSID_AVAIL || st == WL_DISCONNECTED) &&
      millis() - lastWifiKickAt > 30000) {
    Serial.println("Wi-Fi: перезапуск begin()");
    WiFi.disconnect(true, true);
    delay(200);
    WiFi.begin(WIFI_SSID, WIFI_PSK);
    lastWifiKickAt = millis();
  }
}

// ===== Парсер ответа =====
// Простой поиск без ArduinoJson — ответы у нас формы:
//   {"cmd":"idle"}
//   {"cmd":"open","duration_ms":5000}            -> дверь 1 (обратная совместимость)
//   {"cmd":"open","door":2,"duration_ms":5000}   -> дверь 2
bool parseOpenCommand(const String& body, unsigned long& outMs, uint8_t& outDoor) {
  if (body.indexOf("\"cmd\":\"open\"") < 0) return false;
  outMs = DEFAULT_OPEN_MS;
  outDoor = 1;  // если поля "door" нет — это дверь 1
  int p = body.indexOf("\"duration_ms\"");
  if (p >= 0) {
    int colon = body.indexOf(':', p);
    if (colon >= 0) {
      String tail = body.substring(colon + 1);
      tail.trim();
      long v = tail.toInt();
      if (v > 0 && v < 60000) outMs = (unsigned long)v;
    }
  }
  int d = body.indexOf("\"door\"");
  if (d >= 0) {
    int colon = body.indexOf(':', d);
    if (colon >= 0) {
      String tail = body.substring(colon + 1);
      tail.trim();
      long v = tail.toInt();
      if (v == 2) outDoor = 2;
    }
  }
  return true;
}

// ===== Опрос сервера =====
void pollServer() {
  static unsigned int consecutiveFails = 0;
  char url[256];
  snprintf(
    url,
    sizeof(url),
    "%s?token=%s&uptime_s=%lu&rssi=%ld&heap=%u&fails=%u",
    POLL_BASE,
    DEVICE_TOKEN,
    millis() / 1000UL,
    (long)WiFi.RSSI(),
    ESP.getFreeHeap(),
    consecutiveFails
  );

  WiFiClient client;
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(HTTP_TIMEOUT_MS);
  http.setReuse(false);
  if (!http.begin(client, url)) {
    Serial.println("HTTP: begin() failed");
    client.stop();
    return;
  }
  http.addHeader("Connection", "close");
  int code = http.GET();
  if (code == 200) {
    consecutiveFails = 0;
    lastPollOkAt = millis();
    String body = http.getString();
    unsigned long openMs;
    uint8_t door;
    if (parseOpenCommand(body, openMs, door)) {
      Serial.print("Сервер: open ");
      Serial.println(body);
      http.end();
      client.stop();
      openDoorNum(door, openMs);
      return;
    }
    // idle — молча
  } else {
    consecutiveFails++;
    // Не спамим: логируем каждую 5-ю подряд ошибку
    if (consecutiveFails == 1 || consecutiveFails % 5 == 0) {
      Serial.print("HTTP fail #");
      Serial.print(consecutiveFails);
      Serial.print(" code=");
      Serial.print(code);
      if (code < 0) {
        Serial.print(" (");
        Serial.print(http.errorToString(code));
        Serial.print(")");
      }
      Serial.println();
    }
    // 10 ошибок подряд → пинаем Wi-Fi (вдруг застряли)
    if (consecutiveFails >= 10) {
      Serial.println("HTTP: 10 ошибок подряд, перезапускаю Wi-Fi");
      WiFi.disconnect(true, true);
      delay(100);
      WiFi.begin(WIFI_SSID, WIFI_PSK);
      consecutiveFails = 0;
    }
  }
  http.end();
  client.stop();
}

void selfHeal() {
  if (otaInProgress) return;
  unsigned long now = millis();
  if (lastPollOkAt > 0 && now - lastPollOkAt > POLL_STALE_RESTART_MS) {
    Serial.println("SELFHEAL: давно нет успешного poll, перезагрузка");
    lockNow();
    delay(100);
    ESP.restart();
  }
  if (bootAt > 0 && now - bootAt > PERIODIC_RESTART_MS) {
    Serial.println("SELFHEAL: плановая перезагрузка");
    lockNow();
    delay(100);
    ESP.restart();
  }
}

// ===== Serial CLI (для отладки) =====
void handleSerialCli() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  cmd.toLowerCase();
  if (cmd.length() == 0) return;
  if (cmd.startsWith("open")) {
    // формат: "open" | "open2" | "open3" [пробел длительность_мс]. Пример: "open2 60000".
    int sp = cmd.indexOf(' ');
    String verb = (sp < 0) ? cmd : cmd.substring(0, sp);
    long ms = (sp < 0) ? -1 : cmd.substring(sp + 1).toInt();
    uint8_t door; unsigned long defms;
    if (verb == "open")       { door = 1; defms = DEFAULT_OPEN_MS; }
    else if (verb == "open2") { door = 2; defms = DEFAULT_OPEN_MS; }
    else if (verb == "open3") { door = 3; defms = CELL_OPEN_MS; }
    else if (verb == "open4") { door = 4; defms = DEFAULT_OPEN_MS; }
    else { Serial.print("Неизвестная команда: "); Serial.println(cmd); return; }
    unsigned long use = (ms > 0) ? (unsigned long)ms : defms;
    if (use > 180000UL) use = 180000UL;   // потолок 3 минуты
    openDoorNum(door, use);
  } else if (cmd == "lock") {
    lockDoor(1);
  } else if (cmd == "lock2") {
    lockDoor(2);
  } else if (cmd == "lock3") {
    lockDoor(3);
  } else if (cmd == "lock4") {
    lockDoor(4);
  } else if (cmd.startsWith("pin ")) {
    String pin = cmd.substring(4);
    pin.trim();
    submitCredential("pin", pin, 0, "");
  } else if (cmd.startsWith("card ")) {
    String code = cmd.substring(5);
    code.trim();
    submitCredential("card", code, 0, "");
  } else if (cmd == "ip") {
    Serial.print("IP=");
    Serial.println(WiFi.localIP());
    Serial.print("RSSI=");
    Serial.println(WiFi.RSSI());
  } else {
    Serial.print("Неизвестная команда: ");
    Serial.println(cmd);
  }
}

// ===== setup / loop =====
void setup() {
  bootAt = millis();
  lastPollOkAt = millis();
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH);    // дверь 1: защёлкиваем до Serial — чтобы не было дребезга
  pinMode(RELAY2_PIN, OUTPUT);
  digitalWrite(RELAY2_PIN, HIGH);   // дверь 2: защёлкиваем до Serial
  pinMode(RELAY3_PIN, OUTPUT);
  digitalWrite(RELAY3_PIN, HIGH);   // дверь 3 (ячейка): реле в покое = питания на замке нет = заперто
  pinMode(RELAY4_PIN, OUTPUT);
  digitalWrite(RELAY4_PIN, HIGH);   // дверь 4 (дальняя): защёлкиваем до Serial
  pinMode(BTN4_PIN, INPUT_PULLUP);  // кнопка выхода двери 4
  Serial.begin(115200);
  delay(200);
  Serial.println();
#if SERIAL_ONLY
  Serial.println("СКУД ESP32 старт [build v8-4doors+btn / Pi-bridge]. Wi-Fi ВЫКЛ. Serial: open[2|3|4] [мс] | lock[2|3|4] | кнопка выхода D4=GPIO33");
#else
  Serial.println("СКУД ESP32 старт [OTA-build v5-2doors]. Команды Serial: open | open2 | lock | lock2 | ip | pin 1324 | card CODE");
#endif
  Serial.println("Обе двери заперты");
  wiegandBegin();
#if !SERIAL_ONLY
  wifiBegin();
#endif
}

void loop() {
#if !SERIAL_ONLY
  wifiEnsure();
  if (otaInitialized) ArduinoOTA.handle();
#endif
  handleWiegand();
  handlePinTimeout();
  handleSerialCli();
  handleButton4();             // кнопка выхода двери 4 (локальное открытие)
  handleDoorTimers();          // неблокирующее авто-закрытие дверей по таймеру
#if !SERIAL_ONLY
  selfHeal();

  // Во время прошивки не дёргаем HTTP — даём OTA всю полосу
  if (!otaInProgress &&
      WiFi.status() == WL_CONNECTED &&
      millis() - lastPollAt >= POLL_INTERVAL_MS) {
    lastPollAt = millis();
    pollServer();
  }
#endif
  delay(10);
}
