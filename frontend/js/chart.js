/**
 * lightweight-charts integration for KOSPI night futures intraday chart
 */

let chart = null;
let lineSeries = null;
const chartData = [];

export function initChart(containerId) {
  const container = document.getElementById(containerId);
  if (!container || !window.LightweightCharts) return;

  chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight,
    layout: {
      background: { type: 'solid', color: '#0f3460' },
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
  if (!lineSeries) return;

  const time = Math.floor(new Date(timestamp).getTime() / 1000);
  chartData.push({ time, value: price });

  // Keep sorted and deduplicated
  chartData.sort((a, b) => a.time - b.time);
  const seen = new Set();
  const deduped = chartData.filter(d => {
    if (seen.has(d.time)) return false;
    seen.add(d.time);
    return true;
  });

  lineSeries.setData(deduped);
  chart?.timeScale().scrollToRealTime();
}

export function resetChart() {
  chartData.length = 0;
  lineSeries?.setData([]);
}
