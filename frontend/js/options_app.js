/**
 * Alpine.js application — KOSPI options board dashboard
 */

function optionsApp() {
  return {
    // Connection
    connectionState: 'disconnected',
    _ws: null,
    _reconnectDelay: 1000,
    _reconnectTimer: null,

    // Market
    isMarketOpen: false,

    // Product selection
    activeProduct: 'WKI',
    productLabels: {
      'WKI': '위클리(목)',
      'WKM': '위클리(월)',
      'KOSPI200': 'KOSPI200',
      'MKI': '미니',
      'KQI': 'KOSDAQ150',
    },

    // Board data
    rawCalls: [],
    rawPuts: [],
    expiryDisplay: '—',
    expiryCode: '—',
    lastUpdated: null,

    // KOSPI200 futures price
    futuresPrice: null,
    futuresChange: null,
    futuresChangePct: null,
    futuresSymbol: '—',
    futuresHigh: null,
    futuresLow: null,
    futuresOpen: null,

    // Investor data
    callInvestor: {},
    putInvestor: {},
    investorHistory: [],

    // Futures price history
    futuresHistory: [],

    // Computed
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
      // Build strike-keyed map from calls and puts
      const callMap = {};
      for (const c of this.rawCalls) callMap[c.acpr] = c;
      const putMap = {};
      for (const p of this.rawPuts) putMap[p.acpr] = p;

      // Union of all strikes
      const strikes = new Set([...Object.keys(callMap), ...Object.keys(putMap)]);
      const rows = [];
      for (const strike of strikes) {
        const call = callMap[strike] || {};
        const put = putMap[strike] || {};
        const callVol = parseInt(call.acml_vol || '0');
        const putVol = parseInt(put.acml_vol || '0');
        const isAtm = call.atm_cls_name === 'ATM' || put.atm_cls_name === 'ATM';
        // Show rows with volume or near ATM
        if (callVol > 0 || putVol > 0 || isAtm) {
          rows.push({ strike, call, put });
        }
      }
      // Sort descending by strike price
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

    init() {
      this._connect();
      this._pollStatus();
      this._loadInvestorHistory();
      this._loadFuturesHistory();
    },

    _wsUrl() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${proto}//${location.host}/ws/options?product=${this.activeProduct}`;
    },

    _connect() {
      if (this._ws) { this._ws.onclose = null; this._ws.close(); }
      this.connectionState = 'reconnecting';
      const ws = new WebSocket(this._wsUrl());
      this._ws = ws;

      ws.onopen = () => {
        this.connectionState = 'connected';
        this._reconnectDelay = 1000;
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          this._handleMessage(msg);
        } catch (e) {
          console.warn('Options WS parse error:', e);
        }
      };

      ws.onclose = () => {
        this.connectionState = 'reconnecting';
        this._scheduleReconnect();
      };

      ws.onerror = () => ws.close();
    },

    _handleMessage(msg) {
      if (msg.type === 'options_board') {
        // Only update if message is for the currently selected product
        if (msg.product && msg.product !== this.activeProduct) return;
        this.rawCalls = msg.calls || [];
        this.rawPuts = msg.puts || [];
        this.expiryCode = msg.expiry || '—';
        // Format expiry_date if provided, else show raw code
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
        // Prepend live snapshot to history (keep 60 rows)
        const ci = this.callInvestor, pi = this.putInvestor;
        const now = new Date();
        const pad = n => String(n).padStart(2, '0');
        const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
        const row = {
          ts: Date.now(),
          time: timeStr,
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
        this.isMarketOpen = msg.is_open;
      } else if (msg.type === 'pong') {
        // heartbeat ok
      }
    },

    _scheduleReconnect() {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = setTimeout(() => {
        this._connect();
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, 30000);
      }, this._reconnectDelay);
    },

    async _pollStatus() {
      try {
        const resp = await fetch('/api/v1/options/status');
        if (resp.ok) {
          const data = await resp.json();
          this.isMarketOpen = data.is_open;
        }
      } catch (e) {
        console.warn('Options status poll error:', e);
      }
      setTimeout(() => this._pollStatus(), 60000);
    },

    setProduct(key) {
      this.activeProduct = key;
      this.rawCalls = [];
      this.rawPuts = [];
      this.callInvestor = {};
      this.putInvestor = {};
      this.investorHistory = [];
      this.expiryDisplay = '—';
      this._connect();
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

    // Formatters
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
  Alpine.data('optionsApp', optionsApp);
});
