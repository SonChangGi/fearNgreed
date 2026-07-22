export const HISTORY_SERIES_IDS = Object.freeze(["kospi", "long_cash", "long_inverse_cash", "buyhold"]);

const DEFAULT_PREFERENCE = Object.freeze(["long_inverse_cash", "long_cash", "buyhold", "kospi"]);

export function normalizeHistorySeries(seriesId, visibleSeriesIds) {
  const visible = new Set(visibleSeriesIds);
  if (visible.has(seriesId)) return seriesId;
  return DEFAULT_PREFERENCE.find((candidate) => visible.has(candidate)) || "kospi";
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
