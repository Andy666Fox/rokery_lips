#include <Arduino.h>
#include <SPI.h>
#include <WiFi.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <time.h>
#include "monitor.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#include "secrets.example.h"
#endif

// Dxx on this ESP32 board corresponds to GPIO xx.
constexpr int TFT_SCK = 18;
constexpr int TFT_MOSI = 23;
constexpr int TFT_CS = 27;
constexpr int TFT_DC = 26;
constexpr int TFT_RESET = 25;
constexpr uint8_t TFT_PROFILE = INITR_BLACKTAB;
constexpr uint8_t TFT_ROTATION = 1;
constexpr long UTC_OFFSET_SECONDS = 4 * 60 * 60;
constexpr uint32_t WIFI_RETRY_MS = 30000;
constexpr uint32_t CLOCK_SCREEN_MS = 5000;
constexpr uint32_t SERVER_SCREEN_MS = 10000;

Adafruit_ST7735 display(&SPI, TFT_CS, TFT_DC, TFT_RESET);
GFXcanvas16 canvas(160, 128);
uint32_t lastWifiAttempt = 0;
uint32_t screenStartedAt = 0;
uint32_t lastFrameAt = 0;
bool ntpStarted = false;
bool credentialsConfigured = false;
bool serverScreen = false;
bool monitorStarted = false;
bool firstFrame = true;

void textAt(int x, int y, const char* text, uint16_t color = ST77XX_WHITE, int size = 1) {
  canvas.setTextSize(size);
  canvas.setTextColor(color);
  canvas.setCursor(x, y);
  canvas.print(text);
}

void centered(int y, const char* text, uint16_t color = ST77XX_WHITE, int size = 1) {
  textAt((160 - strlen(text) * 6 * size) / 2, y, text, color, size);
}

void drawDayIcon(int hour) {
  const int x = 80;
  const int y = 25;
  if (hour < 6 || hour >= 22) {
    canvas.fillCircle(x, y, 10, ST77XX_WHITE);
    canvas.fillCircle(x + 5, y - 4, 9, ST77XX_BLACK);
    canvas.drawPixel(x + 18, y - 7, ST77XX_WHITE);
    canvas.drawPixel(x - 17, y + 5, ST77XX_WHITE);
  } else if (hour < 12 || hour >= 18) {
    const uint16_t color = hour < 12 ? ST77XX_YELLOW : 0xFD20;
    canvas.fillCircle(x, y + 5, 9, color);
    canvas.fillRect(x - 12, y + 6, 25, 10, ST77XX_BLACK);
    canvas.drawFastHLine(x - 18, y + 6, 37, color);
    canvas.drawLine(x, y - 13, x, y - 8, color);
    canvas.drawLine(x - 15, y - 6, x - 11, y - 2, color);
    canvas.drawLine(x + 15, y - 6, x + 11, y - 2, color);
    // Up/down arrow distinguishes sunrise from sunset independently of color.
    const int arrowY = hour < 12 ? y - 8 : y;
    const int direction = hour < 12 ? 1 : -1;
    canvas.drawLine(x + 23, y - 8, x + 23, y, color);
    canvas.drawLine(x + 23, arrowY, x + 20, arrowY + 3 * direction, color);
    canvas.drawLine(x + 23, arrowY, x + 26, arrowY + 3 * direction, color);
  } else {
    canvas.fillCircle(x, y, 8, ST77XX_YELLOW);
    for (int dx = -1; dx <= 1; ++dx) {
      for (int dy = -1; dy <= 1; ++dy) {
        if (dx != 0 || dy != 0) {
          canvas.drawLine(x + dx * 12, y + dy * 12, x + dx * 16, y + dy * 16, ST77XX_YELLOW);
        }
      }
    }
  }
}

void drawClock(time_t now, bool connected) {
  char clockText[9] = "--:--:--";
  struct tm localTime;
  const bool valid = now >= EARLIEST_VALID_TIME && localtime_r(&now, &localTime) != nullptr;
  if (valid) {
    strftime(clockText, sizeof(clockText), "%H:%M:%S", &localTime);
    drawDayIcon(localTime.tm_hour);
  } else {
    centered(20, "?", ST77XX_YELLOW, 2);
  }
  centered(52, clockText, ST77XX_WHITE, 3);
  if (!credentialsConfigured) {
    centered(105, "SET WIFI IN secrets.h", ST77XX_YELLOW);
  } else if (!connected) {
    centered(105, "WIFI OFFLINE", ST77XX_YELLOW);
  } else if (!valid) {
    centered(105, "WAITING FOR NTP", ST77XX_YELLOW);
  }
}

void formatPair(char* output, size_t size, double first, double second) {
  constexpr double GIB = 1024.0 * 1024 * 1024;
  const bool tib = max(first, second) >= 1000 * GIB;
  const double unit = tib ? 1024 * GIB : GIB;
  snprintf(output, size, "%.1f / %.1f %s", first / unit, second / unit, tib ? "TiB" : "GiB");
}

