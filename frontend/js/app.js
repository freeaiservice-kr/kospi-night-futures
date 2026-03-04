/**
 * Alpine.js application — KOSPI night futures dashboard
 */

import { formatPrice, formatVolume, sessionLabel, connectionLabel } from './utils.js';
import { initChart, addChartPoint, resetChart, loadChartHistory } from './chart.js';

function futuresApp() {
  return {
    // Price state
    price: null,
    change: null,
    changePct: null,
    volume: null,
    openPrice: null,
    highPrice: null,
    lowPrice: null,
    lastTimestamp: null,
    priceDirection: 'neutral', // 'up' | 'down' | 'neutral'
    symbol: '—',

    // Session state
    sessionName: 'closed',
    isMarketOpen: false,

    // Connection state
    connectionState: 'disconnected', // 'connected' | 'reconnecting' | 'stale' | 'disconnected'
    isStale: false,

    // WebSocket
    _ws: null,
    _reconnectDelay: 1000,
    _reconnectTimer: null,
    _staleTimer: null,

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

    init() {
      initChart('chart');
      this._connect();
      this._pollMarketStatus();
    },

    _wsUrl() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${proto}//${location.host}/ws/futures`;
    },

    _connect() {
      if (this._ws) {
        this._ws.onclose = null;
        this._ws.close();
      }

      this.connectionState = 'reconnecting';
      const ws = new WebSocket(this._wsUrl());
      this._ws = ws;

      ws.onopen = () => {
        this.connectionState = 'connected';
        this._reconnectDelay = 1000;
        this._resetStaleTimer();
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          this._handleMessage(msg);
        } catch (e) {
          console.warn('WS parse error:', e);
        }
      };

      ws.onclose = () => {
        if (this.connectionState !== 'stale') {
          this.connectionState = 'reconnecting';
        }
        this._scheduleReconnect();
      };

      ws.onerror = () => {
        ws.close();
      };
    },

    _handleMessage(msg) {
      if (msg.type === 'quote') {
        const d = msg.data;
        const prevPrice = this.price;
        this.price = d.price;
        this.change = d.change;
        this.changePct = d.change_pct;
        this.volume = d.volume;
        this.openPrice = d.open_price;
        this.highPrice = d.high_price;
        this.lowPrice = d.low_price;
        this.lastTimestamp = d.timestamp;
        this.symbol = d.symbol;

        if (prevPrice !== null) {
          this.priceDirection = d.price > prevPrice ? 'up' : d.price < prevPrice ? 'down' : 'neutral';
        } else {
          this.priceDirection = d.change > 0 ? 'up' : d.change < 0 ? 'down' : 'neutral';
        }

        this.isStale = false;
        this.connectionState = 'connected';
        this._resetStaleTimer();
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

    _scheduleReconnect() {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = setTimeout(() => {
        this._connect();
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, 60000);
      }, this._reconnectDelay);
    },

    _resetStaleTimer() {
      clearTimeout(this._staleTimer);
      this._staleTimer = setTimeout(() => {
        if (this.isMarketOpen) {
          this.isStale = true;
          this.connectionState = 'stale';
        }
      }, 60000);
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
      setTimeout(() => this._pollMarketStatus(), 60000);
    },
  };
}

// Register with Alpine
document.addEventListener('alpine:init', () => {
  Alpine.data('futuresApp', futuresApp);
});
