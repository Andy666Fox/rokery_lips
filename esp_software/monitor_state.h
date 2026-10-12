#pragma once

#include <math.h>
#include <stdint.h>
#include <time.h>

constexpr uint32_t STALE_AFTER_MS = 20000;
constexpr time_t EARLIEST_VALID_TIME = 1704067200;

struct MetricsReading {
  double cpu = NAN;
  double ramUsed = 0;
  double ramFree = 0;
  double diskFree = 0;
  double diskTotal = 0;
  double diskUtil = NAN;
  time_t generatedAt = 0;
  // Receipt time of the last successful reading, never of a failed attempt.
  uint32_t metricsCheckedAt = 0;
  bool metricsValid = false;
};

struct ServerSnapshot : MetricsReading {
  uint32_t healthCheckedAt = 0;
  int metricsCode = 0;
  int healthCode = 0;
  bool metricsAttemptValid = false;
  bool healthChecked = false;
};

inline void applyMetricsResult(ServerSnapshot& snapshot, const MetricsReading& reading,
                               int status, uint32_t checkedAt) {
  snapshot.metricsCode = status;
  snapshot.metricsAttemptValid = status == 200 && reading.metricsValid;
  // A failed attempt must not erase a still-fresh reading or prolong its lifetime.
  if (snapshot.metricsAttemptValid) {
    static_cast<MetricsReading&>(snapshot) = reading;
    snapshot.metricsCheckedAt = checkedAt;
  }
}

inline bool metricsFresh(const ServerSnapshot& snapshot, uint32_t nowMs, time_t now) {
  return snapshot.metricsValid && nowMs - snapshot.metricsCheckedAt <= STALE_AFTER_MS &&
         now >= EARLIEST_VALID_TIME && snapshot.generatedAt <= now + 5 &&
         now - snapshot.generatedAt <= STALE_AFTER_MS / 1000;
}