void drawServer(uint32_t nowMs, time_t now, bool connected) {
  ServerSnapshot snapshot;
  if (serverQueue != nullptr) {
    xQueuePeek(serverQueue, &snapshot, 0);
  }
  const bool fresh = connected && metricsFresh(snapshot, nowMs, now);
  char text[40];
  centered(3, "SERVER", ST77XX_CYAN);
  if (fresh && isfinite(snapshot.cpu)) {
    snprintf(text, sizeof(text), "CPU %.1f%%", snapshot.cpu);
  } else {
    snprintf(text, sizeof(text), "CPU --");
  }
  textAt(3, 17, text, ST77XX_WHITE, 2);
  textAt(3, 40, "RAM used/free", ST77XX_CYAN);
  if (fresh) {
    formatPair(text, sizeof(text), snapshot.ramUsed, snapshot.ramFree);
  } else {
    snprintf(text, sizeof(text), "-- / -- GiB");
  }
  textAt(3, 51, text);
  textAt(3, 66, "DISK free/total", ST77XX_CYAN);
  if (fresh) {
    formatPair(text, sizeof(text), snapshot.diskFree, snapshot.diskTotal);
  } else {
    snprintf(text, sizeof(text), "-- / -- GiB");
  }
  textAt(3, 77, text);
  if (fresh && isfinite(snapshot.diskUtil)) {
    snprintf(text, sizeof(text), "DISK I/O %.1f%%", snapshot.diskUtil);
  } else {
    snprintf(text, sizeof(text), "DISK I/O --");
  }
  textAt(3, 91, text);
  const bool healthFresh = connected && snapshot.healthChecked &&
                          nowMs - snapshot.healthCheckedAt <= STALE_AFTER_MS;
  if (!healthFresh) {
    snprintf(text, sizeof(text), "HEALTH --");
  } else if (snapshot.healthCode > 0) {
    snprintf(text, sizeof(text), "HEALTH %s %d", snapshot.healthCode == 200 ? "OK" : "FAIL", snapshot.healthCode);
  } else {
    snprintf(text, sizeof(text), "HEALTH ERR %d", snapshot.healthCode);
  }
  textAt(3, 105, text, healthFresh && snapshot.healthCode == 200 ? ST77XX_GREEN : ST77XX_YELLOW);
  const char* status = "METRICS LIVE";
  if (!connected) {
    status = "WIFI OFFLINE";
  } else if (now < EARLIEST_VALID_TIME) {
    status = "WAITING FOR NTP";
  } else if (!monitorStarted) {
    status = "MONITOR START FAILED";
  } else if (strlen(MONITOR_TOKEN) == 0) {
    status = "SET MONITOR TOKEN";
  } else if (snapshot.metricsCode == 401 || snapshot.metricsCode == 403) {
    status = "METRICS AUTH ERROR";
  } else if (snapshot.metricsCode == 200 && !snapshot.metricsAttemptValid) {
    status = "METRICS INVALID";
  } else if (!fresh) {
    status = "NO FRESH METRICS";
  } else if (snapshot.metricsCode != 200) {
    status = "METRICS RETRYING";
  }
  centered(119, status, fresh && snapshot.metricsAttemptValid ? ST77XX_GREEN : ST77XX_YELLOW);
}

void setup() {
  Serial.begin(115200);
  SPI.begin(TFT_SCK, -1, TFT_MOSI, TFT_CS);
  display.initR(TFT_PROFILE);
  display.setRotation(TFT_ROTATION);
  display.setSPISpeed(8000000);
  display.fillScreen(ST77XX_BLACK);
  if (canvas.getBuffer() == nullptr) {
    Serial.println("Cannot allocate display buffer; restart the ESP32.");
    display.setCursor(0, 50);
    display.print("DISPLAY MEMORY ERROR");
    return;
  }
  canvas.setTextWrap(false);
  credentialsConfigured = strlen(WIFI_SSID) > 0 && strcmp(WIFI_SSID, "YOUR_WIFI_NAME") != 0;
  if (credentialsConfigured) {
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    lastWifiAttempt = millis();
    monitorStarted = startMonitor();
    if (!monitorStarted) {
      Serial.println("Failed to start monitor task; clock remains available.");
    }
  }
  screenStartedAt = millis();
}

void loop() {
  if (canvas.getBuffer() == nullptr) {
    delay(1000);
    return;
  }
  const uint32_t nowMs = millis();
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (credentialsConfigured && !connected && nowMs - lastWifiAttempt >= WIFI_RETRY_MS) {
    lastWifiAttempt = nowMs;
    WiFi.reconnect();
  }
  if (connected && !ntpStarted) {
    configTime(UTC_OFFSET_SECONDS, 0, "pool.ntp.org", "time.google.com", "time.cloudflare.com");
    ntpStarted = true;
  }
  const uint32_t duration = serverScreen ? SERVER_SCREEN_MS : CLOCK_SCREEN_MS;
  const bool switchScreen = nowMs - screenStartedAt >= duration;
  if (switchScreen) {
    serverScreen = !serverScreen;
    screenStartedAt = nowMs;
  }
  if (firstFrame || switchScreen || nowMs - lastFrameAt >= 250) {
    canvas.fillScreen(ST77XX_BLACK);
    const time_t now = time(nullptr);
    if (serverScreen) {
      drawServer(nowMs, now, connected);
    } else {
      drawClock(now, connected);
    }
    display.drawRGBBitmap(0, 0, canvas.getBuffer(), 160, 128);
    lastFrameAt = nowMs;
    firstFrame = false;
  }
  delay(20);
}
