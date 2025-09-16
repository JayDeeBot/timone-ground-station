// Unified chart manager for Flight Data graphs (fixed + user-added)
class GraphManager {
  constructor() {
    this.charts = {};                 // canvasId -> Chart instance
    this.draggedCol = null;
    this.resizedCol = null;
    this.startY = 0;
    this.startH = 0;

    // Fixed chart canvas IDs
    this.fixedIds = ['chartVAA', 'chartAV'];

    // Consistent palette (do not set fill/background to keep clean lines)
    this.palette = [
      '#1f77b4', // blue
      '#ff7f0e', // orange
      '#2ca02c', // green
      '#d62728', // red
      '#9467bd', // purple
      '#8c564b'  // brown
    ];

    // Render when Status tab is shown
    document.addEventListener('shown.bs.tab', (e) => {
      if (e.target && e.target.id === 'status-tab') this.renderAll(true);
    });

    // Also try once if status is already active at load
    document.addEventListener('DOMContentLoaded', () => {
      const statusPane = document.getElementById('status');
      if (statusPane && statusPane.classList.contains('show') && statusPane.classList.contains('active')) {
        this.renderAll(true);
      }
    });

    // expose API for main.js
    window.graphManager = this;
  }

  // ---------- Data presence ----------
  hasFlightData() {
    const fd = window.flightData;
    return !!(fd &&
      Array.isArray(fd.t) && Array.isArray(fd.vel) &&
      Array.isArray(fd.acc) && Array.isArray(fd.alt));
  }

