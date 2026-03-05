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

    // Investor data
    callInvestor: {},
    putInvestor: {},

    // Computed
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

    init() {
      this._connect();
      this._pollStatus();
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
      } else if (msg.type === 'investor_flow') {
        this.callInvestor = msg.call_investor || {};
        this.putInvestor = msg.put_investor || {};
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
      this.expiryDisplay = '—';
      // Reconnect so backend sends fresh data for new product
      // (For now we just reconnect; backend uses its _active_product)
      // Future: send product selection over WS
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
