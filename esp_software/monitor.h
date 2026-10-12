#pragma once

#include <Arduino.h>
#include <HTTPClient.h>
#include <NetworkClientSecure.h>
#include <WiFi.h>
#include <cJSON.h>
#include <math.h>
#include <time.h>
#include "monitor_state.h"

#if __has_include("monitor_secrets.h")
#include "monitor_secrets.h"
#else
#include "monitor_secrets.example.h"
#endif

constexpr uint32_t POLL_INTERVAL_MS = 5000;

// The worker owns networking; loop() alone owns the display. Queue copies are atomic.
QueueHandle_t serverQueue = nullptr;

bool jsonNumber(const cJSON* json, const char* key, double& output, double maximum) {
  const cJSON* field = cJSON_GetObjectItemCaseSensitive(json, key);
  if (!cJSON_IsNumber(field) || !isfinite(field->valuedouble) ||
      field->valuedouble < 0 || field->valuedouble > maximum) {
    return false;
  }
  output = field->valuedouble;
  return true;
}

bool jsonPercent(const cJSON* json, const char* key, double& output) {
  const cJSON* field = cJSON_GetObjectItemCaseSensitive(json, key);
  if (cJSON_IsNull(field)) {
    output = NAN;
    return true;
  }
  return jsonNumber(json, key, output, 100);
}

bool parseMetrics(const char* body, size_t length, MetricsReading& result) {
  cJSON* json = cJSON_ParseWithLengthOpts(body, length + 1, nullptr, true);
  if (json == nullptr) {
    return false;
  }
  double version = 0;
  double generated = 0;
  constexpr double MAX_EXACT_INTEGER = 9007199254740991.0;
  const bool valid = cJSON_IsObject(json) &&
      jsonNumber(json, "schema_version", version, 1) && version == 1 &&
      jsonNumber(json, "generated_at", generated, 4102444800.0) &&
      generated >= EARLIEST_VALID_TIME && floor(generated) == generated &&
      jsonPercent(json, "cpu_percent", result.cpu) &&
      jsonPercent(json, "disk_util_percent", result.diskUtil) &&
      jsonNumber(json, "ram_used_bytes", result.ramUsed, MAX_EXACT_INTEGER) &&
      jsonNumber(json, "ram_free_bytes", result.ramFree, MAX_EXACT_INTEGER) &&
      result.ramUsed + result.ramFree > 0 &&
      jsonNumber(json, "disk_free_bytes", result.diskFree, MAX_EXACT_INTEGER) &&
      jsonNumber(json, "disk_total_bytes", result.diskTotal, MAX_EXACT_INTEGER) &&
      result.diskTotal > 0 && result.diskFree <= result.diskTotal;
  if (valid) {
    result.generatedAt = static_cast<time_t>(generated);
  }
  cJSON_Delete(json);
  return valid;
}

int request(const char* url, MetricsReading* metrics) {
  if (strncmp(url, "https://", 8) != 0) {
    return -100;
  }
  NetworkClientSecure client;
  client.useBuiltinCACertBundle();
  client.setHandshakeTimeout(8);
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  if (!http.begin(client, url)) {
    return -101;
  }
  if (metrics != nullptr) {
    http.addHeader("Authorization", String("Bearer ") + MONITOR_TOKEN);
  }
  const uint32_t started = millis();
  const int status = http.GET();
  if (metrics != nullptr && status == 200) {
    // Caddy serves a static snapshot with Content-Length. Bound allocation and read time.
    const int size = http.getSize();
    char body[1025];
    if (size > 0 && size < static_cast<int>(sizeof(body))) {
      const size_t received = http.getStream().readBytes(body, size);
      body[received] = '\0';
      metrics->metricsValid = received == static_cast<size_t>(size) &&
                              parseMetrics(body, received, *metrics);
    }
  }
  // Keep credentials and URLs out of logs; retain transport details for USB diagnosis.
  Serial.printf("%s HTTP %d duration=%lu ms RSSI=%d heap=%lu max-block=%lu\n",
                metrics != nullptr ? "METRICS" : "HEALTH", status,
                static_cast<unsigned long>(millis() - started), WiFi.RSSI(),
                static_cast<unsigned long>(ESP.getFreeHeap()),
                static_cast<unsigned long>(ESP.getMaxAllocHeap()));
  if (status <= 0) {
    char error[128];
    const int tlsError = client.lastError(error, sizeof(error));
    Serial.printf("Transport: %s; TLS %d: %s\n",
                  HTTPClient::errorToString(status).c_str(), tlsError, error);
  } else if (metrics != nullptr && status == 200 && !metrics->metricsValid) {
    Serial.printf("METRICS invalid/incomplete body; Content-Length=%d\n", http.getSize());
  } else if (metrics != nullptr && metrics->metricsValid) {
    Serial.printf("METRICS age=%lld s\n",
                  static_cast<long long>(time(nullptr) - metrics->generatedAt));
  }
  http.end();
  return status;
}

void monitorWorker(void*) {
  ServerSnapshot snapshot;
  for (;;) {
    const uint32_t started = millis();
    if (WiFi.status() == WL_CONNECTED && time(nullptr) >= EARLIEST_VALID_TIME) {
      // Publish the useful reading before spending time on the independent health check.
      if (strlen(MONITOR_TOKEN) > 0) {
        MetricsReading reading;
        const int status = request(MONITOR_URL, &reading);
        applyMetricsResult(snapshot, reading, status, millis());
        xQueueOverwrite(serverQueue, &snapshot);
      }
      snapshot.healthCode = request(HEALTH_URL, nullptr);
      snapshot.healthChecked = true;
      snapshot.healthCheckedAt = millis();
      xQueueOverwrite(serverQueue, &snapshot);
    }
    const uint32_t elapsed = millis() - started;
    vTaskDelay(pdMS_TO_TICKS(elapsed < POLL_INTERVAL_MS ? POLL_INTERVAL_MS - elapsed : 100));
  }
}

bool startMonitor() {
  serverQueue = xQueueCreate(1, sizeof(ServerSnapshot));
  if (serverQueue == nullptr) {
    return false;
  }
  if (xTaskCreate(monitorWorker, "server-monitor", 12288, nullptr, 1, nullptr) != pdPASS) {
    vQueueDelete(serverQueue);
    serverQueue = nullptr;
    return false;
  }
  return true;
}
