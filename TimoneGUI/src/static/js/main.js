// Add Chart.js to base.html before this file
document.addEventListener('DOMContentLoaded', function() {
  // Registry for continuity charts only (the unified flight charts live in graph-interactions.js)
  const charts = {};
  const STORAGE_KEY = 'flightDataDynamicGraphs_v1';

  // --- Init panels ---
  initContinuityCharts();
  initVoltageCurrentCharts();     // fixed Voltage + Current charts
  restoreSavedGraphs();           // persistence for dynamically-added graphs
  initAddGraphButton();

  // ---------------- Continuity charts (unchanged) ----------------
  function initContinuityCharts() {
    const drogueContinuityCtx = document.getElementById('drogueContinuityChart');
    const mainContinuityCtx   = document.getElementById('mainContinuityChart');

    if (drogueContinuityCtx && mainContinuityCtx) {
      // Drogue continuity
      const drogueChart = new Chart(drogueContinuityCtx, {
        type: 'line',
        data: {
          labels: [],
          datasets: [{
            label: 'Drogue',
            data: [],
            stepped: true,
            borderColor: 'rgb(75, 192, 192)',
            tension: 0.1,
            pointRadius: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            y: {
              min: 0,
              max: 1,
              ticks: { stepSize: 1, callback: v => (v === 0 ? 'Disconnected' : 'Connected') }
            }
          }
        }
      });

      // Main continuity
      const mainChart = new Chart(mainContinuityCtx, {
        type: 'line',
        data: {
          labels: [],
          datasets: [{
            label: 'Main',
            data: [],
            stepped: true,
            borderColor: 'rgb(255, 99, 132)',
            tension: 0.1,
            pointRadius: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            y: {
              min: 0,
              max: 1,
              ticks: { stepSize: 1, callback: v => (v === 0 ? 'Disconnected' : 'Connected') }
            }
          }
        }
      });

      charts['drogueContinuityChart'] = drogueChart;
      charts['mainContinuityChart']   = mainChart;

      // Keep continuity charts responsive to container size
      initResize(drogueContinuityCtx.parentElement);
      initResize(mainContinuityCtx.parentElement);
    }
  }

  // ---------------- Fixed Voltage & Current charts ----------------
  function initVoltageCurrentCharts() {
    const vCtx = document.getElementById('chartVoltage');
    const cCtx = document.getElementById('chartCurrent');
    if (!vCtx || !cCtx) return;

    const baseOpts = {
      type: 'line',
      data: { labels: [], datasets: [{ label: '', data: [], tension: 0.1, pointRadius: 0 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { y: { beginAtZero: false } },
        plugins: { legend: { display: true } }
      }
    };

    const voltageChart = new Chart(vCtx, JSON.parse(JSON.stringify(baseOpts)));
    voltageChart.data.datasets[0].label = 'Voltage (V)';

    const currentChart = new Chart(cCtx, JSON.parse(JSON.stringify(baseOpts)));
    currentChart.data.datasets[0].label = 'Current (A)';

    // Expose for live updates from telemetry stream
    window.flightCharts = window.flightCharts || {};
    window.flightCharts['chartVoltage'] = voltageChart;
    window.flightCharts['chartCurrent'] = currentChart;

    // Convenience updater (optional use from your telemetry)
    // Also updates Battery panel text (#battery-volts, #battery-curr) if present.
    window.updateVoltageCurrent = function(tsISO, voltage, current) {
      const v = window.flightCharts['chartVoltage'];
      const c = window.flightCharts['chartCurrent'];
      if (v) {
        v.data.labels.push(tsISO);
        v.data.datasets[0].data.push(voltage);
        v.update('none');
      }
      if (c) {
        c.data.labels.push(tsISO);
        c.data.datasets[0].data.push(current);
        c.update('none');
      }

      const voltsEl = document.getElementById('battery-volts');
      const currEl  = document.getElementById('battery-curr');
      if (voltsEl && typeof voltage !== 'undefined' && voltage !== null) {
        voltsEl.textContent = String(voltage);
      }
      if (currEl && typeof current !== 'undefined' && current !== null) {
        currEl.textContent = String(current);
      }
    };
  }

  // ---------------- Add Graph button (persist selections) ----------------
  function initAddGraphButton() {
    const addGraphBtn = document.getElementById('addGraphBtn');
    if (!addGraphBtn) return;

    addGraphBtn.addEventListener('click', () => {
      const select = document.getElementById('telemetrySelect');
      if (!select) return;

      const selectedMetrics = Array.from(select.selectedOptions).map(o => o.value);
      if (!selectedMetrics.length) return;

      // Delegate to GraphManager so titles, legend, styles, and layout match fixed charts
      selectedMetrics.forEach(metric => {
        if (window.graphManager && typeof window.graphManager.addGraph === 'function') {
          window.graphManager.addGraph(metric); // e.g., 'acceleration', 'velocity', etc.
        }
      });

      // PERSIST: merge into saved metric list (avoid duplicates)
      addSavedMetrics(selectedMetrics);

      // Close modal & reset selection
      const modal = bootstrap.Modal.getInstance(document.getElementById('addGraphModal'));
      if (modal) modal.hide();
      select.selectedIndex = -1;
    });

    // OPTIONAL: if GraphManager dispatches a custom removal event, persist the removal.
    // Emit from GraphManager: document.dispatchEvent(new CustomEvent('graph:removed', { detail: { metric } }));
    document.addEventListener('graph:removed', (e) => {
      if (e?.detail?.metric) removeSavedMetric(e.detail.metric);
    });
  }

  // ---------------- Persistence helpers (localStorage) ----------------
  function restoreSavedGraphs() {
    const saved = readSavedMetrics();
    if (!Array.isArray(saved) || !saved.length) return;

    // Recreate each saved metric graph via GraphManager
    saved.forEach(metric => {
      if (window.graphManager && typeof window.graphManager.addGraph === 'function') {
        window.graphManager.addGraph(metric);
      }
    });
  }

  function readSavedMetrics() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : { metrics: [] };
      return parsed.metrics || [];
    } catch {
      return [];
    }
  }

  function writeSavedMetrics(metricsArr) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ metrics: metricsArr }));
    } catch { /* no-op */ }
  }

  function addSavedMetrics(newOnes) {
    const existing = new Set(readSavedMetrics());
    newOnes.forEach(m => existing.add(m));
    writeSavedMetrics(Array.from(existing));
  }

  function removeSavedMetric(metric) {
    const existing = readSavedMetrics().filter(m => m !== metric);
    writeSavedMetrics(existing);
  }

  // ---------------- Legacy charts (safe no-ops unless canvases exist) ----------------
  const legacyFlightCtx = document.getElementById('flightChart');
  if (legacyFlightCtx) {
    new Chart(legacyFlightCtx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          { label: 'Altitude (m)',            data: [], borderColor: 'rgb(75, 192, 192)',  tension: 0.1, pointRadius: 0 },
          { label: 'Vertical Velocity (m/s)', data: [], borderColor: 'rgb(255, 99, 132)', tension: 0.1, pointRadius: 0 }
        ]
      },
      options: { responsive: true, scales: { y: { beginAtZero: true } } }
    });
  }

  const legacyTelemetryCtx = document.getElementById('telemetryChart');
  if (legacyTelemetryCtx) {
    new Chart(legacyTelemetryCtx, {
      type: 'line',
      data: { labels: [], datasets: [] },
      options: { responsive: true, scales: { y: { beginAtZero: true } } }
    });
  }

  // ---------------- Radio status helpers (unchanged) ----------------
  function updateRadioStatus(radio, connected, healthy) {
    const connectedEl = document.getElementById(`${radio}Connected`);
    const healthEl    = document.getElementById(`${radio}Health`);
    if (connectedEl) connectedEl.className = `status-indicator ${connected ? 'connected' : 'disconnected'}`;
    if (healthEl)    healthEl.className    = `status-indicator ${healthy ? 'healthy' : 'unhealthy'}`;
  }
  window.updateRadioStatus = updateRadioStatus;

  function initResize(graphElement) {
    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        const canvas = entry.target.querySelector('canvas');
        if (!canvas) continue;
        const id = canvas.id;
        if (charts[id]) charts[id].resize(); // only continuity charts are in this local registry
      }
    });
    if (graphElement) resizeObserver.observe(graphElement);
  }
});

// Keep any tab-change logging you had before (muted)
// document.addEventListener('shown.bs.tab', function (event) {
//   console.log('Tab shown:', event.target.id);
// });
