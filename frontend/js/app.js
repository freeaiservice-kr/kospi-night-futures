/**
 * Alpine.js application — unified dashboard (야간선물 + 옵션전광판)
 */

import { formatPrice, formatVolume, sessionLabel, connectionLabel } from './utils.js';
import { initChart, addChartPoint, resetChart, loadChartHistory } from './chart.js';

function dashboardApp() {
  return {
    // ── Tab ──────────────────────────────────────────────────────────────
    activeTab: 'futures',

    // ── Futures: price state ─────────────────────────────────────────────
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

    // Futures: additional indicators
    cttr: null,
    basis: null,
    openInterest: null,
    oiChange: null,

    // Futures: 5-level orderbook
    asks: [],
    bids: [],
    totalAskQty: 0,
    totalBidQty: 0,

    // Futures: session
    sessionName: 'closed',
    isMarketOpen: false,

    // Futures: connection
    connectionState: 'disconnected',
    isStale: false,
    _ws: null,
    _reconnectDelay: 1000,
    _reconnectTimer: null,
    _staleTimer: null,
    _pingTimer: null,

    // ── Options: connection ───────────────────────────────────────────────
    optConnectionState: 'disconnected',
    _optWs: null,
    _optReconnectDelay: 1000,
    _optReconnectTimer: null,
    _optPingTimer: null,

    // Options: market
    optIsMarketOpen: false,

    // Options: product selection
    activeProduct: 'WKI',
    productLabels: {
      'WKI': '위클리(목)',
      'WKM': '위클리(월)',
      'KOSPI200': 'KOSPI200',
      'MKI': '미니',
      'KQI': 'KOSDAQ150',
    },

    // Options: board data
    rawCalls: [],
    rawPuts: [],
    expiryDisplay: '—',
    expiryCode: '—',
    lastUpdated: null,

    // Options: KOSPI200 underlying
    futuresPrice: null,
    futuresChange: null,
    futuresChangePct: null,
    futuresSymbol: '—',
    futuresHigh: null,
    futuresLow: null,
    futuresOpen: null,

    // Options: investor flow
    callInvestor: {},
    putInvestor: {},
    investorHistory: [],
    futuresHistory: [],

    // ── Futures: computed ────────────────────────────────────────────────
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

    // ── Options: computed ────────────────────────────────────────────────
    get futuresPriceFormatted() {
      if (this.futuresPrice === null) return '—';
      return this.futuresPrice.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    },
    get futuresChangeFormatted() {
      if (this.futuresChange === null) return '—';
      const sign = this.futuresChange >= 0 ? '+' : '';
      return `${sign}${this.futuresChange.toFixed(2)}`;
    },
    get futuresChangePctFormatted() {
      if (this.futuresChangePct === null) return '';
      const sign = this.futuresChangePct >= 0 ? '+' : '';
      return `(${sign}${this.futuresChangePct.toFixed(2)}%)`;
    },
    get futuresChangeClass() {
      if (this.futuresChange === null) return 'change--neutral';
      return this.futuresChange > 0 ? 'change--up' : this.futuresChange < 0 ? 'change--down' : 'change--neutral';
    },
    get activeProductLabel() {
      return this.productLabels[this.activeProduct] || this.activeProduct;
    },
    get hasInvestorData() {
      return Object.keys(this.callInvestor).length > 0;
    },
    get board() {
      const callMap = {};
      for (const c of this.rawCalls) callMap[c.acpr] = c;
      const putMap = {};
      for (const p of this.rawPuts) putMap[p.acpr] = p;
      const strikes = new Set([...Object.keys(callMap), ...Object.keys(putMap)]);
      const rows = [];
      for (const strike of strikes) {
        const call = callMap[strike] || {};
        const put = putMap[strike] || {};
        const callVol = parseInt(call.acml_vol || '0');
        const putVol = parseInt(put.acml_vol || '0');
        const isAtm = call.atm_cls_name === 'ATM' || put.atm_cls_name === 'ATM';
        if (callVol > 0 || putVol > 0 || isAtm) {
          rows.push({ strike, call, put });
        }
      }
      rows.sort((a, b) => parseFloat(b.strike) - parseFloat(a.strike));
      return rows;
    },
    get activeOptions() {
      const list = [];
      for (const c of this.rawCalls) {
        const vol = parseInt(c.acml_vol || '0');
        if (vol > 0) list.push({ kind: '콜', kindClass: 'call-price', ...c, _vol: vol });
      }
      for (const p of this.rawPuts) {
        const vol = parseInt(p.acml_vol || '0');
        if (vol > 0) list.push({ kind: '풋', kindClass: 'put-price', ...p, _vol: vol });
      }
      return list.sort((a, b) => b._vol - a._vol).slice(0, 15);
    },
    get pcRatio() {
      const callVol = this.rawCalls.reduce((s, c) => s + parseInt(c.acml_vol || 0), 0);
      const putVol = this.rawPuts.reduce((s, p) => s + parseInt(p.acml_vol || 0), 0);
      if (callVol === 0) return '—';
      return (putVol / callVol).toFixed(2);
    },
    get pcRatioClass() {
      const callVol = this.rawCalls.reduce((s, c) => s + parseInt(c.acml_vol || 0), 0);
      const putVol = this.rawPuts.reduce((s, p) => s + parseInt(p.acml_vol || 0), 0);
      if (callVol === 0) return 'pc-ratio--neutral';
      const r = putVol / callVol;
      if (r > 1.5) return 'pc-ratio--bearish';
      if (r < 0.7) return 'pc-ratio--bullish';
      return 'pc-ratio--neutral';
    },
    get topVolumeStrike() {
      let maxVol = 0, maxStrike = '—';
      for (const { strike, call, put } of this.board) {
        const vol = parseInt(call.acml_vol || 0) + parseInt(put.acml_vol || 0);
        if (vol > maxVol) { maxVol = vol; maxStrike = strike; }
      }
      return maxStrike;
    },

    // ── Init ─────────────────────────────────────────────────────────────
    init() {
      initChart('chart');
      this._connect();
      this._pollMarketStatus();
      this._connectOptions();
      this._pollOptionsStatus();
      this._loadInvestorHistory();
      this._loadFuturesHistory();
    },

    // ── Futures: WebSocket ────────────────────────────────────────────────
    _wsUrl() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${proto}//${location.host}/ws/futures`;
    },

    _connect() {
      if (this._ws) { this._ws.onclose = null; this._ws.close(); }
      clearInterval(this._pingTimer);
      this.connectionState = 'reconnecting';
      const ws = new WebSocket(this._wsUrl());
      this._ws = ws;

      ws.onopen = () => {
        this.connectionState = 'connected';
        this._reconnectDelay = 1000;
        this._resetStaleTimer();
        this._pingTimer = setInterval(() => {
          if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            this._ws.send('ping');
          }
        }, 30000);
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
        clearInterval(this._pingTimer);
        if (this.connectionState !== 'stale') {
          this.connectionState = 'reconnecting';
        }
        this._scheduleReconnect();
      };

      ws.onerror = () => ws.close();
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

    // ── Options: WebSocket ────────────────────────────────────────────────
    _optWsUrl() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${proto}//${location.host}/ws/options?product=${this.activeProduct}`;
    },

    _connectOptions() {
      if (this._optWs) { this._optWs.onclose = null; this._optWs.close(); }
      clearInterval(this._optPingTimer);
      this.optConnectionState = 'reconnecting';
      const ws = new WebSocket(this._optWsUrl());
      this._optWs = ws;

      ws.onopen = () => {
        this.optConnectionState = 'connected';
        this._optReconnectDelay = 1000;
        this._optPingTimer = setInterval(() => {
          if (this._optWs && this._optWs.readyState === WebSocket.OPEN) {
            this._optWs.send('ping');
          }
        }, 30000);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          this._handleOptionsMessage(msg);
        } catch (e) {
          console.warn('Options WS parse error:', e);
        }
      };

      ws.onclose = () => {
        clearInterval(this._optPingTimer);
        this.optConnectionState = 'reconnecting';
        this._scheduleOptReconnect();
      };

      ws.onerror = () => ws.close();
    },

    _handleOptionsMessage(msg) {
      if (msg.type === 'options_board') {
        if (msg.product && msg.product !== this.activeProduct) return;
        this.rawCalls = msg.calls || [];
        this.rawPuts = msg.puts || [];
        this.expiryCode = msg.expiry || '—';
        if (msg.expiry_date) {
          const d = new Date(msg.expiry_date);
          const days = ['일', '월', '화', '수', '목', '금', '토'];
          this.expiryDisplay = `${msg.expiry_date.replace(/-/g, '/')}(${days[d.getDay()]})`;
        } else {
          this.expiryDisplay = msg.expiry || '—';
        }
        if (msg.updated_at) this.lastUpdated = msg.updated_at;
      } else if (msg.type === 'investor_flow') {
        this.callInvestor = msg.call_investor || {};
        this.putInvestor = msg.put_investor || {};
        const ci = this.callInvestor, pi = this.putInvestor;
        const now = new Date();
        const pad = n => String(n).padStart(2, '0');
        const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
        const row = {
          ts: Date.now(), time: timeStr,
          call_frgn: parseInt(ci.frgn_ntby || 0),
          call_prsn: parseInt(ci.prsn_ntby || 0),
          call_orgn: parseInt(ci.orgn_ntby || 0),
          put_frgn: parseInt(pi.frgn_ntby || 0),
          put_prsn: parseInt(pi.prsn_ntby || 0),
          put_orgn: parseInt(pi.orgn_ntby || 0),
        };
        this.investorHistory = [row, ...this.investorHistory].slice(0, 60);
      } else if (msg.type === 'futures_price') {
        this.futuresPrice = msg.price;
        this.futuresChange = msg.change;
        this.futuresChangePct = msg.change_pct;
        this.futuresSymbol = msg.symbol || '—';
        this.futuresHigh = msg.high;
        this.futuresLow = msg.low;
        this.futuresOpen = msg.open;
      } else if (msg.type === 'options_status') {
        this.optIsMarketOpen = msg.is_open;
      } else if (msg.type === 'pong') {
        // heartbeat ok
      }
    },

    _scheduleOptReconnect() {
      clearTimeout(this._optReconnectTimer);
      this._optReconnectTimer = setTimeout(() => {
        this._connectOptions();
        this._optReconnectDelay = Math.min(this._optReconnectDelay * 2, 30000);
      }, this._optReconnectDelay);
    },

    async _pollOptionsStatus() {
      try {
        const resp = await fetch('/api/v1/options/status');
        if (resp.ok) {
          const data = await resp.json();
          this.optIsMarketOpen = data.is_open;
        }
      } catch (e) {
        console.warn('Options status poll error:', e);
      }
      setTimeout(() => this._pollOptionsStatus(), 60000);
    },

    setProduct(key) {
      this.activeProduct = key;
      this.rawCalls = [];
      this.rawPuts = [];
      this.callInvestor = {};
      this.putInvestor = {};
      this.investorHistory = [];
      this.expiryDisplay = '—';
      this._connectOptions();
      this._loadInvestorHistory();
    },

    async _loadInvestorHistory() {
      try {
        const resp = await fetch(`/api/v1/options/investor-history?product=${this.activeProduct}&limit=60`);
        if (resp.ok) {
          const data = await resp.json();
          this.investorHistory = data.rows || [];
        }
      } catch (e) {
        console.warn('Investor history load error:', e);
      }
    },

    async _loadFuturesHistory() {
      try {
        const resp = await fetch('/api/v1/options/futures-history?limit=120');
        if (resp.ok) {
          const data = await resp.json();
          this.futuresHistory = data.rows || [];
        }
      } catch (e) {
        console.warn('Futures history load error:', e);
      }
      setTimeout(() => this._loadFuturesHistory(), 60000);
    },

    // ── Formatters ────────────────────────────────────────────────────────
    fmtVol(v) {
      const n = parseInt(v || '0');
      if (n === 0) return '—';
      return n.toLocaleString('ko-KR');
    },
    fmtIv(v) {
      const n = parseFloat(v || '0');
      if (n === 0) return '—';
      return n.toFixed(1);
    },
    fmtChg(v, sign) {
      const n = parseFloat(v || '0');
      if (n === 0) return '0';
      const prefix = sign === '2' ? '+' : sign === '5' ? '-' : '';
      return `${prefix}${Math.abs(n).toFixed(2)}`;
    },
    fmtFlow(v) {
      const n = parseInt(v || '0');
      const prefix = n > 0 ? '+' : '';
      return `${prefix}${n.toLocaleString('ko-KR')}`;
    },
    changeClass(sign) {
      if (sign === '2') return 'change--up';
      if (sign === '5') return 'change--down';
      return 'change--neutral';
    },
    flowClass(v) {
      const n = parseInt(v || '0');
      if (n > 0) return 'flow-positive';
      if (n < 0) return 'flow-negative';
      return '';
    },
  };
}

document.addEventListener('alpine:init', () => {
  Alpine.data('dashboardApp', dashboardApp);
});
