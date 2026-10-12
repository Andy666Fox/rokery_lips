#include "../../esp_software/monitor_state.h"

#include <stdio.h>
#include <stdlib.h>

void check(bool condition, const char* message) {
  if (!condition) {
    fprintf(stderr, "FAIL: %s\n", message);
    exit(1);
  }
}

int main() {
  constexpr time_t NOW = EARLIEST_VALID_TIME + 100;
  ServerSnapshot snapshot;
  check(!metricsFresh(snapshot, 1000, NOW), "no reading before first success");
  snapshot.healthCode = 503;
  snapshot.healthChecked = true;
  snapshot.healthCheckedAt = 900;

  MetricsReading reading;
  reading.cpu = 42;
  reading.generatedAt = NOW;
  reading.metricsValid = true;
  applyMetricsResult(snapshot, reading, 200, 1000);
  check(metricsFresh(snapshot, 1000, NOW), "successful reading becomes visible");
  check(snapshot.healthCode == 503 && snapshot.healthCheckedAt == 900,
        "metrics update preserves independent health result");

  MetricsReading failed;
  failed.cpu = 99;  // An invalid response may contain partially parsed values.
  applyMetricsResult(snapshot, failed, -1, 6000);
  check(metricsFresh(snapshot, 6000, NOW + 5), "connection failure preserves fresh reading");
  check(snapshot.cpu == 42 && snapshot.metricsCheckedAt == 1000,
        "failure neither replaces values nor extends their lifetime");
  check(snapshot.metricsCode == -1 && !snapshot.metricsAttemptValid,
        "latest failure remains observable while cached values are fresh");
  applyMetricsResult(snapshot, failed, 200, 11000);
  check(metricsFresh(snapshot, 11000, NOW + 10) && snapshot.cpu == 42,
        "malformed HTTP 200 preserves good values");
  applyMetricsResult(snapshot, failed, 401, 16000);
  check(snapshot.metricsCode == 401 && !snapshot.metricsAttemptValid,
        "authentication failure remains distinguishable");
  check(metricsFresh(snapshot, 21000, NOW + 20), "reading is fresh at 20 second boundary");
  check(!metricsFresh(snapshot, 21001, NOW + 20), "receipt expires after 20 seconds");
  check(!metricsFresh(snapshot, 2000, NOW + 21), "upstream timestamp also expires");
  check(!metricsFresh(snapshot, 2000, EARLIEST_VALID_TIME - 1), "unsynchronized clock rejected");
  check(metricsFresh(snapshot, 2000, NOW - 5), "five seconds of clock skew accepted");
  check(!metricsFresh(snapshot, 2000, NOW - 6), "future upstream timestamp rejected");

  reading.cpu = 7;
  reading.generatedAt = NOW + 22;
  applyMetricsResult(snapshot, reading, 200, 23000);
  check(metricsFresh(snapshot, 23000, NOW + 22) && snapshot.cpu == 7 &&
        snapshot.metricsAttemptValid, "successful retry replaces expired reading");
  applyMetricsResult(snapshot, reading, 200, UINT32_MAX - 1000);
  check(metricsFresh(snapshot, 999, NOW + 22), "millis wrap preserves elapsed-time comparison");
  check(!metricsFresh(snapshot, 20000, NOW + 22), "millis wrap still expires old readings");
  puts("PASS: monitor cache, failures, recovery, freshness and clock boundaries");
}
