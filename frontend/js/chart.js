/**
 * lightweight-charts integration for KOSPI night futures intraday chart
 */

let chart = null;
let lineSeries = null;
let lastPointTime = 0;  // track last added time to guard out-of-order points

/**
 * Convert UTC ISO string or Unix seconds to KST Unix seconds for lightweight-charts.
 * lightweight-charts v4 is timezone-unaware — adding 9h (=9*3600s) shifts display to KST.
 */
function toKST(ts) {
  const unixSec = typeof ts === 'string'
    ? Math.floor(new Date(ts).getTime() / 1000)
    : Math.floor(ts);
  return unixSec + 9 * 3600;
}

export function initChart(containerId) {
  const container = document.getElementById(containerId);
  if (!container || !window.LightweightCharts) return;

  chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight,
    layout: {
      background: { type: 'solid', color: '#1E293B' },
      textColor: '#a0a0b0',
    },
    grid: {
      vertLines: { color: 'rgba(255,255,255,0.05)' },
      horzLines: { color: 'rgba(255,255,255,0.05)' },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
    },
    rightPriceScale: {
      borderColor: 'rgba(255,255,255,0.1)',
    },
    timeScale: {
      borderColor: 'rgba(255,255,255,0.1)',
      timeVisible: true,
      secondsVisible: false,
    },
  });

  lineSeries = chart.addLineSeries({
    color: '#4fc3f7',
    lineWidth: 2,
    crosshairMarkerVisible: true,
    lastValueVisible: true,
    priceLineVisible: true,
  });

  // Responsive resize
  const resizeObserver = new ResizeObserver(entries => {
    for (const entry of entries) {
      chart.applyOptions({ width: entry.contentRect.width });
    }
  });
  resizeObserver.observe(container);
}

export function addChartPoint(timestamp, price) {
  if (!lineSeries) { console.warn('[chart] lineSeries not ready'); return; }

  const time = toKST(timestamp);
  if (time < lastPointTime) { console.warn('[chart] out-of-order', time, lastPointTime); return; }

  try {
    lineSeries.update({ time, value: price });
    lastPointTime = time;
  } catch(e) {
    console.error('[chart] update error', e.message, 'time=', time, 'last=', lastPointTime);
  }
}

/**
 * Load bulk historical data and fit chart to show all of it.
 * Data fills left (oldest) → right (newest).
 */
export function loadChartHistory(ticks) {
  if (!lineSeries || !chart) return;
  const data = ticks
    .map(t => ({ time: toKST(t.timestamp), value: t.price }))
    .filter(d => d.time > 0)
    .sort((a, b) => a.time - b.time);
  if (data.length === 0) return;
  lineSeries.setData(data);
  lastPointTime = data[data.length - 1].time;
  chart.timeScale().fitContent();
}

export function resetChart() {
  lastPointTime = 0;
  lineSeries?.setData([]);
}
