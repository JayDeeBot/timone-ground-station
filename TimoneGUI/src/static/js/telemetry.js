// telemetry.js — resilient live telemetry with DOM-safe Chart.js updates

// ----- Notification state (robust) -----
const _notifyState = {
  stateCanon: null,   // last NON-ZERO canonical state (number or string)
  radio1: null,       // 'good'|'fair'|'poor'|null
  radio2: null,       // 'good'|'fair'|'poor'|null
  main: null,         // 0|1|null
  drog: null,         // 0|1|null
};

// Canonicalize values for comparison
function _canon(val) {
  if (val === null || val === undefined) return null;
  const n = Number(val);
  return Number.isFinite(n) ? n : String(val).trim().toLowerCase();
}

// Radio health → 'good'|'fair'|'poor'|null
function _normRadio(val) {
  if (val == null) return null;
  const s = String(val).trim().toLowerCase();

  // common strings
  if (s.includes('poor') || s.includes('bad') || s.includes('weak')) return 'poor';
  if (s.includes('fair') || s.includes('mid')  || s.includes('ok'))   return 'fair';
  if (s.includes('good') || s.includes('strong'))                     return 'good';

  // numeric heuristics:
  const n = Number(val);
  if (Number.isFinite(n)) {
    // Heuristic 1: percentage 0..100
    if (n >= 0 && n <= 100) {
      if (n <= 25) return 'poor';
      if (n <= 60) return 'fair';
      return 'good';
    }
    // Heuristic 2: RSSI dBm (negative numbers)
    if (n < 0) {
      if (n <= -110) return 'poor';
      if (n <= -95)  return 'fair';
      return 'good';
    }
    // Heuristic 3: small ordinal 0..2
    if (n <= 1) return 'poor';
    if (n <= 2) return 'fair';
    return 'good';
  }
  return null;
}

// Try to extract 433/915 (or radio1/radio2) health from t
function _getRadioPair(t) {
  const g = (k) => t[k];

  let r433 = g('rh433') ?? g('radio433') ?? g('radio_433') ?? g('health433') ?? g('link433') ?? g('lora433') ?? g('rf433')
           ?? g('r433_status') ?? g('radio1') ?? g('radio_1') ?? g('health1') ?? g('link1');
  let r915 = g('rh915') ?? g('radio915') ?? g('radio_915') ?? g('health915') ?? g('link915') ?? g('lora915') ?? g('rf915')
           ?? g('r915_status') ?? g('radio2') ?? g('radio_2') ?? g('health2') ?? g('link2');

  // Fallback: scan keys if unknown naming; prefer keys that look like radio health
  if (r433 == null || r915 == null) {
    for (const [k,v] of Object.entries(t)) {
      const ks = k.toLowerCase();
      if (r433 == null && /(433|radio1|rf1|lowfreq).*?(health|state|status)?/.test(ks)) r433 = v;
      if (r915 == null && /(915|radio2|rf2|highfreq).*?(health|state|status)?/.test(ks)) r915 = v;
    }
  }

  return [_normRadio(r433), _normRadio(r915)];
}

// Normalize continuity to 0|1|null
function _norm01(v) {
  if (v == null) return null;
  if (v === true) return 1;
  if (v === false) return 0;
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return n > 0 ? 1 : 0;
}

