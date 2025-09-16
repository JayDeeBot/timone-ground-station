// telemetry.js — resilient live telemetry with DOM-safe Chart.js updates

(function () {
  let es = null;

  const MAX_POINTS = 600;
  const buffer = [];             // recent telemetry window for backfill
  let pendingSelection = null;   // last "Add Graph" selection

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
    return !!(ch && ch.canvas && ch.canvas.isConnected);
  }
  // Is the canvas in the DOM *and* visible (has layout boxes & size)?
  function canvasReady(canvas) {
    if (!canvas || !canvas.isConnected) return false;
    const rects = canvas.getClientRects?.();
    return !!(rects && rects.length > 0 && (canvas.offsetWidth || canvas.offsetHeight));
  }
  // Destroy and null a chart instance safely
  function nukeChart(ch) {
    try { ch?.destroy?.(); } catch (_) {}
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
  }

  // ---------- Fields ----------
  function safeNum(v) {
    if (v === null || v === undefined || v === '') return '—';
    const n = Number(v); return Number.isFinite(n) ? n.toString() : String(v);
  }
  function updateFields(t) {
    if (els.alt)  els.alt.textContent  = safeNum(t.alt);
    if (els.vel)  els.vel.textContent  = safeNum(t.vel);
    if (els.ax)   els.ax.textContent   = safeNum(t.ax);
    if (els.ay)   els.ay.textContent   = safeNum(t.ay);
    if (els.az)   els.az.textContent   = safeNum(t.az);
    if (els.temp) els.temp.textContent = safeNum(t.temp);
    if (els.pres) els.pres.textContent = safeNum(t.pres);
    if (els.state) els.state.textContent = (t.state ?? '').toString();
    if (els.main)  els.main.textContent  = (t.main  ?? '').toString();
    if (els.drog)  els.drog.textContent  = (t.drog  ?? '').toString();
    if (els.vbat) els.vbat.textContent = safeNum(t.volts);
    if (els.cbat) els.cbat.textContent = safeNum(t.curr);
  }

  // ---------- Chart helpers ----------
  function accelMag(t){ const ax=+t.ax||0, ay=+t.ay||0, az=+t.az||0; return Math.sqrt(ax*ax+ay*ay+az*az); }
  function baseLineOpts(){
    return { animation:false, responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{ display:true } }, elements:{ point:{ radius:0 } },
      scales:{ x:{ display:false }, y:{ beginAtZero:true } } };
  }
  function lineOpts01(){ const o=baseLineOpts(); o.scales.y.min=0; o.scales.y.max=1; o.scales.y.ticks={stepSize:1}; return o; }

  function ensureChart(canvas, configBuilder){
    if (!hasChart() || !canvasReady(canvas)) return null; // only create when visible & attached
    let inst = Chart.getChart(canvas);
    if (inst) return inst;
    try {
      return new Chart(canvas.getContext('2d'), configBuilder());
    } catch (e) {
      console.warn('[telemetry] chart create failed (will retry when visible):', e);
      return null;
    }
  }

  function pushPoint(ch, label, values){
    if (!chartUsable(ch) || !canvasReady(ch.canvas)) return;
    try {
      const data = ch.data;
      data.labels ??= [];
      data.datasets ??= [];
      data.labels.push(label);
      for (let i=0;i<values.length;i++){
        if (!data.datasets[i]) data.datasets[i] = { label:`Series ${i+1}`, data:[] };
        data.datasets[i].data.push(values[i]);
      }
      while (data.labels.length > MAX_POINTS){
        data.labels.shift();
        for (const ds of data.datasets) ds.data.shift();
      }
      ch.update('none');
    } catch (e) {
      console.warn('[telemetry] chart update failed; destroying to recover:', e);
      // Clear fixed references if this was one of them
      for (const key of ['chartVAA', 'chartAV', 'mainCont', 'drogCont']) {
        if (charts[key] === ch) { charts[key] = nukeChart(ch); return; }
      }
      // Or clear dynamic entry
      for (const [canvas, meta] of charts.dynamic.entries()) {
        if (meta.chart === ch) { charts.dynamic.delete(canvas); nukeChart(ch); return; }
      }
    }
  }

  function accessorsForChart(chart, fallbackSelection){
    const keywords = [
      { keys:['vel','velocity'], fn:t=>+t.vel||0 },
      { keys:['acc','accel','acceleration'], fn:t=>accelMag(t) },
      { keys:['alt','altitude'], fn:t=>+t.alt||0 },
      { keys:['temp','temperature'], fn:t=>+t.temp||0 },
      { keys:['pres','pressure'], fn:t=>+t.pres||0 },
      { keys:['main'], fn:t=>(+t.main?1:0) },
      { keys:['drog','drogue'], fn:t=>(+t.drog?1:0) },
    ];
    const selToFn = sel => ({
      velocity:t=>+t.vel||0, acceleration:t=>accelMag(t),
      altitude:t=>+t.alt||0, temperature:t=>+t.temp||0, pressure:t=>+t.pres||0
    }[sel] || (t=>+t.vel||0));

    const ds = (chart?.data?.datasets)||[];
    const fns = ds.length ? ds.map(d=>{
      const lbl=(d.label||'').toLowerCase();
      const m = keywords.find(k=>k.keys.some(k2=>lbl.includes(k2)));
      return (m?.fn) || (fallbackSelection && fallbackSelection[0] ? selToFn(fallbackSelection[0]) : (t=>+t.vel||0));
    }) : [fallbackSelection && fallbackSelection[0] ? selToFn(fallbackSelection[0]) : (t=>+t.vel||0)];
    const sig = ds.map(d=>d.label||'').join('|');
    return { fns, sig };
  }

  // ---------- Fixed charts ----------
  function attachOrInitFixedCharts(){
    // VAA
    if (els.canvasVAA){
      if (!charts.chartVAA || !chartUsable(charts.chartVAA) || !canvasReady(charts.chartVAA?.canvas)){
        const created = ensureChart(els.canvasVAA, ()=>({
          type:'line',
          data:{ labels:[], datasets:[
            { label:'Velocity (m/s)', data:[] },
            { label:'|Acceleration| (m/s²)', data:[] },
            { label:'Altitude (m)', data:[] },
          ]},
          options: baseLineOpts()
        }));
        if (created) { charts.chartVAA = created; backfillFixedCharts(); }
      }
    } else { charts.chartVAA = null; }

    // AV
    if (els.canvasAV){
      if (!charts.chartAV || !chartUsable(charts.chartAV) || !canvasReady(charts.chartAV?.canvas)){
        const created = ensureChart(els.canvasAV, ()=>({
          type:'line',
          data:{ labels:[], datasets:[
            { label:'|Acceleration| (m/s²)', data:[] },
            { label:'Velocity (m/s)', data:[] },
          ]},
          options: baseLineOpts()
        }));
        if (created) { charts.chartAV = created; backfillFixedCharts(); }
      }
    } else { charts.chartAV = null; }
  }

  function backfillFixedCharts(){
    if (!buffer.length) return;
    if (charts.chartVAA && (!chartUsable(charts.chartVAA) || !canvasReady(charts.chartVAA.canvas))) charts.chartVAA = nukeChart(charts.chartVAA);
    if (charts.chartAV  && (!chartUsable(charts.chartAV)  || !canvasReady(charts.chartAV.canvas)))   charts.chartAV  = nukeChart(charts.chartAV);

    if (charts.chartVAA) {
      const d=charts.chartVAA.data; d.labels=[]; d.datasets.forEach(ds=>ds.data=[]);
      for (const t of buffer) pushPoint(charts.chartVAA, String(t.time??''), [+t.vel||0, accelMag(t), +t.alt||0]);
    }
    if (charts.chartAV) {
      const d=charts.chartAV.data; d.labels=[]; d.datasets.forEach(ds=>ds.data=[]);
      for (const t of buffer) pushPoint(charts.chartAV, String(t.time??''), [accelMag(t), +t.vel||0]);
    }
    resizeAllCharts();
  }

  function updateFixedCharts(t){
    ensureUsableOrNull('chartVAA'); ensureUsableOrNull('chartAV');
    const label = String(t.time ?? '');
    if (charts.chartVAA) pushPoint(charts.chartVAA, label, [+t.vel||0, accelMag(t), +t.alt||0]);
    if (charts.chartAV)  pushPoint(charts.chartAV,  label, [accelMag(t), +t.vel||0]);
  }

  // ---------- Continuity charts ----------
  function attachOrInitContinuityCharts(){
    if (els.canvasMainC){
      if (!charts.mainCont || !chartUsable(charts.mainCont) || !canvasReady(charts.mainCont?.canvas)){
        const created = ensureChart(els.canvasMainC, ()=>({
          type:'line', data:{ labels:[], datasets:[{ label:'Main', data:[] }] }, options: lineOpts01()
        }));
        if (created) { charts.mainCont = created; backfillContinuityCharts(); }
      }
    } else { charts.mainCont = null; }

    if (els.canvasDrogC){
      if (!charts.drogCont || !chartUsable(charts.drogCont) || !canvasReady(charts.drogCont?.canvas)){
        const created = ensureChart(els.canvasDrogC, ()=>({
          type:'line', data:{ labels:[], datasets:[{ label:'Drogue', data:[] }] }, options: lineOpts01()
        }));
        if (created) { charts.drogCont = created; backfillContinuityCharts(); }
      }
    } else { charts.drogCont = null; }
  }

  function backfillContinuityCharts(){
    if (!buffer.length) return;
    if (charts.mainCont && (!chartUsable(charts.mainCont) || !canvasReady(charts.mainCont.canvas))) charts.mainCont = nukeChart(charts.mainCont);
    if (charts.drogCont && (!chartUsable(charts.drogCont) || !canvasReady(charts.drogCont.canvas))) charts.drogCont = nukeChart(charts.drogCont);

    if (charts.mainCont){
      const d=charts.mainCont.data; d.labels=[]; d.datasets.forEach(ds=>ds.data=[]);
      for (const t of buffer) pushPoint(charts.mainCont, String(t.time??''), [(+t.main?1:0)]);
    }
    if (charts.drogCont){
      const d=charts.drogCont.data; d.labels=[]; d.datasets.forEach(ds=>ds.data=[]);
      for (const t of buffer) pushPoint(charts.drogCont, String(t.time??''), [(+t.drog?1:0)]);
    }
    resizeAllCharts();
  }

  function updateContinuityCharts(t){
    ensureUsableOrNull('mainCont'); ensureUsableOrNull('drogCont');
    const label = String(t.time ?? '');
    if (charts.mainCont) pushPoint(charts.mainCont, label, [(+t.main?1:0)]);
    if (charts.drogCont) pushPoint(charts.drogCont, label, [(+t.drog?1:0)]);
  }

  // ---------- Dynamic charts ----------
  function initDynamicChartForCanvas(canvas, selectionForFallback){
    if (!hasChart() || !canvasReady(canvas)) return; // only when visible
    let inst = Chart.getChart(canvas);
    if (!inst){
      try {
        inst = new Chart(canvas.getContext('2d'), {
          type:'line', data:{ labels:[], datasets:[{ label:'Velocity (m/s)', data:[] }] }, options: baseLineOpts()
        });
      } catch (e) {
        console.warn('[telemetry] dynamic chart create failed (hidden?):', e);
        return;
      }
    }
    const { fns, sig } = accessorsForChart(inst, selectionForFallback || pendingSelection);
    charts.dynamic.set(canvas, { chart: inst, accessors: fns, lastSig: sig });
    backfillDynamicChart(canvas);
    pendingSelection = null;
  }

  function backfillDynamicChart(canvas){
    const meta = charts.dynamic.get(canvas);
    if (!meta) return;
    const { chart, accessors } = meta;
    if (!chartUsable(chart) || !canvasReady(chart.canvas) || !buffer.length) return;
    const d = chart.data; d.labels=[]; d.datasets.forEach(ds=>ds.data=[]);
    for (const t of buffer) pushPoint(chart, String(t.time??''), accessors.map(fn => +fn(t)||0));
    safeUpdate(chart, 'none');
  }

  function updateDynamicCharts(t){
    for (const [canvas, meta] of charts.dynamic.entries()){
      const { chart, accessors } = meta;
      if (!chartUsable(chart) || !canvasReady(chart.canvas)){
        charts.dynamic.delete(canvas);
        nukeChart(chart);
        continue;
      }
      pushPoint(chart, String(t.time ?? ''), accessors.map(fn => +fn(t)||0));
    }
  }

  // ---------- Telemetry ingest ----------
  function onTelemetry(t){
    buffer.push(t);
    if (buffer.length > MAX_POINTS) buffer.shift();

    updateFields(t);
    updateFixedCharts(t);
    updateContinuityCharts(t);
    updateDynamicCharts(t);
  }

  // ---------- Tab / visibility / resize ----------
  function resizeAllCharts(){
    [charts.chartVAA, charts.chartAV, charts.mainCont, charts.drogCont].forEach(ch => { if (chartUsable(ch) && canvasReady(ch.canvas)) safeUpdate(ch, 'none'); });
    for (const { chart } of charts.dynamic.values()) if (chartUsable(chart) && canvasReady(chart.canvas)) safeUpdate(chart, 'none');
  }

  document.addEventListener('shown.bs.tab', () => {
    refreshEls();
    attachOrInitFixedCharts();
    attachOrInitContinuityCharts();
    bindAllDynamicCanvases();
    // Backfill again in case canvases were recreated
    backfillFixedCharts();
    backfillContinuityCharts();
    for (const c of charts.dynamic.keys()) backfillDynamicChart(c);
    resizeAllCharts();
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden){
      refreshEls();
      attachOrInitFixedCharts();
      attachOrInitContinuityCharts();
      bindAllDynamicCanvases();
      backfillFixedCharts();
      backfillContinuityCharts();
      for (const c of charts.dynamic.keys()) backfillDynamicChart(c);
      resizeAllCharts();
    }
  });
  window.addEventListener('resize', resizeAllCharts);

  // ---------- Dynamic graph hooks ----------
  function setupAddGraphHook(){
    if (!els.addGraphBtn || !els.telemetrySelect) return;
    els.addGraphBtn.addEventListener('click', () => {
      pendingSelection = Array.from(els.telemetrySelect.selectedOptions).map(o => o.value);
      // The canvas will appear later; our observer will bind/backfill it.
    });
  }

  function bindAllDynamicCanvases(){
    if (!els.graphContainer) return;
    els.graphContainer.querySelectorAll('canvas').forEach(canvas => {
      if (!canvas.isConnected) return;
      // Skip fixed canvases
      if (canvas === els.canvasVAA || canvas === els.canvasAV) return;

      const inst = hasChart() ? Chart.getChart(canvas) : null;
      if (inst && !charts.dynamic.has(canvas)){
        const { fns, sig } = accessorsForChart(inst, pendingSelection);
        charts.dynamic.set(canvas, { chart: inst, accessors: fns, lastSig: sig });
        backfillDynamicChart(canvas);
        pendingSelection = null;
      } else if (!inst && !charts.dynamic.has(canvas)){
        initDynamicChartForCanvas(canvas, pendingSelection);
      } else if (inst && charts.dynamic.has(canvas)){
        const meta = charts.dynamic.get(canvas);
        const sigNow = (inst.data?.datasets||[]).map(d=>d.label||'').join('|');
        if (sigNow !== meta.lastSig){
          const { fns, sig } = accessorsForChart(inst, pendingSelection);
          meta.accessors = fns; meta.lastSig = sig;
          backfillDynamicChart(canvas);
          pendingSelection = null;
        }
      }
    });
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
    bindAllDynamicCanvases();
    setupAddGraphHook();

    mo.observe(document.documentElement, { childList:true, subtree:true });

    if (typeof window.EventSource !== 'undefined'){
      es = new EventSource('/api/telemetry/stream');
      es.onopen = () => console.log('[telemetry] connected');
      es.onmessage = (ev) => { if (ev.data) { try { onTelemetry(JSON.parse(ev.data)); } catch(e){ console.error('telemetry parse', e); } } };
      es.onerror = () => {
        try { es.close(); } catch(_){}
        setTimeout(() => {
          es = new EventSource('/api/telemetry/stream');
          es.onopen = () => console.log('[telemetry] reconnected');
          es.onmessage = (ev) => { if (ev.data) { try { onTelemetry(JSON.parse(ev.data)); } catch(e){} } };
        }, 1500);
      };
    }
  });
})();
