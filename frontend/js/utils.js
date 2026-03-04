/**
 * Number formatting utilities for Korean financial data
 */

export function formatPrice(price) {
  if (!price && price !== 0) return '—';
  return new Intl.NumberFormat('ko-KR', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(price);
}

export function formatVolume(volume) {
  if (!volume && volume !== 0) return '—';
  if (volume >= 1000000) return (volume / 1000000).toFixed(1) + 'M';
  if (volume >= 1000) return (volume / 1000).toFixed(1) + 'K';
  return volume.toLocaleString('ko-KR');
}

export function formatChange(change, pct) {
  if (change === undefined || change === null) return { text: '—', direction: 'neutral' };
  const sign = change > 0 ? '+' : '';
  const direction = change > 0 ? 'up' : change < 0 ? 'down' : 'neutral';
  const text = `${sign}${formatPrice(change)} (${sign}${pct?.toFixed(2) ?? '0.00'}%)`;
  return { text, direction };
}

export function formatTime(isoString) {
  if (!isoString) return '—';
  const d = new Date(isoString);
  return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function sessionLabel(sessionName) {
  const labels = {
    night: '야간장 운영중',
    auction_pre: '단일가 매매 (장전)',
    auction_close: '단일가 매매 (장마감)',
    day: '정규장',
    closed: '장 마감',
  };
  return labels[sessionName] || sessionName;
}

export function connectionLabel(state) {
  const labels = {
    connected: '연결됨',
    reconnecting: '재연결 중',
    stale: '데이터 지연',
    disconnected: '연결 끊김',
  };
  return labels[state] || state;
}
