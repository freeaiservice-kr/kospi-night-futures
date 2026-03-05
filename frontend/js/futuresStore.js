/**
 * Alpine.store('futures') — futures WebSocket state + computed
 */

import { formatPrice, formatVolume, sessionLabel, connectionLabel } from './utils.js';
import { initChart, addChartPoint, resetChart, loadChartHistory } from './chart.js';

const FUTURES_POLL_MS = 2000;
const FUTURES_STALE_MS = 10000;

document.addEventListener('alpine:init', () => {
  Alpine.store('futures', {
    // Price state
    price: null,
    change: null,
    changePct: null,
    volume: null,
    openPrice: null,
    highPrice: null,
    lowPrice: null,
    lastTimestamp: null,
    priceDirection: 'neutral',
    symbol: '—',

    // Additional indicators
    cttr: null,
    basis: null,
    openInterest: null,
    oiChange: null,

    // 5-level orderbook
    asks: [],
    bids: [],
    totalAskQty: 0,
    totalBidQty: 0,

    // Session
    sessionName: 'closed',
    isMarketOpen: false,

    // Connection
    connectionState: 'disconnected',
    isStale: false,
    _pollTimer: null,
    _staleTimer: null,
    _marketStatusTimer: null,

    // Computed
    get formattedPrice() { return this.price !== null ? formatPrice(this.price) : '—'; },
    get formattedChange() {
      if (this.change === null) return '—';
      const sign = this.change >= 0 ? '+' : '';
      return `${sign}${formatPrice(this.change)}`;
    },
    get formattedChangePct() {
      if (this.changePct === null) return '—';
      const sign = this.changePct >= 0 ? '+' : '';
      return `${sign}${this.changePct.toFixed(2)}%`;
    },
    get formattedVolume() { return this.volume !== null ? formatVolume(this.volume) : '—'; },
    get formattedHigh() { return this.highPrice !== null ? formatPrice(this.highPrice) : '—'; },
    get formattedLow() { return this.lowPrice !== null ? formatPrice(this.lowPrice) : '—'; },
    get formattedOpen() { return this.openPrice !== null ? formatPrice(this.openPrice) : '—'; },
    get sessionLabel() { return sessionLabel(this.sessionName); },
    get connectionLabel() { return connectionLabel(this.connectionState); },
    get cttrClass() {
      if (this.cttr === null) return 'indicator-item__value--neutral';
      return this.cttr >= 50 ? 'indicator-item__value--up' : 'indicator-item__value--down';
    },
    get basisClass() {
      if (this.basis === null) return 'indicator-item__value--neutral';
      return this.basis > 0 ? 'indicator-item__value--up' : this.basis < 0 ? 'indicator-item__value--down' : 'indicator-item__value--neutral';
    },
    get bidAskRatio() {
      if (this.totalAskQty === 0) return null;
      return (this.totalBidQty / this.totalAskQty).toFixed(2);
    },

    // Init
    init() {
      initChart('chart');
      this._startPolling();
      this._pollMarketStatus();
      this._loadChartHistory();
    },

    _startPolling() {
      clearTimeout(this._pollTimer);
      this.connectionState = 'reconnecting';
      this._pollFutures();
    },

    async _pollFutures() {
      try {
        const resp = await fetch('/api/v1/futures/latest');
        if (!resp.ok) {
          this.connectionState = this.isMarketOpen ? 'reconnecting' : 'disconnected';
          return;
        }

        const data = await resp.json();
        this._applyLatestPayload(data);
        this.connectionState = 'connected';
      } catch (e) {
        console.warn('Futures polling error:', e);
        this.connectionState = this.isMarketOpen ? 'reconnecting' : 'disconnected';
      } finally {
        clearTimeout(this._pollTimer);
        this._pollTimer = setTimeout(() => this._pollFutures(), FUTURES_POLL_MS);
      }
    },

    _applyLatestPayload(msg) {
      if (msg.type === 'quote') {
        const d = msg.data;
        const timestamp = d.timestamp || new Date().toISOString();
        const prevPrice = this.price;
        this.price = d.price;
        this.change = d.change;
        this.changePct = d.change_pct;
        this.volume = d.volume;
        this.openPrice = d.open_price;
        this.highPrice = d.high_price;
        this.lowPrice = d.low_price;
        this.lastTimestamp = timestamp;
        this.symbol = d.symbol;
        if (d.cttr !== undefined) this.cttr = d.cttr;
        if (d.basis !== undefined) this.basis = d.basis;
        if (d.open_interest !== undefined) this.openInterest = d.open_interest;
        if (d.oi_change !== undefined) this.oiChange = d.oi_change;

        if (prevPrice !== null) {
          this.priceDirection = d.price > prevPrice ? 'up' : d.price < prevPrice ? 'down' : 'neutral';
        } else {
          this.priceDirection = d.change > 0 ? 'up' : d.change < 0 ? 'down' : 'neutral';
        }
        this.isStale = false;
        this.connectionState = 'connected';
        addChartPoint(timestamp, this.price);
        this._resetStaleTimer();
      } else if (msg.type === 'orderbook') {
        this.asks = msg.asks || [];
        this.bids = msg.bids || [];
        this.totalAskQty = msg.total_ask_qty || 0;
        this.totalBidQty = msg.total_bid_qty || 0;
      } else if (msg.type === 'chart_tick') {
        addChartPoint(msg.timestamp, msg.price);
      } else if (msg.type === 'chart_history') {
        loadChartHistory(msg.ticks);
      } else if (msg.type === 'rollover') {
        this.symbol = msg.symbol || this.symbol;
        resetChart();
      } else if (msg.type === 'status' && msg.state === 'stale') {
        this.isStale = true;
        this.connectionState = 'stale';
      } else if (msg.type === 'connected') {
        this.symbol = msg.symbol || this.symbol;
      } else if (msg.type === 'pong') {
        // heartbeat ok
      }
    },

    async _loadChartHistory() {
      try {
        const resp = await fetch('/api/v1/options/futures-history?limit=120');
        if (!resp.ok) return;
        const data = await resp.json();
        const rows = (data.rows || []).map((row) => {
          if (row.timestamp) {
            return row;
          }
          if (!row.ts) return null;
          return {
            ...row,
            timestamp: new Date(row.ts * 1000).toISOString(),
          };
        }).filter(Boolean);
        loadChartHistory(rows);
      } catch (e) {
        console.warn('Chart history load error:', e);
      }
    },

    _resetStaleTimer() {
      clearTimeout(this._staleTimer);
      this._staleTimer = setTimeout(() => {
        if (this.isMarketOpen) {
          this.isStale = true;
          this.connectionState = 'stale';
        }
      }, FUTURES_STALE_MS);
    },

    async _pollMarketStatus() {
      try {
        const resp = await fetch('/api/v1/futures/status');
        if (resp.ok) {
          const data = await resp.json();
          this.sessionName = data.session_name;
          this.isMarketOpen = data.is_open;
        }
      } catch (e) {
        console.warn('Market status poll error:', e);
      }
      clearTimeout(this._marketStatusTimer);
      this._marketStatusTimer = setTimeout(() => this._pollMarketStatus(), 60000);
    },
  });
});
