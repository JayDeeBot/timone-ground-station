// Add Chart.js to base.html before this file
document.addEventListener('DOMContentLoaded', function() {
  // Only keep continuity charts in main.js registry
  const charts = {};

  // --- Init panels ---
  initContinuityCharts();

  // --- Notifier (toast-style) + ding sound, app-wide ---
  (function initNotifier(){
    if (window.notify) return;

    // Inject minimal styles
    if (!document.getElementById('notif-style')) {
      const css = `
      .notif-wrap{position:fixed;right:16px;bottom:16px;z-index:99999;display:flex;flex-direction:column;gap:8px;pointer-events:none}
      .notif{
        pointer-events:auto;background:#fff;border-radius:10px;box-shadow:0 6px 18px rgba(0,0,0,.18);
        padding:10px 12px;min-width:260px;max-width:360px;border:1px solid rgba(0,0,0,.08);
        animation:notifSlide .18s ease-out;
      }
      .notif h6{margin:0 0 4px;font-size:14px;font-weight:700}
      .notif p{margin:0;font-size:13px;line-height:1.35;color:#222}
      .notif .meta{margin-top:6px;font-size:12px;color:#666}
      .notif.good h6{color:#198754}
      .notif.warn h6{color:#ffc107}
      .notif.bad  h6{color:#dc3545}
      @keyframes notifSlide{from{transform:translateY(6px);opacity:.0}to{transform:translateY(0);opacity:1}}
      `;
      const style = document.createElement('style');
      style.id = 'notif-style';
      style.textContent = css;
      document.head.appendChild(style);
    }

    // Container
    let wrap = document.getElementById('notif-wrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'notif-wrap';
      wrap.className = 'notif-wrap';
      document.body.appendChild(wrap);
    }

    // --- WebAudio ding with strict user-gesture gating ---
    let _ac = null;                 // AudioContext (created only after gesture)
    let _armed = false;             // has the user interacted?
    let _pendingDings = 0;          // dings queued before we're armed
    let _bound = false;             // have we bound the gesture listeners?

    function _cleanupGestureListeners(fn) {
      document.removeEventListener('click', fn);
      document.removeEventListener('touchstart', fn);
      document.removeEventListener('keydown', fn);
    }

    function _armOnFirstGesture() {
      if (_armed) return;
      // If Chrome already reports user activation, arm immediately
      if (navigator.userActivation && navigator.userActivation.hasBeenActive) {
        _armed = true;
        try {
          _ac = new (window.AudioContext || window.webkitAudioContext)();
          _ac.resume().catch(()=>{}).finally(_flushPending);
        } catch { /* ignore */ }
        return;
      }
      if (_bound) return;
      _bound = true;
      const onFirst = () => {
        _armed = true;
        try {
          _ac = new (window.AudioContext || window.webkitAudioContext)();
          _ac.resume().catch(()=>{}).finally(_flushPending);
        } catch { /* ignore */ }
        _cleanupGestureListeners(onFirst);
      };
      const opts = { once: true, passive: true };
      document.addEventListener('click', onFirst, opts);
      document.addEventListener('touchstart', onFirst, opts);
      document.addEventListener('keydown', onFirst, opts);
    }

    function _beepOnce(delayMs = 0) {
      if (!_ac || _ac.state !== 'running') return;
      try {
        const o = _ac.createOscillator();
        const g = _ac.createGain();
        o.type = 'sine';
        o.frequency.value = 880; // A5
        g.gain.value = 0.0001;
        o.connect(g); g.connect(_ac.destination);
        const start = _ac.currentTime + (delayMs/1000);
        const end   = start + 0.32;
        o.start(start);
        g.gain.exponentialRampToValueAtTime(0.14, start + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, end - 0.02);
        o.stop(end);
      } catch { /* ignore */ }
    }

    function _flushPending() {
      if (!_ac || _ac.state !== 'running') return;
      const n = Math.min(_pendingDings, 3); // cap burst
      _pendingDings = 0;
      for (let i = 0; i < n; i++) _beepOnce(i * 60);
    }

    function playDing() {
      if (!_armed) {
        _pendingDings++;
        _armOnFirstGesture();  // bind listeners and arm later
        return;
      }
      if (!_ac) {
        try {
          _ac = new (window.AudioContext || window.webkitAudioContext)();
        } catch { return; }
      }
      if (_ac.state !== 'running') {
        _pendingDings++;
        _ac.resume().catch(()=>{}).finally(_flushPending);
        return;
      }
      _beepOnce();
    }

    // public API
    window.notify = function(title, message, opts = {}) {
      const level = opts.level || ''; // '', 'good', 'warn', 'bad'
      const el = document.createElement('div');
      el.className = `notif ${level}`;
      el.innerHTML = `<h6>${title}</h6><p>${message}</p>`;
      wrap.appendChild(el);

      if (!opts.silent) playDing();

      const ttl = Number.isFinite(opts.ttl) ? opts.ttl : 5000;
      setTimeout(() => {
        el.style.transition = 'opacity .2s ease, transform .2s ease';
        el.style.opacity = '0';
        el.style.transform = 'translateY(6px)';
        setTimeout(() => el.remove(), 220);
      }, ttl);
    };
  })();

  // Keep only continuity charts logic
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

      // Keep continuity charts responsive
      initResize(drogueContinuityCtx.parentElement);
      initResize(mainContinuityCtx.parentElement);
    }
  }

  // Keep the resize observer logic for continuity charts
  function initResize(graphElement) {
    const resizeObserver = new ResizeObserver(entries => {
      requestAnimationFrame(() => {  // Wrap in rAF for better performance
        for (const entry of entries) {
          const canvas = entry.target.querySelector('canvas');
          if (!canvas || !canvas.isConnected) {
            try { resizeObserver.unobserve(entry.target); } catch (_) {}
            continue;
          }

          const id = canvas.id;
          if (!id) continue;

          // Check local registry first
          const chart = charts[id];
          if (chart) {
            try {
              if (chart.canvas && chart.canvas.isConnected) {
                chart.resize();
              } else {
                try { resizeObserver.unobserve(entry.target); } catch (_) {}
              }
            } catch (e) {
              console.warn('[charts] resize failed:', e);
              try { resizeObserver.unobserve(entry.target); } catch (_) {}
            }
          }
        }
      });
    });

    if (graphElement && graphElement.isConnected) {
      try {
        resizeObserver.observe(graphElement);
      } catch (e) {
        console.warn('[charts] observe failed:', e);
      }
    }

    // Return for cleanup
    return resizeObserver;
  }

  // Cleanup on page unload
  const observers = new Set();
  window.addEventListener('pagehide', () => {
    observers.forEach(o => { try { o.disconnect(); } catch (_) {} });
    observers.clear();
  }, { capture: true });

  // Keep radio status helper
  function updateRadioStatus(radio, connected, healthy) {
    const connectedEl = document.getElementById(`${radio}Connected`);
    const healthEl    = document.getElementById(`${radio}Health`);
    if (connectedEl) connectedEl.className = `status-indicator ${connected ? 'connected' : 'disconnected'}`;
    if (healthEl)    healthEl.className    = `status-indicator ${healthy ? 'healthy' : 'unhealthy'}`;
  }
  window.updateRadioStatus = updateRadioStatus;
});

// Keep any tab-change logging you had before (muted)
// document.addEventListener('shown.bs.tab', function (event) {
//   console.log('Tab shown:', event.target.id);
// });
