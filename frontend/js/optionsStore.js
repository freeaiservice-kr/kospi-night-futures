/**
 * Alpine.store('options') — options WebSocket state + computed
 */

document.addEventListener('alpine:init', () => {
  Alpine.store('options', {
    // Connection
    optConnectionState: 'disconnected',
    _pollTimer: null,
    _pollDelayMs: 3000,

    // Market
    optIsMarketOpen: false,

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

    // KOSPI200 underlying
    futuresPrice: null,
    futuresChange: null,
    futuresChangePct: null,
    futuresSymbol: '—',
    futuresHigh: null,
    futuresLow: null,
    futuresOpen: null,

    // Investor flow
    callInvestor: {},
    putInvestor: {},
    investorHistory: [],
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
    get atmIV() {
      const atmRow = this.board.find(r => r.call.atm_cls_name === 'ATM' || r.put.atm_cls_name === 'ATM');
      if (!atmRow) return null;
      const callIV = parseFloat(atmRow.call.hts_ints_vltl || 0);
      const putIV = parseFloat(atmRow.put.hts_ints_vltl || 0);
      if (callIV === 0 && putIV === 0) return null;
      const count = (callIV > 0 ? 1 : 0) + (putIV > 0 ? 1 : 0);
      return ((callIV + putIV) / count).toFixed(1);
    },
    get ivSkew() {
      const atmRow = this.board.find(r => r.call.atm_cls_name === 'ATM' || r.put.atm_cls_name === 'ATM');
      if (!atmRow) return null;
      const callIV = parseFloat(atmRow.call.hts_ints_vltl || 0);
      const putIV = parseFloat(atmRow.put.hts_ints_vltl || 0);
      if (callIV === 0 || putIV === 0) return null;
      return (putIV - callIV).toFixed(1);
    },
    get oiPcRatio() {
      const callOI = this.rawCalls.reduce((s, c) => s + parseInt(c.hts_otst_stpl_qty || 0), 0);
      const putOI = this.rawPuts.reduce((s, p) => s + parseInt(p.hts_otst_stpl_qty || 0), 0);
      if (callOI === 0) return null;
      return (putOI / callOI).toFixed(2);
    },

    // Init
    init() {
      this._startPolling();
      this._pollOptionsStatus();
      this._loadInvestorHistory();
      this._loadFuturesHistory();
    },

    _startPolling() {
      clearTimeout(this._pollTimer);
      this.optConnectionState = 'reconnecting';
      this._pollLatest();
    },

    _schedulePoll() {
      clearTimeout(this._pollTimer);
      this._pollTimer = setTimeout(
        () => this._pollLatest(),
        this._pollDelayMs,
      );
    },

    async _pollLatest() {
      try {
        const resp = await fetch(`/api/v1/options/latest?product=${this.activeProduct}`);
        if (!resp.ok) {
          this.optConnectionState = this.optIsMarketOpen ? 'reconnecting' : 'disconnected';
          this._pollDelayMs = 5000;
          return;
        }

        const msg = await resp.json();
        this._handleLatestPayload(msg);
        this.optConnectionState = 'connected';
        this._pollDelayMs = 3000;
      } catch (e) {
        console.warn('Options polling error:', e);
        this.optConnectionState = this.optIsMarketOpen ? 'reconnecting' : 'disconnected';
        this._pollDelayMs = 5000;
      } finally {
        this._schedulePoll();
      }
    },

    _isPayloadForActiveProduct(product) {
      return !product || product === this.activeProduct;
    },

    _handleLatestPayload(msg) {
      const board = msg.board || {};
      const investor = msg.investor || {};
      const futures = msg.futures || {};

      if (this._isPayloadForActiveProduct(board.product)) {
        this.rawCalls = board.calls || [];
        this.rawPuts = board.puts || [];
        this.expiryCode = board.expiry || '—';
        if (board.expiry_date) {
          const d = new Date(board.expiry_date);
          const days = ['일', '월', '화', '수', '목', '금', '토'];
          if (Number.isNaN(d.getTime())) {
            this.expiryDisplay = board.expiry_date;
          } else {
            this.expiryDisplay = `${board.expiry_date.replace(/-/g, '/')}(${days[d.getDay()]})`;
          }
        } else {
          this.expiryDisplay = board.expiry || '—';
        }
        if (board.updated_at) this.lastUpdated = board.updated_at;
      }

      if (this._isPayloadForActiveProduct(investor.product)) {
        this.callInvestor = investor.call_investor || {};
        this.putInvestor = investor.put_investor || {};

        const ci = this.callInvestor;
        const pi = this.putInvestor;
        if (ci && pi) {
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
        }
      }

      if (futures && (futures.price != null || futures.change != null || futures.change_pct != null || futures.symbol)) {
        this.futuresPrice = futures.price;
        this.futuresChange = futures.change;
        this.futuresChangePct = futures.change_pct;
        this.futuresSymbol = futures.symbol || this.futuresSymbol;
        this.futuresHigh = futures.high;
        this.futuresLow = futures.low;
        this.futuresOpen = futures.open;
      }
    },

    _stopPolling() {
      clearTimeout(this._pollTimer);
      this._pollTimer = null;
    },

    _reconnectOnProductChange() {
      this._stopPolling();
      this._startPolling();
      this._loadInvestorHistory();
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
      this._reconnectOnProductChange();
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
  });
});
