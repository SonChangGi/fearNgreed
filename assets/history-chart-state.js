export const HISTORY_SERIES_IDS = Object.freeze(["kospi", "long_cash", "long_inverse_cash", "buyhold"]);

const DEFAULT_PREFERENCE = Object.freeze(["long_inverse_cash", "long_cash", "buyhold", "kospi"]);

export function normalizeHistorySeries(seriesId, visibleSeriesIds) {
  const visible = new Set(visibleSeriesIds);
  if (visible.has(seriesId)) return seriesId;
  return DEFAULT_PREFERENCE.find((candidate) => visible.has(candidate)) || "kospi";
}

export function normalizeHistoryRange(length, startIndex, endIndex) {
  const size = Number(length);
  if (!Number.isInteger(size) || size <= 0 || startIndex == null || endIndex == null || !Number.isFinite(Number(startIndex)) || !Number.isFinite(Number(endIndex))) return null;
  const clamp = (value) => Math.max(0, Math.min(size - 1, Math.trunc(Number(value))));
  const first = clamp(startIndex);
  const last = clamp(endIndex);
  const normalizedStart = Math.min(first, last);
  const normalizedEnd = Math.max(first, last);
  return {
    startIndex: normalizedStart,
    endIndex: normalizedEnd,
    count: normalizedEnd - normalizedStart + 1
  };
}

export function relativeReturn(startValue, endValue) {
  if (startValue == null || endValue == null) return null;
  const start = Number(startValue);
  const end = Number(endValue);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start === 0) return null;
  return end / start - 1;
}

export function historyIntervalSnapshot(rows, startIndex, endIndex, {
  showLongCash = true,
  showLongShort = true
} = {}) {
  const range = normalizeHistoryRange(rows?.length || 0, startIndex, endIndex);
  if (!range) return null;
  const start = rows[range.startIndex];
  const end = rows[range.endIndex];
  const kospiStartValue = start?.kospiClose ?? start?.kospi;
  const kospiEndValue = end?.kospiClose ?? end?.kospi;
  const kospiStart = kospiStartValue == null ? Number.NaN : Number(kospiStartValue);
  const kospiEnd = kospiEndValue == null ? Number.NaN : Number(kospiEndValue);
  const returns = {
    kospi: relativeReturn(kospiStart, kospiEnd),
    ...(showLongCash ? { long_cash: relativeReturn(start?.longCashValue, end?.longCashValue) } : {}),
    ...(showLongShort ? { long_inverse_cash: relativeReturn(start?.longShortValue, end?.longShortValue) } : {}),
    buyhold: relativeReturn(start?.buyHoldValue, end?.buyHoldValue)
  };
  return {
    ...range,
    startDate: start?.date || "",
    endDate: end?.date || "",
    kospiStart,
    kospiEnd,
    returns
  };
}

export function createHistoryChartState(initialSeries = "long_inverse_cash") {
  let activeSeries = HISTORY_SERIES_IDS.includes(initialSeries) ? initialSeries : "long_inverse_cash";
  return {
    get activeSeries() {
      return activeSeries;
    },
    normalize(visibleSeriesIds) {
      activeSeries = normalizeHistorySeries(activeSeries, visibleSeriesIds);
      return activeSeries;
    },
    activate(seriesId, visibleSeriesIds) {
      if (visibleSeriesIds.includes(seriesId)) activeSeries = seriesId;
      return activeSeries;
    },
    preview(seriesId, visibleSeriesIds) {
      return visibleSeriesIds.includes(seriesId) ? seriesId : activeSeries;
    }
  };
}
