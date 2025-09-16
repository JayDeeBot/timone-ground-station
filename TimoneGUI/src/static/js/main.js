// Add Chart.js to base.html before this file
document.addEventListener('DOMContentLoaded', function() {
  // Registry for continuity charts only (the unified flight charts live in graph-interactions.js)
  const charts = {};

  // Initialize continuity charts
  initContinuityCharts();

  // Initialize Add Graph functionality (delegates to GraphManager for unified styling & layout)
  initAddGraphButton();

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

  function initAddGraphButton() {
    const addGraphBtn = document.getElementById('addGraphBtn');
    if (!addGraphBtn) return;

    addGraphBtn.addEventListener('click', () => {
      const select = document.getElementById('telemetrySelect');
      if (!select) return;

      const selectedMetrics = Array.from(select.selectedOptions).map(o => o.value);
      if (!selectedMetrics.length) return;

      // Delegate to the unified GraphManager so titles, legend, styles, and 2-per-row layout match fixed charts
      selectedMetrics.forEach(metric => {
        if (window.graphManager && typeof window.graphManager.addGraph === 'function') {
          window.graphManager.addGraph(metric); // e.g., 'acceleration', 'velocity', etc.
        }
      });

      // Close modal & reset selection
      const modal = bootstrap.Modal.getInstance(document.getElementById('addGraphModal'));
      if (modal) modal.hide();
      select.selectedIndex = -1;
    });
  }

  // --- Optional legacy charts (safe no-ops unless canvases exist) ---
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

  // --- Radio status helpers (unchanged) ---
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
