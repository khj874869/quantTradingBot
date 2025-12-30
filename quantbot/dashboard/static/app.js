/* global LightweightCharts */

const $ = (id) => document.getElementById(id);

let selectedBotId = null;
let chart = null;
let candleSeries = null;

let equityChart = null;
let equitySeries = null;
let dailyPnlChart = null;
let dailyPnlSeries = null;

function fmt(n, d = 4) {
  if (n === null || n === undefined) return '-';
  const x = Number(n);
  if (!Number.isFinite(x)) return '-';
  return x.toFixed(d);
}

function fmtInt(n) {
  const x = Number(n);
  if (!Number.isFinite(x)) return '-';
  return String(Math.trunc(x));
}

function fmtTimeISO(iso) {
  if (!iso) return '';
  return String(iso).replace('T',' ').slice(0,19);
}

function fmtTimeMs(ms) {
  const x = Number(ms);
  if (!Number.isFinite(x) || x <= 0) return '';
  const d = new Date(x);
  const pad = (v) => String(v).padStart(2,'0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function parseYMD(s) {
  // LightweightCharts accepts BusinessDay as {year, month, day}
  if (!s || typeof s !== 'string') return null;
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) };
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

function ensureChart() {
  if (chart) return;
  chart = LightweightCharts.createChart($('chart'), {
    width: $('chart').clientWidth,
    height: $('chart').clientHeight,
    layout: { background: { color: 'transparent' }, textColor: '#cfd6e4' },
    grid: { vertLines: { color: '#1f2630' }, horzLines: { color: '#1f2630' } },
    timeScale: { timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: '#1f2630' },
    crosshair: { mode: 0 },
  });
  candleSeries = chart.addCandlestickSeries();
  window.addEventListener('resize', () => {
    chart.applyOptions({ width: $('chart').clientWidth, height: $('chart').clientHeight });
  });
}

function ensurePerfCharts() {
  if (!equityChart) {
    equityChart = LightweightCharts.createChart($('equityChart'), {
      width: $('equityChart').clientWidth,
      height: $('equityChart').clientHeight,
      layout: { background: { color: 'transparent' }, textColor: '#cfd6e4' },
      grid: { vertLines: { color: '#1f2630' }, horzLines: { color: '#1f2630' } },
      timeScale: { timeVisible: false },
      rightPriceScale: { borderColor: '#1f2630' },
      crosshair: { mode: 0 },
    });
    equitySeries = equityChart.addLineSeries({});
  }

  if (!dailyPnlChart) {
    dailyPnlChart = LightweightCharts.createChart($('dailyPnlChart'), {
      width: $('dailyPnlChart').clientWidth,
      height: $('dailyPnlChart').clientHeight,
      layout: { background: { color: 'transparent' }, textColor: '#cfd6e4' },
      grid: { vertLines: { color: '#1f2630' }, horzLines: { color: '#1f2630' } },
      timeScale: { timeVisible: false },
      rightPriceScale: { borderColor: '#1f2630' },
      crosshair: { mode: 0 },
    });
    dailyPnlSeries = dailyPnlChart.addHistogramSeries({
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      base: 0,
    });
  }

  if (!ensurePerfCharts._boundResize) {
    ensurePerfCharts._boundResize = true;
    window.addEventListener('resize', () => {
      if (equityChart) equityChart.applyOptions({ width: $('equityChart').clientWidth, height: $('equityChart').clientHeight });
      if (dailyPnlChart) dailyPnlChart.applyOptions({ width: $('dailyPnlChart').clientWidth, height: $('dailyPnlChart').clientHeight });
    });
  }
}

function toCandles(arr) {
  if (!Array.isArray(arr)) return [];
  // lightweight-charts expects UNIX seconds
  return arr
    .filter((x) => x && x.t)
    .map((x) => ({
      time: Number(x.t),
      open: Number(x.o),
      high: Number(x.h),
      low: Number(x.l),
      close: Number(x.c),
    }))
    .filter((x) => Number.isFinite(x.time) && Number.isFinite(x.open));
}