(function () {
  let es = null;
  const MAX_POINTS = 600;
  let pendingSelection = null;
  let current = {};

  // Add graph interaction state
  let draggedCol = null;
  const STORAGE_KEY = 'savedGraphs';
  
  // Chart colors palette
  const palette = {
    velocity: '#1f77b4',     // blue
    acceleration: '#ff7f0e', // orange
    altitude: '#2ca02c',     // green
    temperature: '#d62728',  // red
    pressure: '#9467bd',     // purple
    voltage: '#8c564b',      // brown
    current: '#e377c2'      // pink
  };

  // Define ResizeObserver early
  const resizeObserver = new ResizeObserver((entries) => {
    requestAnimationFrame(() => {
      for (const entry of entries) {
        const canvas = entry.target;
        if (!canvas || !canvas.isConnected) continue;
        
        const chart = Chart.getChart(canvas);
        if (!chart || !chartUsable(chart)) {
          try { resizeObserver.unobserve(canvas); } catch (_) {}
          continue;
        }

        try {
          chart.resize();
        } catch (e) {
          console.warn('[telemetry] resize failed:', e);
          try { resizeObserver.unobserve(canvas); } catch (_) {}
          nukeChart(chart);
        }
      }
    });
  });

  const els = {};
  const charts = {
    chartVAA: null,
    chartAV: null,
    mainCont: null,
    drogCont: null,
    dynamic: new Map(), // Map<canvas, {chart, accessors, lastSig}>
  };

  function id(x) { return document.getElementById(x); }
  const hasChart = () => typeof window.Chart !== 'undefined';

  // ---------- Safe guards ----------
  function chartUsable(ch) {
    return !!(ch && ch.canvas && ch.canvas.isConnected && ch.canvas.ownerDocument);
  }
  function canvasReady(canvas) {
    if (!canvas || !canvas.isConnected) return false;
    const rects = canvas.getClientRects?.();
    return !!(rects && rects.length > 0 && (canvas.offsetWidth || canvas.offsetHeight));
  }
  function nukeChart(ch) {
    if (ch?.canvas) {
      try {
        resizeObserver.unobserve(ch.canvas);
      } catch (_) {}
    }
    try { 
      ch?.destroy?.(); 
    } catch (_) {}
    return null;
  }
  function ensureUsableOrNull(refName) {
    const ch = charts[refName];
    if (!chartUsable(ch) || !canvasReady(ch.canvas)) charts[refName] = nukeChart(ch);
  }
  function safeUpdate(ch, mode) {
    if (!chartUsable(ch) || !canvasReady(ch.canvas)) return false;
    try { ch.update(mode || 'none'); return true; } catch (_) { return false; }
  }

  // ---------- Elements ----------
  function refreshEls() {
    // Telemetry fields
    els.alt  = id('tele-alt'); els.vel  = id('tele-vel');
    els.ax   = id('tele-ax');  els.ay   = id('tele-ay'); els.az = id('tele-az');
    els.temp = id('tele-temp'); els.pres = id('tele-pres');

    // Status fields
    els.state = id('status-state'); els.main = id('status-main'); els.drog = id('status-drog');

    // Battery
    els.vbat = id('battery-volts'); els.cbat = id('battery-curr');

    // Canvases
    els.canvasVAA   = id('chartVAA');
    els.canvasAV    = id('chartAV');
    els.canvasMainC = id('mainContinuityChart');
    els.canvasDrogC = id('drogueContinuityChart');

    // Dynamic graph area & controls
    els.graphContainer  = id('graphContainer');
    els.addGraphBtn     = id('addGraphBtn');
    els.telemetrySelect = id('telemetrySelect');

    // GPS fields - remove gpsAlt
    els.lat = id('tele-lat');
    els.lng = id('tele-lng');

    // Add voltage and current canvases
    els.canvasVoltage = id('chartVoltage');
    els.canvasCurrent = id('chartCurrent');
  }

  // ---------- Fields ----------
  function safeNum(v) {
    if (v === null || v === undefined || v === '') return '—';
    const n = Number(v); return Number.isFinite(n) ? n.toString() : String(v);
  }
  function updateFields(t) {
    if (els.alt)   els.alt.textContent   = safeNum(t.alt);
    if (els.vel)   els.vel.textContent   = safeNum(t.vel);
    if (els.ax)    els.ax.textContent    = safeNum(t.ax);
    if (els.ay)    els.ay.textContent    = safeNum(t.ay);
    if (els.az)    els.az.textContent    = safeNum(t.az);
    if (els.temp)  els.temp.textContent  = safeNum(t.temp);
    if (els.pres)  els.pres.textContent  = safeNum(t.pres);
    if (els.state) els.state.textContent = (t.state ?? '').toString();

    // (A) Notify: raw state value CHANGED (ignore any packet with state == 0)
    {
      const raw = t.state;
      if (raw != null) {
        const currCanon = _canon(raw);

        // Completely ignore zero-state packets (no baseline change, no alert)
        const isZero = (currCanon === 0 || currCanon === '0');
        if (!isZero) {
          if (_notifyState.stateCanon !== null && currCanon !== _notifyState.stateCanon) {
            window.notify?.('State changed', `${_notifyState.stateCanon} → ${raw}`, { level: 'warn' });
          }
          _notifyState.stateCanon = currCanon; // update baseline only for non-zero
        }
        // if isZero: do nothing (no baseline update)
      }
    }

    // Forward rocket GPS to the Maps tab
    if (typeof window.updateRocketPosition === 'function') {
      const lat = parseFloat(t.lat);
      const lon = parseFloat(t.lng ?? t.lon);
      const alt = t.alt != null ? parseFloat(t.alt) : undefined;
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        window.updateRocketPosition(lat, lon, alt);
      }
    }

    // Charge continuity raw values:
    // Prefer numeric mc/dc if present; otherwise map main/drog booleans to 1/0; else show em dash.
    if (els.main) {
      els.main.textContent =
        (t.mc != null) ? String(t.mc) :
        (t.main != null ? (Number(t.main) ? '1' : '0') : '—');
    }
    if (els.drog) {
      els.drog.textContent =
        (t.dc != null) ? String(t.dc) :
        (t.drog != null ? (Number(t.drog) ? '1' : '0') : '—');
    }

    if (els.vbat) els.vbat.textContent = safeNum(t.volts);
    if (els.cbat) els.cbat.textContent = safeNum(t.curr);

    if (els.lat)  els.lat.textContent  = safeNum(t.lat);
    if (els.lng)  els.lng.textContent  = safeNum(t.lng);

  // (B) Notify: both radios transition to 'poor'
  {
    const [r1, r2] = _getRadioPair(t);
    const wasBothPoor = (_notifyState.radio1 === 'poor' && _notifyState.radio2 === 'poor');
    const nowBothPoor = (r1 === 'poor' && r2 === 'poor');

    // Fire on entering both-poor, even if previous was unknown/null
    if (nowBothPoor && !wasBothPoor) {
      window.notify?.('Radio link degraded', 'Both radios reported POOR health', { level: 'bad' });
    }

    // update rememberers if present this packet
    if (r1 !== null) _notifyState.radio1 = r1;
    if (r2 !== null) _notifyState.radio2 = r2;
  }

  // (C) Notify: drogue/main continuity rising edge (0 -> 1 only)
  {
    const mainNow = _norm01(t.mc ?? t.main ?? t.main_charge ?? t.mainContinuity ?? t.main_cont);
    const drogNow = _norm01(t.dc ?? t.drog ?? t.drogue_charge ?? t.drogueContinuity ?? t.drog_cont);

    if (mainNow === 1 && _notifyState.main !== 1) {
      if (_notifyState.main !== null) {
        window.notify?.('Main charge continuity', 'Main changed from 0 → 1', { level: 'warn' });
      }
    }
    if (drogNow === 1 && _notifyState.drog !== 1) {
      if (_notifyState.drog !== null) {
        window.notify?.('Drogue charge continuity', 'Drogue changed from 0 → 1', { level: 'warn' });
      }
    }

    if (mainNow !== null) _notifyState.main = mainNow;
    if (drogNow !== null) _notifyState.drog = drogNow;
  }
  }

  // ---------- Chart helpers ----------
  // Update the baseLineOpts function to include all styling
  function baseLineOpts() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { 
        legend: { 
          display: true, 
          position: 'top',
          labels: { usePointStyle: false }
        }
      },
      elements: { 
        point: { radius: 0 },
        line: { tension: 0.2, borderWidth: 2 }
      },
      scales: {
        x: { 
          title: { display: true, text: 'Time (s)' },
          display: true,
          grid: { display: true }
        },
        y: { 
          beginAtZero: true,
          grid: { display: true }
        }
      }
    };
  }
  function lineOpts01(){ const o=baseLineOpts(); o.scales.y.min=0; o.scales.y.max=1; o.scales.y.ticks={stepSize:1}; return o; }

  // Update the chart initialization logic
  function ensureChart(canvas, configBuilder) {
    if (!hasChart() || !canvasReady(canvas)) return null;
    
    // Check for existing chart
    let inst = Chart.getChart(canvas);
    if (inst) {
      // Preserve existing datasets and labels
      const existingConfig = {
        data: {
          labels: inst.data.labels.slice(),
          datasets: inst.data.datasets.map(ds => ({
            ...ds,
            data: ds.data.slice()
          }))
        }
      };
      return inst;
    }

    // Create new chart
    try {
      const config = configBuilder();
      inst = new Chart(canvas.getContext('2d'), config);
      if (inst && canvas.isConnected) {
        try {
          resizeObserver.observe(canvas);
        } catch (_) {}
      }
      return inst;
    } catch (e) {
      console.warn('[telemetry] chart create failed:', e);
      return null;
    }
  }

  // Replace the buffer array with a ChartState class
  class ChartState {
    constructor(maxPoints = 600) {
      this.maxPoints = maxPoints;
      this.labels = [];
      this.values = new Map();
    
      this.dataTypes = {
        velocity: {label: 'Velocity (m/s)', fn: t => +t.vel || 0},
        acceleration: {label: 'Acceleration (m/s²)', fn: t => this.accelMag(t)},
        altitude: {label: 'Altitude (m)', fn: t => +t.alt || 0},
        temperature: {
          label: 'Temperature (°C)',
          fn: t => Number(t.temp) || 0
        },
        pressure: {
          label: 'Pressure (kPa)',
          fn: t => Number(t.pres) || 0
        },
        voltage: {label: 'Voltage (V)', fn: t => +t.volts || 0},
        current: {label: 'Current (A)', fn: t => +t.curr || 0},
       // LoRa charge continuity values (mc = main charge, dc = drogue charge)
       mc: { label: 'Main Charge', fn: t => Number(t.mc) || 0 },
       dc: { label: 'Drogue Charge', fn: t => Number(t.dc) || 0 },
        latitude: {label: 'Latitude', fn: t => +t.lat || 0},
        longitude: {label: 'Longitude', fn: t => +t.lng || 0}
      };
    }

    accelMag(t) {
      const ax = +t.ax || 0, ay = +t.ay || 0, az = +t.az || 0;
      return Math.sqrt(ax*ax + ay*ay + az*az);
    }

    addPoint(time, telemetry) {
      // Add timestamp
      this.labels.push(String(time));
    
      // Calculate and store all values
      for (const [type, {fn}] of Object.entries(this.dataTypes)) {
        if (!this.values.has(type)) {
          this.values.set(type, []);
        }
        this.values.get(type).push(fn(telemetry));
      }

      // Trim old data
      if (this.labels.length > this.maxPoints) {
        this.labels.shift();
        for (const arr of this.values.values()) {
          arr.shift();
        }
      }
    }

    getDataForType(type) {
      if (!this.values.has(type)) {
        this.values.set(type, []);
      }
      return this.values.get(type);
    }
    
    getLabelForType(type) {
      return this.dataTypes[type]?.label || type;
    }
  }
  
  // Replace existing buffer with chartState
  const chartState = new ChartState(MAX_POINTS);

  // Update chart initialization
  function initChart(canvas, dataTypes) {
    if (!hasChart() || !canvas) return null;
    try {
      // If a Chart instance already exists for this canvas, reuse it
      const existing = Chart.getChart(canvas);
      if (existing) {
        // If existing chart is usable and attached to the same canvas, return it
        try {
          if (existing.canvas === canvas && chartUsable(existing)) return existing;
        } catch (_) {}
        // Otherwise destroy the stale chart so we can recreate cleanly
        try { nukeChart(existing); } catch (_) {}
      }

      const datasets = dataTypes.map(type => ({
        label: chartState.getLabelForType(type),
        data: chartState.getDataForType(type),
        borderColor: palette[type],
        tension: 0.2,
        borderWidth: 2
      }));

      const chart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
          labels: chartState.labels,
          datasets
        },
        options: baseLineOpts()
      });

      if (canvas.isConnected) {
        try {
          resizeObserver.observe(canvas);
        } catch (_) {}
      }
      return chart;
    } catch (e) {
      console.warn('[telemetry] chart create failed:', e);
      return null;
    }
  }

  // ---------- Telemetry ingest ----------
  function normalizeRow(raw) {
  const has = (v) => v !== undefined && v !== null;

  // ---------- Object payload ----------
  if (typeof raw === 'object' && raw !== null) {
    // Prefer numbers; fall back to undefined (not 0) so we don't overwrite with NaN.
    const mcNum = has(raw.mc)   ? Number(raw.mc)   : (has(raw.main) ? Number(raw.main) : undefined);
    const dcNum = has(raw.dc)   ? Number(raw.dc)   : (has(raw.drog) ? Number(raw.drog) : undefined);

    return {
      time:  has(raw.time) ? raw.time : Date.now(),
      temp:  has(raw.temp) ? Number(raw.temp) : undefined,
      pres:  has(raw.pres) ? Number(raw.pres) : undefined,
      alt:   has(raw.alt)  ? Number(raw.alt)  : undefined,
      vel:   has(raw.vel)  ? Number(raw.vel)  : undefined,
      ax:    has(raw.ax)   ? Number(raw.ax)   : undefined,
      ay:    has(raw.ay)   ? Number(raw.ay)   : undefined,
      az:    has(raw.az)   ? Number(raw.az)   : undefined,
      volts: has(raw.volts)? Number(raw.volts): undefined,
      curr:  has(raw.curr) ? Number(raw.curr) : undefined,
      lat:   has(raw.lat)  ? Number(raw.lat)  : undefined,
      lng:   has(raw.lng)  ? Number(raw.lng)  : undefined,
      state: has(raw.state)? String(raw.state) : '',

      // Numeric continuity (0/1)
      mc: mcNum,
      dc: dcNum,

      // Legacy boolean view for any existing consumers
      main: has(raw.main) ? Boolean(Number(raw.main)) : (has(mcNum) ? Boolean(mcNum) : undefined),
      drog: has(raw.drog) ? Boolean(Number(raw.drog)) : (has(dcNum) ? Boolean(dcNum) : undefined),
    };
  }

  // ---------- String log line ----------
  if (typeof raw === 'string') {
    const data = { time: Date.now() };

    // BARO: "[BARO] P=1013.2 hPa T=21.5°C"  (pres in kPa)
    const baroMatch = raw.match(/\[BARO\]\s*P=(\d+\.?\d*)\s*hPa\s*T=(\d+\.?\d*)°C/i);
    if (baroMatch) {
      data.pres = parseFloat(baroMatch[1]) / 10.0; // hPa → kPa
      data.temp = parseFloat(baroMatch[2]);
    }

    // GPS: "[GPS] ... LAT:..., LNG:..., ALT:..."
    const gpsMatch = raw.match(/\[GPS\].*?LAT:(-?\d+\.?\d*),?\s*LNG:(-?\d+\.?\d*),?\s*ALT:(-?\d+\.?\d*)/i);
    if (gpsMatch) {
      data.lat = parseFloat(gpsMatch[1]);
      data.lng = parseFloat(gpsMatch[2]);
      data.alt = parseFloat(gpsMatch[3]);
    }

    // APRS (DDMM.mm/DDDMM.mm with N/S/E/W)
    const aprsMatch = raw.match(/\[APRS\].*?(\d{2})(\d{2}\.\d{2})([NS])[\/\\](\d{3})(\d{2}\.\d{2})([EW])/i);
    if (aprsMatch) {
      const [_, latDeg, latMin, latDir, lonDeg, lonMin, lonDir] = aprsMatch;
      let lat = parseInt(latDeg) + parseFloat(latMin) / 60;
      let lng = parseInt(lonDeg) + parseFloat(lonMin) / 60;
      if (latDir === 'S') lat = -lat;
      if (lonDir === 'W') lng = -lng;
      data.lat = lat;
      data.lng = lng;
    }

    // ALT/VEL: "ALT:123.4 VEL:-5.6"
    const altVelMatch = raw.match(/ALT:(-?\d+\.?\d*)\s*VEL:(-?\d+\.?\d*)/i);
    if (altVelMatch) {
      data.alt = parseFloat(altVelMatch[1]);
      data.vel = parseFloat(altVelMatch[2]);
    }

    // LoRa continuity: match "mc:1" / "mc=1" and "dc:0" / "dc=0" anywhere
    const mcMatch = raw.match(/\bmc[:=]\s*([01])\b/i);
    if (mcMatch) data.mc = Number(mcMatch[1]);

    const dcMatch = raw.match(/\bdc[:=]\s*([01])\b/i);
    if (dcMatch) data.dc = Number(dcMatch[1]);

    // Legacy booleans derived from numeric
    if (data.mc != null) data.main = Boolean(data.mc);
    if (data.dc != null) data.drog = Boolean(data.dc);

    return data;
  }

  // Fallback
  return { time: Date.now() };
}

