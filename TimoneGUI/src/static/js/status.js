// status.js — Radio Status + IBIS FSM + Battery panel (hold + smooth render)
(function () {
  let es = null;

  // --- DOM refs
  const els = {
    // Radio 433
    rssi433: document.getElementById('radio433RSSI'),
    snr433:  document.getElementById('radio433SNR'),
    hl433:   document.getElementById('radio433HealthLabel'),
    // Radio 915
    rssi915: document.getElementById('radio915RSSI'),
    snr915:  document.getElementById('radio915SNR'),
    hl915:   document.getElementById('radio915HealthLabel'),
    // IBIS FSM
    stateRaw: document.getElementById('status-state'),
    // Battery panel (new)
    battPct: document.getElementById('battery-percent'),
    battChg: document.getElementById('battery-charging'),
    // Battery values shown under IBIS FSM
    battV: document.getElementById('battery-volts'),
    battI: document.getElementById('battery-curr'),
  };

  // --- Cached latest values
  const cache = {
    r433: { rssi: null, snr: null },
    r915: { rssi: null, snr: null },
    state: null,                   // numeric FSM state
    batt: { pct: null, charging: null, volts: null, curr_mA: null },
  };

  // --- Helpers
  function classifyHealth(rssi, snr) {
    const scoreRSSI = (v) => (v == null || Number.isNaN(v)) ? 2 : (v >= -85 ? 0 : (v >= -100 ? 1 : 2));
    const scoreSNR  = (v) => (v == null || Number.isNaN(v)) ? 2 : (v >=   8 ? 0 : (v >=    0 ? 1 : 2));
    const s = Math.max(scoreRSSI(rssi), scoreSNR(snr));
    return s === 0 ? 'healthy' : (s === 1 ? 'okay' : 'poor');
  }

  function setHealthBadge(el, level) {
    if (!el) return;
    el.classList.remove('bg-success', 'bg-warning', 'bg-danger');
    if (level === 'healthy') el.classList.add('bg-success');
    else if (level === 'okay') el.classList.add('bg-warning');
    else el.classList.add('bg-danger');
    el.textContent = level === 'healthy' ? 'Healthy' : (level === 'okay' ? 'Okay' : 'Poor');
  }

  const isFiniteNum = (v) => v != null && Number.isFinite(Number(v));

  function fmt(value, unit, digits = 1) {
    if (!isFiniteNum(value)) return null;
    const n = Number(value);
    return `${n.toFixed(digits)}${unit ? ' ' + unit : ''}`;
  }

  // Render loop (smooth)
  function render() {
    // Radios
    if (els.rssi433) { const t = fmt(cache.r433.rssi, 'dBm'); if (t !== null) els.rssi433.textContent = t; }
    if (els.snr433)  { const t = fmt(cache.r433.snr,  'dB');  if (t !== null) els.snr433.textContent  = t; }
    if (els.hl433)   setHealthBadge(els.hl433, classifyHealth(cache.r433.rssi, cache.r433.snr));

    if (els.rssi915) { const t = fmt(cache.r915.rssi, 'dBm'); if (t !== null) els.rssi915.textContent = t; }
    if (els.snr915)  { const t = fmt(cache.r915.snr,  'dB');  if (t !== null) els.snr915.textContent  = t; }
    if (els.hl915)   setHealthBadge(els.hl915, classifyHealth(cache.r915.rssi, cache.r915.snr));

    // FSM
    if (els.stateRaw && isFiniteNum(cache.state)) {
      els.stateRaw.textContent = String(cache.state);
    }

    // Battery (% + Charging panel)
    if (els.battPct) {
      const t = isFiniteNum(cache.batt.pct) ? `${Number(cache.batt.pct).toFixed(0)} %` : null;
      if (t !== null) els.battPct.textContent = t;
    }
    if (els.battChg) {
      if (cache.batt.charging === true) els.battChg.textContent = 'Charging';
      else if (cache.batt.charging === false) els.battChg.textContent = 'Not charging';
      // else leave as-is (—)
    }

    // Battery V / I under FSM
    if (els.battV) {
      const t = fmt(cache.batt.volts, 'V', 2);
      if (t !== null) els.battV.textContent = t.replace(' V',''); // keep external V suffix from HTML
    }
    if (els.battI) {
      const t = isFiniteNum(cache.batt.curr_mA) ? `${Number(cache.batt.curr_mA).toFixed(0)}` : null;
      if (t !== null) els.battI.textContent = t; // 'mA' suffix in HTML
    }
  }

  // Update helpers (hold: only overwrite fields present in row)
  function updateRadio(which, row) {
    const tgt = which === '433' ? cache.r433 : cache.r915;
    if (isFiniteNum(row.rssi)) tgt.rssi = Number(row.rssi);
    if (isFiniteNum(row.snr))  tgt.snr  = Number(row.snr);
  }

  function normBool(v) {
    if (typeof v === 'boolean') return v;
    if (v == null) return null;
    const s = String(v).trim().toLowerCase();
    if (['1','true','t','yes','y','on','charging'].includes(s)) return true;
    if (['0','false','f','no','n','off','not charging','discharging'].includes(s)) return false;
    return null;
  }

  function handleRow(row) {
    // FSM
    if (isFiniteNum(row.state)) cache.state = Number(row.state);

    // Battery: percent
    const pct = row.battery_percent ?? row.battery ?? row.soc;
    if (isFiniteNum(pct)) cache.batt.pct = Math.max(0, Math.min(100, Number(pct)));

    // Battery: charging flag
    const chg = normBool(row.charging ?? row.chg ?? row.is_charging);
    if (chg !== null) cache.batt.charging = chg;

    // Battery: voltage
    const v = row.voltage ?? row.volt ?? row.v;
    if (isFiniteNum(v)) cache.batt.volts = Number(v);

    // Battery: current (prefer mA if row looks like mA; otherwise convert A→mA if value is small)
    let i = row.current ?? row.curr ?? row.i ?? row.amps ?? row.ma;
    if (isFiniteNum(i)) {
      i = Number(i);
      // Heuristic: if magnitude < 10, assume it's Amps and convert to mA
      cache.batt.curr_mA = (Math.abs(i) < 10 && (row.ma == null)) ? i * 1000.0 : i;
    }

    // Radios
    const hasRSSI = isFiniteNum(row.rssi);
    const hasSNR  = isFiniteNum(row.snr);
    if (hasRSSI || hasSNR) {
      const band = (row.band || row.source || row.src || '').toString().toLowerCase();
      if (band.includes('433')) updateRadio('433', row);
      else if (band.includes('915')) updateRadio('915', row);
      else { updateRadio('433', row); updateRadio('915', row); }
    }
  }

  // Telemetry stream
  function ensureStream() {
    if (es) return;
    try {
      es = new EventSource('/api/telemetry/stream');
      es.addEventListener('message', (ev) => {
        try {
          handleRow(JSON.parse(ev.data));
        } catch {}
      });
      es.addEventListener('error', () => {
        try { es.close(); } catch {}
        es = null;
        setTimeout(ensureStream, 1000);
      });
    } catch {
      setTimeout(ensureStream, 1000);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    ensureStream();
    setInterval(render, 250); // smooth 4 Hz updates
  });
})();