function renderBots(bots) {
  const wrap = $('bots');
  wrap.innerHTML = '';

  bots.forEach((b) => {
    const div = document.createElement('div');
    div.className = 'bot' + (b.id === selectedBotId ? ' active' : '');
    div.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
        <div>
          <div><b>${b.id}</b></div>
          <div class="muted" style="font-size:12px">${b.venue} • ${b.symbol} • ${b.mode || 'paper'} • ${b.strategy || '-'}</div>
        </div>
        <div class="muted" style="font-size:12px">${fmtTimeISO(b.ts)}</div>
      </div>
    `;
    div.onclick = () => { selectedBotId = b.id; };
    wrap.appendChild(div);
  });
}

function botIdFromSnapshot(bots) {
  if (selectedBotId && bots.some((b) => b.id === selectedBotId)) return selectedBotId;
  return bots.length ? bots[0].id : null;
}

function updateKV(id, value, digits = 4) {
  $(id).textContent = (typeof value === 'number' || typeof value === 'string') ? fmt(value, digits) : (value ?? '-');
}

function table(headers, rowsHtml) {
  return `
    <table>
      <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
      <tbody>${rowsHtml || ''}</tbody>
    </table>
  `;
}

function renderOrderbook(ob) {
  if (!ob || (!Array.isArray(ob.bids) && !Array.isArray(ob.asks))) {
    $('ob').innerHTML = `<div class="muted small">No orderbook data yet.</div>`;
    return;
  }
  const bids = Array.isArray(ob.bids) ? ob.bids.slice(0, 10) : [];
  const asks = Array.isArray(ob.asks) ? ob.asks.slice(0, 10) : [];

  const depth = Math.max(bids.length, asks.length, 10);
  const maxQty = Math.max(
    1e-12,
    ...bids.map((x) => Number(x?.[1] || 0)),
    ...asks.map((x) => Number(x?.[1] || 0))
  );

  let rows = '';
  for (let i = 0; i < depth; i++) {
    const b = bids[i] || [];
    const a = asks[i] || [];
    const bPx = b[0] ? Number(b[0]) : null;
    const bQty = b[1] ? Number(b[1]) : null;
    const aPx = a[0] ? Number(a[0]) : null;
    const aQty = a[1] ? Number(a[1]) : null;

    const bW = bQty ? Math.max(0, Math.min(100, (bQty / maxQty) * 100)) : 0;
    const aW = aQty ? Math.max(0, Math.min(100, (aQty / maxQty) * 100)) : 0;

    rows += `
      <tr>
        <td class="buy mono">${bPx ? fmt(bPx, 4) : ''}</td>
        <td class="buy mono depthcell">
          <div class="depthbar buy" style="width:${bW.toFixed(1)}%"></div>
          <div class="depthtext">${bQty ? fmt(bQty, 6) : ''}</div>
        </td>
        <td class="sell mono">${aPx ? fmt(aPx, 4) : ''}</td>
        <td class="sell mono depthcell">
          <div class="depthbar sell" style="width:${aW.toFixed(1)}%"></div>
          <div class="depthtext">${aQty ? fmt(aQty, 6) : ''}</div>
        </td>
      </tr>
    `;
  }
  $('ob').innerHTML = table(['Bid Px', 'Bid Qty', 'Ask Px', 'Ask Qty'], rows);
}

function renderTrades(trades) {
  if (!Array.isArray(trades) || trades.length === 0) {
    $('trades').innerHTML = `<div class="muted small">No trade tape yet.</div>`;
    return;
  }
  let rows = '';
  for (const t of trades.slice(0, 40)) {
    const side = (t.side || '').toUpperCase();
    rows += `
      <tr>
        <td class="mono">${fmtTimeMs(t.ts_ms)}</td>
        <td class="${side === 'BUY' ? 'buy' : 'sell'}"><b>${side}</b></td>
        <td class="mono">${fmt(t.price, 4)}</td>
        <td class="mono">${fmt(t.qty, 6)}</td>
        <td class="mono">${fmt(t.notional, 2)}</td>
      </tr>
    `;
  }
  $('trades').innerHTML = table(['Time','Side','Price','Qty','Notional'], rows);
}

function renderEvents(events) {
  if (!Array.isArray(events) || events.length === 0) {
    $('events').innerHTML = `<div class="muted small">No events yet.</div>`;
    return;
  }
  let rows = '';
  // newest last in tape; show newest first
  const arr = [...events].reverse().slice(0, 60);
  for (const e of arr) {
    const side = (e.side || '').toUpperCase();
    rows += `
      <tr>
        <td class="mono">${fmtTimeISO(e.ts).slice(11,19)}</td>
        <td><b>${e.type || ''}</b></td>
        <td class="${side === 'BUY' ? 'buy' : (side === 'SELL' ? 'sell' : '')}">${side}</td>
        <td class="mono">${e.qty ? fmt(e.qty, 6) : ''}</td>
        <td class="mono">${e.price ? fmt(e.price, 4) : ''}</td>
        <td class="mono">${e.reason ? String(e.reason) : ''}</td>
        <td class="mono">${e.fee ? fmt(e.fee, 4) : ''}</td>
      </tr>
    `;
  }
  $('events').innerHTML = table(['Time','Type','Side','Qty','Price','Reason','Fee'], rows);
}

function renderFlow(flow) {
  if (!flow || typeof flow !== 'object') {
    $('flow').innerHTML = `<div class="muted small">No flow snapshot yet.</div>`;
    return;
  }
  const kvs = [
    ['Trades (win)', fmtInt(flow.trade_count)],
    ['Notional (win)', fmt(flow.total_notional, 2)],
    ['Rate /s', fmt(flow.notional_rate, 2)],
    ['Accel', fmt(flow.notional_accel, 2)],
    ['Rate Z', fmt(flow.rate_z, 2)],
    ['Accel Z', fmt(flow.accel_z, 2)],
    ['Large Share', fmt(flow.large_trade_share, 3)],
    ['Large Cnt', fmtInt(flow.large_trade_count)],
  ];
  $('flow').innerHTML = `
    <div class="kvs">
      ${kvs.map(([k,v]) => `<div class="kv"><div class="k">${k}</div><div class="v mono">${v}</div></div>`).join('')}
    </div>
  `;
}

function renderLiq(liq) {
  if (!liq || typeof liq !== 'object') {
    $('liq').innerHTML = `<div class="muted small">No liquidation stream (spot or disabled).</div>`;
    return;
  }
  const kvs = [
    ['Buy liq notional', fmt(liq.buy_liq_notional, 2)],
    ['Sell liq notional', fmt(liq.sell_liq_notional, 2)],
    ['Top buy px', liq.top_buy_price ? fmt(liq.top_buy_price, 4) : '-'],
    ['Top sell px', liq.top_sell_price ? fmt(liq.top_sell_price, 4) : '-'],
  ];
  $('liq').innerHTML = `
    <div class="kvs">
      ${kvs.map(([k,v]) => `<div class="kv"><div class="k">${k}</div><div class="v mono">${v}</div></div>`).join('')}
    </div>
  `;
}

function renderOrders(payload) {
  const events = payload && Array.isArray(payload.events) ? payload.events : [];
  if (!events.length) {
    $('orders').innerHTML = `<div class="muted small">No fills yet.</div>`;
    return;
  }
  let rows = '';
  for (const e of events.slice(0, 80)) {
    const side = String(e.side || '').toUpperCase();
    const reason = e.reason || '';
    const rn = Number(e.realized_net_delta || 0);
    rows += `
      <tr>
        <td class="mono">${fmtTimeMs(e.ts_ms) || fmtTimeISO(e.ts).slice(11, 19)}</td>
        <td class="mono">${e.venue || ''}</td>
        <td class="mono">${e.symbol || ''}</td>
        <td class="${side === 'BUY' ? 'buy' : 'sell'}"><b>${side}</b></td>
        <td class="mono">${e.qty ? fmt(e.qty, 6) : ''}</td>
        <td class="mono">${e.price ? fmt(e.price, 4) : ''}</td>
        <td class="mono">${e.fee ? fmt(e.fee, 4) : ''}</td>
        <td class="mono">${Number.isFinite(rn) ? fmt(rn, 4) : ''}</td>
        <td class="mono">${reason}</td>
      </tr>
    `;
  }
  $('orders').innerHTML = table(['Time','Venue','Symbol','Side','Qty','Price','Fee','Realized Δ(net)','Reason'], rows);
}

function renderPositions(b, globalRisk) {
  const acct = (b && b.account_tag) ? String(b.account_tag) : '';
  const pos = b && b.position ? b.position : {};

  const accounts = globalRisk && Array.isArray(globalRisk.accounts) ? globalRisk.accounts : [];
  const total = globalRisk && globalRisk.total ? globalRisk.total : null;

  let acctRow = null;
  if (acct) acctRow = accounts.find((r) => String(r.account_tag) === acct) || null;

  const acctEq = acctRow ? Number(acctRow.equity || 0) : null;
  const acctAn = acctRow ? Number(acctRow.abs_notional || 0) : null;
  const totalEq = total ? Number(total.equity || 0) : null;
  const totalAn = total ? Number(total.abs_notional || 0) : null;

  const acctFrac = (acctEq && acctAn !== null && acctEq > 0) ? (acctAn / acctEq) : null;
  const totalFrac = (totalEq && totalAn !== null && totalEq > 0) ? (totalAn / totalEq) : null;

  const blocks = [];
  blocks.push(`
    <div class="kvs">
      <div class="kv"><div class="k">Account Tag</div><div class="v mono">${acct || '-'}</div></div>
      <div class="kv"><div class="k">Position Notional</div><div class="v mono">${pos.notional ? fmt(pos.notional, 2) : '0.00'}</div></div>
      <div class="kv"><div class="k">PnL Total</div><div class="v mono">${pos.pnl_total ? fmt(pos.pnl_total, 4) : '0.0000'}</div></div>
      <div class="kv"><div class="k">PnL %</div><div class="v mono">${Number.isFinite(Number(pos.pnl_pct)) ? (Number(pos.pnl_pct) * 100).toFixed(2) + '%' : '-'}</div></div>
    </div>
  `);

  blocks.push('<div style="height:12px"></div>');

  // Global risk summary
  const riskKvs = [
    ['Account Equity', acctEq !== null ? fmt(acctEq, 2) : '-'],
    ['Account Exposure', acctAn !== null ? fmt(acctAn, 2) : '-'],
    ['Account Exposure %', acctFrac !== null && Number.isFinite(acctFrac) ? (acctFrac * 100).toFixed(2) + '%' : '-'],
    ['Total Equity', totalEq !== null ? fmt(totalEq, 2) : '-'],
    ['Total Exposure', totalAn !== null ? fmt(totalAn, 2) : '-'],
    ['Total Exposure %', totalFrac !== null && Number.isFinite(totalFrac) ? (totalFrac * 100).toFixed(2) + '%' : '-'],
  ];
  blocks.push(`
    <div class="kvs">
      ${riskKvs.map(([k,v]) => `<div class="kv"><div class="k">${k}</div><div class="v mono">${v}</div></div>`).join('')}
    </div>
  `);

  if (accounts.length) {
    blocks.push('<div style="height:10px"></div>');
    const rows = accounts
      .map((r) => {
        const eq = Number(r.equity || 0);
        const an = Number(r.abs_notional || 0);
        const frac = (eq > 0) ? (an / eq) * 100.0 : 0;
        return `<tr><td class="mono">${r.account_tag}</td><td class="mono">${fmt(eq,2)}</td><td class="mono">${fmt(an,2)}</td><td class="mono">${frac.toFixed(2)}%</td></tr>`;
      })
      .join('');
    blocks.push(table(['Account','Equity','Abs Exposure','Exposure %'], rows));
  }

  $('positions').innerHTML = blocks.join('');
}

function renderSignal(sig) {
  if (!sig || typeof sig !== 'object' || !sig.side) {
    $('signal').innerHTML = `<div class="muted small">No signal yet.</div>`;
    return;
  }
  const side = (sig.side || '').toUpperCase();
  const score = sig.score;
  const meta = sig.meta || {};
  const comps = meta.score_components || {};
  const basic = `
    <div class="kvs">
      <div class="kv"><div class="k">Time</div><div class="v mono">${fmtTimeISO(sig.ts)}</div></div>
      <div class="kv"><div class="k">Side</div><div class="v ${side==='BUY'?'buy':(side==='SELL'?'sell':'')} mono"><b>${side}</b></div></div>
      <div class="kv"><div class="k">Score</div><div class="v mono">${fmt(score, 3)}</div></div>
      <div class="kv"><div class="k">Reason</div><div class="v mono">${meta.reason ? String(meta.reason) : '-'}</div></div>
    </div>
  `;
  const compRows = Object.keys(comps).length ? table(
    ['Component','Value'],
    Object.entries(comps).map(([k,v]) => `<tr><td class="mono">${k}</td><td class="mono">${fmt(v,3)}</td></tr>`).join('')
  ) : `<div class="muted small" style="margin-top:10px">No score components.</div>`;
  $('signal').innerHTML = basic + `<div style="height:10px"></div>` + compRows;
}

function setupTabs() {
  const btns = Array.from(document.querySelectorAll('.tabbtn'));
  btns.forEach((b) => {
    b.addEventListener('click', () => {
      const group = b.parentElement;
      if (!group) return;
      // deactivate buttons in this group
      Array.from(group.querySelectorAll('.tabbtn')).forEach(x => x.classList.remove('active'));
      b.classList.add('active');

      // activate corresponding tab content (scope: closest panel)
      const panel = b.closest('.panel');
      const target = b.getAttribute('data-tab');
      if (!panel || !target) return;
      Array.from(panel.querySelectorAll('.tab')).forEach(t => t.classList.remove('active'));
      const el = $(target);
      if (el) el.classList.add('active');
    });
  });
}

async function refreshPerfCharts(accountTag) {
  ensurePerfCharts();
  const acctQ = accountTag ? `&account_tag=${encodeURIComponent(accountTag)}` : '';

  // Daily realized PnL (30d)
  try {
    const pnl = await fetchJSON(`/api/pnl_series?days=30${acctQ}`);
    const daily = (pnl.daily || [])
      .map((r) => ({ time: parseYMD(String(r.date)), value: Number(r.realized_net || 0) }))
      .filter((p) => p.time && Number.isFinite(p.value));
    if (dailyPnlSeries) dailyPnlSeries.setData(daily);
  } catch (_) {
    if (dailyPnlSeries) dailyPnlSeries.setData([]);
  }

  // Equity curve
  let retPct = null;
  try {
    const eq = await fetchJSON(`/api/equity_series?days=30${acctQ}`);
    let series = (eq.series || []).map((p) => ({ time: Math.floor(Number(p.ts_ms) / 1000), value: Number(p.equity) }));
    series = series.filter((p) => Number.isFinite(p.time) && Number.isFinite(p.value));
    if (series.length > 2000) {
      const step = Math.ceil(series.length / 2000);
      series = series.filter((_, i) => i % step === 0 || i === series.length - 1);
    }
    if (equitySeries) equitySeries.setData(series);
    const daily = (eq.daily || []);
    if (daily.length >= 2) {
      const first = Number(daily[0].equity);
      const last = Number(daily[daily.length - 1].equity);
      if (Number.isFinite(first) && Number.isFinite(last) && first > 0) {
        retPct = ((last - first) / first) * 100.0;
      }
    }
  } catch (_) {
    if (equitySeries) equitySeries.setData([]);
  }

  if (retPct === null || !Number.isFinite(retPct)) {
    $('m_ret30').textContent = '-';
  } else {
    $('m_ret30').textContent = `${retPct.toFixed(2)}%`;
  }
}

async function refresh() {
  const snap = await fetchJSON('/api/snapshot');
  const bots = snap.bots || [];
  renderBots(bots);

  const newId = botIdFromSnapshot(bots);
  if (!newId) {
    $('raw').textContent = 'No bot state files found yet. Start a bot (paper/live) and keep it running.';
    return;
  }
  selectedBotId = newId;

  const b = await fetchJSON(`/api/bot?id=${encodeURIComponent(selectedBotId)}`);
  $('title').textContent = `${b.venue} • ${b.symbol} • ${b.mode || 'paper'} • ${b.strategy || '-'}`;

  const accountTag = (b.account_tag || '').trim();

  const pos = b.position || {};
  updateKV('p_qty', Number(pos.qty || 0), 6);
  updateKV('p_avg', Number(pos.avg_cost || 0), 4);
  updateKV('p_unr', Number(pos.unrealized_pnl || 0), 4);
  updateKV('p_real', Number(pos.realized_pnl_net || 0), 4);
  updateKV('p_eq', Number(b.equity || 0), 2);
  const pnlPct = Number(pos.pnl_pct || 0) * 100.0;
  $('p_pnlpct').textContent = Number.isFinite(pnlPct) ? `${pnlPct.toFixed(2)}%` : '-';

  updateKV('m_last', Number(b.last_price || 0), 4);
  $('m_ba').textContent = `${fmt(Number(b.best_bid || 0), 4)} / ${fmt(Number(b.best_ask || 0), 4)}`;
  $('m_mode').textContent = b.mode || '-';
  $('m_strat').textContent = b.strategy || '-';

  try {
    const pnl = await fetchJSON(`/api/pnl?days=30${accountTag ? `&account_tag=${encodeURIComponent(accountTag)}` : ''}`);
    $('m_realized').textContent = fmt(Number(pnl.realized_net || 0), 2);
    $('m_fees').textContent = fmt(Number(pnl.fees || 0), 2);
  } catch (_) {
    $('m_realized').textContent = '-';
    $('m_fees').textContent = '-';
  }

  // Update performance charts & 30d return
  await refreshPerfCharts(accountTag);

  ensureChart();
  const c = toCandles(b.candles_1m || []);
  if (candleSeries && c.length) candleSeries.setData(c);

  renderOrderbook(b.orderbook_l2);

  // Orders / fills
  try {
    const fills = await fetchJSON(`/api/fills?limit=80&days=7${accountTag ? `&account_tag=${encodeURIComponent(accountTag)}` : ''}&symbol=${encodeURIComponent(b.symbol || '')}&venue=${encodeURIComponent(b.venue || '')}`);
    renderOrders(fills);
  } catch (_) {
    renderOrders({ events: [] });
  }

  // Positions / global exposure
  try {
    const gr = await fetchJSON(`/api/global_risk?max_age_sec=120${accountTag ? `&account_tag=${encodeURIComponent(accountTag)}` : ''}`);
    renderPositions(b, gr);
  } catch (_) {
    renderPositions(b, { accounts: [], total: null });
  }

  renderTrades(b.trades || []);
  renderEvents(b.events || []);
  renderFlow(b.flow || {});
  renderLiq(b.liq || {});
  renderSignal(b.last_signal || {});

  $('raw').textContent = JSON.stringify(b, null, 2);
}

async function tick() {
  try {
    await refresh();
  } catch (e) {
    $('raw').textContent = String(e && e.stack ? e.stack : e);
  }
}

setupTabs();
setInterval(tick, 1000);
tick();
