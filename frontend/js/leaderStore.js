/**
 * Alpine.store('leader') — 주도주 랭킹 상태
 * /api/v1/leaders/top 를 5분마다 폴링 (장중에만).
 */

document.addEventListener('alpine:init', () => {
  Alpine.store('leader', {
    loading: false,
    leaders: [],
    ts: null,
    lastUpdated: null,
    _pollTimer: null,
    _pollDelayMs: 5000,

    async init() {
      this._pollLatest();
    },

    _schedulePoll() {
      clearTimeout(this._pollTimer);
      this._pollTimer = setTimeout(() => this._pollLatest(), this._pollDelayMs);
    },

    async _pollLatest() {
      this.loading = true;
      try {
        const resp = await fetch('/api/v1/leaders/top?n=50');
        if (!resp.ok) {
          this._pollDelayMs = 15000;
          return;
        }
        const data = await resp.json();
        this.leaders = data.leaders || [];
        this.ts = data.ts || null;
        if (this.ts) {
          const d = new Date(this.ts * 1000);
          const pad = n => String(n).padStart(2, '0');
          this.lastUpdated = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        }
        this._pollDelayMs = 300000; // 5분
      } catch (e) {
        console.warn('Leader polling error:', e);
        this._pollDelayMs = 15000;
      } finally {
        this.loading = false;
        this._schedulePoll();
      }
    },

    // Formatters
    fmtScore(v) {
      if (v === null || v === undefined) return '—';
      return v.toFixed(1);
    },
    fmtPct(v) {
      if (v === null || v === undefined) return '—';
      const sign = v >= 0 ? '+' : '';
      return `${sign}${v.toFixed(2)}%`;
    },
    fmtSurge(v) {
      if (v === null || v === undefined) return '—';
      const pct = (v * 100).toFixed(0);
      return `+${pct}%`;
    },
    changeClass(v) {
      if (v === null || v === undefined) return 'change--neutral';
      return v > 0 ? 'change--up' : v < 0 ? 'change--down' : 'change--neutral';
    },
    sectorLabel(cat) {
      const m = {
        semiconductor: '반도체',
        auto:          '자동차',
        beauty:        '화학/뷰티',
        consumer:      '소비재',
        energy:        '에너지',
        finance:       '금융',
      };
      return m[cat] || cat || '—';
    },
  });
});