function onTelemetry(raw) {
  try {
    const newData = normalizeRow(raw);

    // Merge that accepts 0 as a valid update; only ignore undefined/null/NaN.
    const mergeVal = (oldVal, newVal) =>
      (newVal === undefined || newVal === null || Number.isNaN(newVal)) ? oldVal : newVal;

    Object.keys(newData).forEach((key) => {
      current[key] = mergeVal(current[key], newData[key]);
    });

    // Ensure fixed charts exist (create them lazily on first telemetry if needed)
    attachOrInitFixedCharts();
    attachOrInitContinuityCharts();

    // Update UI and charts
    updateFields(current);
    chartState.addPoint(newData.time || Date.now(), current);

    if (charts.chartVAA && charts.chartVAA.ctx) updateFixedCharts();
    if (charts.mainCont && charts.mainCont.ctx) updateContinuityCharts(current);
    updateDynamicCharts();
  } catch (e) {
    console.error('Telemetry parse error:', e);
  }
}

// Update chart handling to prevent recreation
function updateFixedCharts() {
  if (!hasChart()) return;

  // Update VAA Chart (Velocity, Acceleration, Altitude)
  if (charts.chartVAA && charts.chartVAA.data) {
    charts.chartVAA.data.labels = chartState.labels;
    charts.chartVAA.data.datasets[0].data = chartState.getDataForType('velocity');
    charts.chartVAA.data.datasets[1].data = chartState.getDataForType('acceleration');
    charts.chartVAA.data.datasets[2].data = chartState.getDataForType('altitude');
    charts.chartVAA.update('none');
  }

  // Update AV Chart (Acceleration & Velocity)
  if (charts.chartAV && charts.chartAV.data) {
    charts.chartAV.data.labels = chartState.labels;
    charts.chartAV.data.datasets[0].data = chartState.getDataForType('acceleration');
    charts.chartAV.data.datasets[1].data = chartState.getDataForType('velocity');
    charts.chartAV.update('none');
  }

  // Update Voltage Chart
  if (charts.chartVoltage && charts.chartVoltage.data) {
    charts.chartVoltage.data.labels = chartState.labels;
    charts.chartVoltage.data.datasets[0].data = chartState.getDataForType('voltage');
    charts.chartVoltage.update('none');
  }

  // Update Current Chart
  if (charts.chartCurrent && charts.chartCurrent.data) {
    charts.chartCurrent.data.labels = chartState.labels;
    charts.chartCurrent.data.datasets[0].data = chartState.getDataForType('current');
    charts.chartCurrent.update('none');
  }
}

  function updateDynamicCharts() {
    for (const [canvas, meta] of charts.dynamic.entries()) {
      const { chart, accessors } = meta;
      if (!chartUsable(chart) || !canvasReady(chart.canvas)) {
        charts.dynamic.delete(canvas);
        nukeChart(chart);
        continue;
      }

      // prefer DOM-declared metric
      const card = canvas.closest('.graph-card');
      const metric = card?.dataset?.metric || meta.lastSig || 'velocity';

      const fn = chartState.dataTypes[metric]?.fn;
      if (!fn) continue;

      chart.data.labels = chartState.labels;
      chart.data.datasets[0].label = chartState.getLabelForType(metric);
      chart.data.datasets[0].data = chartState.getDataForType(metric);
      chart.update('none');
    }
  }

  // ---------- Tab / visibility / resize ----------
  function resizeAllCharts() {
    // Use requestAnimationFrame to prevent layout thrashing
    requestAnimationFrame(() => {
      [charts.chartVAA, charts.chartAV, charts.mainCont, charts.drogCont].forEach(ch => {
        if (chartUsable(ch) && canvasReady(ch.canvas)) safeUpdate(ch, 'none');
      });
      for (const { chart } of charts.dynamic.values()) {
        if (chartUsable(chart) && canvasReady(chart.canvas)) safeUpdate(chart, 'none');
      }
    });
  }

  document.addEventListener('shown.bs.tab', (e) => {
    if (e.target.id === 'status-tab') {
      refreshEls();
      // Take control of charts immediately
      requestAnimationFrame(() => {
        // Force recreate fixed charts
        charts.chartVAA = null;
        charts.chartAV = null;
        attachOrInitFixedCharts();
        attachOrInitContinuityCharts();
        bindAllDynamicCanvases();
        resizeAllCharts();
      });
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      refreshEls();
      attachOrInitFixedCharts();
      attachOrInitContinuityCharts();
      bindAllDynamicCanvases();
      resizeAllCharts();
    }
  });
  window.addEventListener('resize', resizeAllCharts);

  // ---------- Dynamic graph hooks ----------
  function setupAddGraphHook(){
    if (!els.addGraphBtn || !els.telemetrySelect) return;
    els.addGraphBtn.addEventListener('click', () => {
      pendingSelection = Array.from(els.telemetrySelect.selectedOptions).map(o => o.value);
    });
  }

  // Update the bindAllDynamicCanvases function to remove backfilling
  function bindAllDynamicCanvases(){
    if (!els.graphContainer) return;
    els.graphContainer.querySelectorAll('canvas').forEach(canvas => {
      if (!canvas.isConnected) return;
      if (canvas === els.canvasVAA || canvas === els.canvasAV || canvas === els.canvasVoltage || canvas === els.canvasCurrent) return;

      const inst = hasChart() ? Chart.getChart(canvas) : null;

      // derive metric from DOM card first
      const card = canvas.closest('.graph-card');
      const domMetric = card?.dataset?.metric;
      const fallbackMetric = (pendingSelection && pendingSelection[0]) || null;

      if (inst && !charts.dynamic.has(canvas)){
        // choose metric: DOM -> pendingSelection -> try label-match -> velocity
        let metric = domMetric || fallbackMetric;
        if (!metric) {
          const lbl = (inst.data?.datasets?.[0]?.label || '').toLowerCase();
          metric = Object.keys(chartState.dataTypes).find(t => lbl.includes(t)) || 'velocity';
        }

        const fn = chartState.dataTypes[metric]?.fn || (t=>+t.vel||0);
        charts.dynamic.set(canvas, { chart: inst, accessors: [fn], lastSig: metric });

        // initialize dataset and label to preserve metric
        inst.data.labels = chartState.labels;
        inst.data.datasets[0].label = chartState.getLabelForType(metric);
        inst.data.datasets[0].data = chartState.getDataForType(metric);
        inst.update('none');

        pendingSelection = null;

      } else if (!inst && !charts.dynamic.has(canvas)){
        // create a new chart on the canvas using dom metric or selection fallback
        const sel = domMetric ? [domMetric] : (pendingSelection ? pendingSelection : null);
        initDynamicChartForCanvas(canvas, sel);
        pendingSelection = null;

      } else if (inst && charts.dynamic.has(canvas)){
        const meta = charts.dynamic.get(canvas);
        // if the card defines a metric, ensure meta matches it
        const desired = domMetric || meta.lastSig;
        if (desired !== meta.lastSig){
          const fn = chartState.dataTypes[desired]?.fn || (t=>+t.vel||0);
          meta.accessors = [fn];
          meta.lastSig = desired;
          inst.data.labels = chartState.labels;
          inst.data.datasets[0].label = chartState.getLabelForType(desired);
          inst.data.datasets[0].data = chartState.getDataForType(desired);
          inst.update('none');
        }
      }
    });
  }

  // ---------- Chart data accessors ----------
  function accessorsForChart(chart, fallbackSelection) {
    // Get data type from chart label
    const dataset = chart?.data?.datasets?.[0];
    if (!dataset) return { fns: [], sig: '' };

    const label = dataset.label?.toLowerCase() || '';
    
    // Match label to known data type
    const type = Object.keys(chartState.dataTypes).find(t => 
      label.includes(t) || label.includes(chartState.getLabelForType(t).toLowerCase())
    );

    // Use matched type or fallback
    const dataType = type || fallbackSelection?.[0] || 'velocity';
    const fn = t => chartState.dataTypes[dataType]?.fn(t) || 0;

    return {
      fns: [fn],
      sig: dataset.label || ''
    };
  }

  function initDynamicChartForCanvas(canvas, selectionForFallback) {
    if (!hasChart() || !canvasReady(canvas)) return;

    const type = selectionForFallback?.[0] || 'velocity';
    const label = chartState.getLabelForType(type);
    
    try {
      const chart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
          labels: chartState.labels,
          datasets: [{
            label: label,
            data: chartState.getDataForType(type),
            borderColor: getColorForType(type)
          }]
        },
        options: baseLineOpts()
      });

      const { fns, sig } = accessorsForChart(chart, selectionForFallback);
      charts.dynamic.set(canvas, { chart, accessors: fns, lastSig: sig });
      
      if (canvas.isConnected) {
        try {
          resizeObserver.observe(canvas);
        } catch (_) {}
      }
    } catch (e) {
      console.warn('[telemetry] dynamic chart create failed:', e);
    }
  }

  const mo = new MutationObserver(() => {
    refreshEls();
    attachOrInitFixedCharts();
    attachOrInitContinuityCharts();
    bindAllDynamicCanvases();
  });

  // ---------- Boot ----------
  document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded');
    refreshEls();
    attachOrInitFixedCharts();
    attachOrInitContinuityCharts();
    setupAddGraphHook();
    restoreSavedGraphs(); // Restore saved graphs
    bindAllDynamicCanvases();

    // Initialize drag & drop for existing graphs
    document.querySelectorAll('#graphContainer .col-12.col-md-6').forEach(col => {
      const card = col.querySelector('.graph-card');
      if (card && !card.dataset.fixed) {
        initDrag(col);
        bindRemove(card);
      }
    });

    mo.observe(document.documentElement, { childList:true, subtree:true });

    if (typeof window.EventSource !== 'undefined') {
      es = new EventSource('/api/telemetry/stream');

      es.onopen = () => console.log('[telemetry] connected');

      es.onmessage = (ev) => {
        if (!ev.data) return;
        try {
          onTelemetry(JSON.parse(ev.data));
        } catch (e) {
          console.error('[telemetry] parse error:', e, ev.data);
        }
      };

      es.onerror = () => {
        try { es.close(); } catch (_) {}
        setTimeout(() => {
          es = new EventSource('/api/telemetry/stream');

          es.onopen = () => console.log('[telemetry] reconnected');

          es.onmessage = (ev) => {
            if (!ev.data) return;
            try {
              onTelemetry(JSON.parse(ev.data));
            } catch (e) {
              console.error('[telemetry] parse error (reconnect):', e, ev.data);
            }
          };
        }, 1500);
      };
    }
  });

  // ---------- Cleanup ----------
  function cleanup() {
    charts.dynamic.forEach((meta, canvas) => {
      nukeChart(meta.chart);
    });
    [charts.chartVAA, charts.chartAV, charts.mainCont, charts.drogCont].forEach(nukeChart);
    if (es) es.close();
    mo.disconnect();
    resizeObserver.disconnect();
  }

  // Use pagehide instead of unload
  window.addEventListener('pagehide', cleanup, { capture: true });
  
  // Also handle visibility change for tab switching/closing
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      // Pause updates when hidden
      if (es) es.close();
    } else {
      // Reconnect and refresh when visible again
      refreshEls();
      attachOrInitFixedCharts();
      attachOrInitContinuityCharts();
      bindAllDynamicCanvases();
      resizeAllCharts();
    }
  });

  // ---------- Fixed chart initialization ----------
  function attachOrInitFixedCharts() {
    // Initialize each fixed chart independently if its canvas exists and chart is missing
    try {
      if (els.canvasVAA && !charts.chartVAA) {
        charts.chartVAA = initChart(els.canvasVAA, ['velocity', 'acceleration', 'altitude']);
        if (charts.chartVAA && charts.chartVAA.canvas) {
          charts.chartVAA.options = baseLineOpts();
          charts.chartVAA.canvas.style.height = '130px';
          safeUpdate(charts.chartVAA, 'none');
        }
      }

      if (els.canvasAV && !charts.chartAV) {
        charts.chartAV = initChart(els.canvasAV, ['acceleration', 'velocity']);
        if (charts.chartAV && charts.chartAV.canvas) {
          charts.chartAV.options = baseLineOpts();
          charts.chartAV.canvas.style.height = '130px';
          safeUpdate(charts.chartAV, 'none');
        }
      }

      if (els.canvasVoltage && !charts.chartVoltage) {
        charts.chartVoltage = initChart(els.canvasVoltage, ['voltage']);
        if (charts.chartVoltage && charts.chartVoltage.canvas) {
          charts.chartVoltage.options = baseLineOpts();
          charts.chartVoltage.canvas.style.height = '130px';
          safeUpdate(charts.chartVoltage, 'none');
        }
      }

      if (els.canvasCurrent && !charts.chartCurrent) {
        charts.chartCurrent = initChart(els.canvasCurrent, ['current']);
        if (charts.chartCurrent && charts.chartCurrent.canvas) {
          charts.chartCurrent.options = baseLineOpts();
          charts.chartCurrent.canvas.style.height = '130px';
          safeUpdate(charts.chartCurrent, 'none');
        }
      }
    } catch (e) {
      console.warn('[telemetry] attachOrInitFixedCharts error:', e);
    }
  }

  // ---------- Continuity chart initialization ----------
  function attachOrInitContinuityCharts() {
    // Make sure the canvases exist
    if (!els.canvasMainC || !els.canvasDrogC) return;

    // Helper to build a 0/1 square-wave chart
    const buildContinuityChart = (canvas, typeKey, label) => {
      // Destroy any stale chart on this canvas
      const existing = Chart.getChart(canvas);
      if (existing) { nukeChart(existing); }

      try {
        const chart = new Chart(canvas.getContext('2d'), {
          type: 'line',
          data: {
            labels: chartState.labels,
            datasets: [{
              label,
              data: chartState.getDataForType(typeKey),
              borderColor: getColorForType(typeKey),
              borderWidth: 2,
              tension: 0,
              stepped: true,        // ← square wave
              pointRadius: 0
            }]
          },
          options: (function(){
            const o = baseLineOpts();
            o.scales.y.min = 0;
            o.scales.y.max = 1;
            o.scales.y.ticks = { stepSize: 1 };
            return o;
          })()
        });

        if (canvas.isConnected) {
          try { resizeObserver.observe(canvas); } catch (_) {}
        }
        return chart;
      } catch (e) {
        console.warn('[telemetry] continuity chart create failed:', e);
        return null;
      }
    };

    // Create/refresh the two continuity charts (mc, dc) only
    if (!charts.mainCont) {
      charts.mainCont = buildContinuityChart(els.canvasMainC, 'mc', chartState.getLabelForType('mc'));
    }
    if (!charts.drogCont) {
      charts.drogCont = buildContinuityChart(els.canvasDrogC, 'dc', chartState.getLabelForType('dc'));
    }
  }


  // helper to map type -> color (used by dynamic init)
  function getColorForType(type) {
    return palette[type] || '#777777';
  }

  // ---------- Saved graphs ----------
  // Persist dynamic graphs as an ordered list of metric keys (e.g. "voltage","temperature")
  function saveGraphs() {
    if (!els.graphContainer) return;
    const metrics = Array.from(els.graphContainer.querySelectorAll('.graph-card'))
      .filter(card => card.dataset.metric && card.dataset.fixed !== '1')
      .map(card => card.dataset.metric);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(metrics));
    } catch (e) {
      console.warn('[telemetry] saveGraphs failed:', e);
    }
  }

  function restoreSavedGraphs() {
    if (!els.graphContainer) return;
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      // re-create cards in saved order, avoid duplicating already-present dynamic cards
      for (const metric of saved) {
        const already = Array.from(els.graphContainer.querySelectorAll('.graph-card'))
          .some(c => c.dataset.metric === metric);
        if (!already) addGraph(metric);
      }
    } catch (e) {
      console.warn('[telemetry] restoreSavedGraphs failed:', e);
    }
  }

  // ---------- Drag & Drop ----------
  function initDrag(col) {
    if (!col) return;

    let startX, startY, startLeft, startTop;

    const onMouseDown = (e) => {
      startX = e.clientX;
      startY = e.clientY;
      startLeft = parseInt(getComputedStyle(col).left, 10);
      startTop = parseInt(getComputedStyle(col).top, 10);

      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    };

    const onMouseMove = (e) => {
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      col.style.left = `${startLeft + dx}px`;
      col.style.top = `${startTop + dy}px`;
    };

    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);

      // Save position
      saveGraphs();
    };

    col.addEventListener('mousedown', onMouseDown);
  }

  function bindRemove(card) {
    if (!card || card.dataset.removeInit === '1') return;
    card.dataset.removeInit = '1';

    const btn = card.querySelector('.remove-graph');
    if (!btn) return;

    const isFixed = card.dataset.fixed === '1';
    if (isFixed) { btn.remove(); return; }

    btn.addEventListener('click', () => {
      const col = card.closest('.col-12.col-md-6');
      if (!col) return;
      const canvas = col.querySelector('canvas');
      if (canvas) {
        const ch = Chart.getChart(canvas);
        if (ch) nukeChart(ch);
        charts.dynamic.delete(canvas);
      }
      col.remove();
      saveGraphs();
    });
  }

  // Update setupAddGraphHook to properly handle button click and modal closing
  function setupAddGraphHook() {
    const addBtn = document.getElementById('addGraphBtn');
    const select = document.getElementById('telemetrySelect');
    if (!addBtn || !select) return;

    addBtn.addEventListener('click', () => {
      const selected = Array.from(select.selectedOptions).map(o => o.value);
      selected.forEach(metric => addGraph(metric));
      
      // Close modal
      const modal = bootstrap.Modal.getInstance(document.getElementById('addGraphModal'));
      if (modal) modal.hide();
    });
  }

  // Add graph function to create new dynamic graphs
  function addGraph(metric) {
    if (!els.graphContainer) return;

    const type = metric;
    const label = chartState.getLabelForType(type);
    
    const col = document.createElement('div');
    col.className = 'col-12 col-md-6';
    
    col.innerHTML = `
      <div class="card graph-card" data-metric="${type}">
        <div class="card-header py-2 d-flex justify-content-between align-items-center">
          <span class="fw-semibold">${label}</span>
          <button class="btn btn-sm btn-outline-danger remove-graph">
            <i class="bi bi-x-lg"></i>
          </button>
        </div>
        <div class="card-body">
          <canvas height="130"></canvas>
        </div>
      </div>
    `;

    els.graphContainer.appendChild(col);

    const canvas = col.querySelector('canvas');
    const chart = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels: chartState.labels,
        datasets: [{
          label: label,
          data: chartState.getDataForType(type),
          borderColor: palette[type],
          tension: 0.2,
          borderWidth: 2
        }]
      },
      options: baseLineOpts()
    });

    charts.dynamic.set(canvas, {
      chart,
      accessors: [chartState.dataTypes[type].fn],
      lastSig: label
    });

    initDrag(col);
    bindRemove(col.querySelector('.graph-card'));
    
    saveGraphs();
  }

  function updateContinuityCharts() {
    try {
      if (charts.mainCont && charts.mainCont.data) {
        const ch = charts.mainCont;
        ch.data.labels = chartState.labels;
        if (!ch.data.datasets.length) ch.data.datasets.push({ label: chartState.getLabelForType('mc'), data: [] });
        ch.data.datasets[0].label = chartState.getLabelForType('mc');
        ch.data.datasets[0].data = chartState.getDataForType('mc');
        ch.data.datasets[0].stepped = true;
        safeUpdate(ch, 'none');
      }

      if (charts.drogCont && charts.drogCont.data) {
        const ch = charts.drogCont;
        ch.data.labels = chartState.labels;
        if (!ch.data.datasets.length) ch.data.datasets.push({ label: chartState.getLabelForType('dc'), data: [] });
        ch.data.datasets[0].label = chartState.getLabelForType('dc');
        ch.data.datasets[0].data = chartState.getDataForType('dc');
        ch.data.datasets[0].stepped = true;
        safeUpdate(ch, 'none');
      }
    } catch (e) {
      console.warn('[telemetry] updateContinuityCharts failed:', e);
    }
  }
})();