  // ---------- Unified style for ALL charts ----------
  baseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: true, position: 'top', labels: { usePointStyle: false } } },
      elements: { point: { radius: 0 } },
      scales: {
        x: { title: { display: true, text: 'Time (s)' } },
        y: { beginAtZero: true }
      }
    };
  }

  // ---------- Render fixed + existing ----------
  renderAll(force = false) {
    // Ensure DnD for existing cards (no resize handle anywhere now)
    document.querySelectorAll('#graphContainer .col-12.col-md-6').forEach(col => {
      const card = col.querySelector('.graph-card');
      if (card) {
        this.initDrag(col);
        this.bindRemove(card); // removes button on non-fixed only
      }
    });

    // Render fixed charts with unified style (ALWAYS render — even with no data)
    this.renderFixed('chartVAA', force);
    this.renderFixed('chartAV', force);
  }

  renderFixed(id, force) {
    const canvas = document.getElementById(id);
    if (!canvas) return;

    // Hide the "No flight data yet" placeholder for fixed charts so the canvas is visible
    const placeholder = canvas.parentElement?.querySelector(`.placeholder[data-for="${id}"]`);
    if (placeholder) placeholder.classList.add('d-none');

    if (force && this.charts[id]) {
      try { this.charts[id].destroy(); } catch {}
      delete this.charts[id];
    }

    const fd = window.flightData || {};
    const labels = Array.isArray(fd.t) ? fd.t : [];

    let datasets;
    if (id === 'chartVAA') {
      datasets = [
        { label: 'Velocity ( m/s )',      data: Array.isArray(fd.vel) ? fd.vel : [], borderWidth: 2, tension: 0.2, borderColor: this.palette[0] },
        { label: 'Acceleration ( m/s² )', data: Array.isArray(fd.acc) ? fd.acc : [], borderWidth: 2, tension: 0.2, borderColor: this.palette[1] },
        { label: 'Altitude ( m )',        data: Array.isArray(fd.alt) ? fd.alt : [], borderWidth: 2, tension: 0.2, borderColor: this.palette[2] }
      ];
    } else {
      datasets = [
        { label: 'Acceleration ( m/s² )', data: Array.isArray(fd.acc) ? fd.acc : [], borderWidth: 2, tension: 0.2, borderColor: this.palette[1] },
        { label: 'Velocity ( m/s )',      data: Array.isArray(fd.vel) ? fd.vel : [], borderWidth: 2, tension: 0.2, borderColor: this.palette[0] }
      ];
    }

    if (this.charts[id]) {
      this.charts[id].data.labels = labels;
      this.charts[id].data.datasets = datasets;
      this.charts[id].update();
    } else {
      const ctx = canvas.getContext('2d');
      this.charts[id] = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: this.baseOptions()
      });
    }
  }

  // ---------- Public API for adding graphs (called by main.js) ----------
  addGraph(metric) {
    const map = {
      velocity:     { key: 'vel', label: 'Velocity ( m/s )',      title: 'Velocity' },
      acceleration: { key: 'acc', label: 'Acceleration ( m/s² )', title: 'Acceleration' },
      altitude:     { key: 'alt', label: 'Altitude ( m )',        title: 'Altitude' },
      temperature:  { key: 'temp', label: 'Temperature ( °C )',   title: 'Temperature' },
      pressure:     { key: 'pres', label: 'Pressure ( Pa )',      title: 'Pressure' }
    };
    const meta = map[metric];
    if (!meta) return;

    const container = document.getElementById('graphContainer');
    if (!container) return;

    // Always put each chart in a col-12 col-md-6 to force max two per row
    const col = document.createElement('div');
    col.className = 'col-12 col-md-6';

    const canvasId = `chart_${metric}_${Date.now()}`;

    col.innerHTML = `
      <div class="card graph-card">
        <div class="card-header py-2 d-flex justify-content-between align-items-center">
          <span class="fw-semibold">${meta.title}</span>
          <button class="btn btn-sm btn-outline-danger remove-graph" title="Remove">
            <i class="bi bi-x-lg"></i>
          </button>
        </div>
        <div class="card-body">
          <div class="placeholder text-muted small d-none" data-for="${canvasId}">No flight data yet.</div>
          <canvas id="${canvasId}" height="130"></canvas>
        </div>
      </div>
    `;

    container.appendChild(col);

    this.initDrag(col);
    this.bindRemove(col.querySelector('.graph-card'));

    // Render with unified style
    const canvas = col.querySelector('canvas');
    const ctx = canvas.getContext('2d');
    const fd = window.flightData || {};
    const labels = Array.isArray(fd.t) ? fd.t : [];
    const seriesData = Array.isArray(fd[meta.key]) ? fd[meta.key] : [];

    const color = this.palette[0]; // single-series; consistent first color
    this.charts[canvasId] = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: meta.label, data: seriesData, borderWidth: 2, tension: 0.2, borderColor: color }
        ]
      },
      options: this.baseOptions()
    });
  }

  // ---------- Drag & Drop ----------
  initDrag(col) {
    if (col.dataset.dndInit === '1') return;
    col.dataset.dndInit = '1';

    col.setAttribute('draggable', 'true');

    col.addEventListener('dragstart', (e) => {
      this.draggedCol = col;
      col.classList.add('dragging');
      e.dataTransfer.setData('text/plain', '');
    });

    col.addEventListener('dragend', () => {
      col.classList.remove('dragging');
      document.querySelectorAll('#graphContainer .col-12.col-md-6').forEach(c => c.classList.remove('drop-target'));
      Object.values(this.charts).forEach(c => c.resize());
    });

    col.addEventListener('dragover', (e) => {
      e.preventDefault();
      const over = e.currentTarget;
      if (this.draggedCol !== over) over.classList.add('drop-target');
    });

    col.addEventListener('dragleave', (e) => {
      e.currentTarget.classList.remove('drop-target');
    });

    col.addEventListener('drop', (e) => {
      e.preventDefault();
      const target = e.currentTarget;
      if (this.draggedCol && this.draggedCol !== target) {
        const container = document.getElementById('graphContainer');
        const r = target.getBoundingClientRect();
        if (e.clientY < r.top + r.height / 2) {
          container.insertBefore(this.draggedCol, target);
        } else {
          container.insertBefore(this.draggedCol, target.nextSibling);
        }
        Object.values(this.charts).forEach(c => c.resize());
      }
      target.classList.remove('drop-target');
    });
  }

  // ---------- Remove button (disabled for fixed) ----------
  bindRemove(card) {
    if (card.dataset.removeInit === '1') return;
    card.dataset.removeInit = '1';

    const isFixed = card.dataset.fixed === '1';
    const btn = card.querySelector('.remove-graph');
    if (!btn) return;

    if (isFixed) {
      btn.remove(); // fixed graphs cannot be removed
      return;
    }

    btn.addEventListener('click', () => {
      const col = card.closest('.col-12.col-md-6') || card;
      const canvas = col.querySelector('canvas');
      if (canvas && this.charts[canvas.id]) {
        try { this.charts[canvas.id].destroy(); } catch {}
        delete this.charts[canvas.id];
      }
      col.remove();
    });
  }
}

// Singleton
new GraphManager();
