document.addEventListener('DOMContentLoaded', function() {
  const radio433Form = document.getElementById('radio433Form');
  const radio915Form = document.getElementById('radio915Form');

  // Inputs for 433
  const bw433 = document.getElementById('bandwidth433');
  const cr433 = document.getElementById('codingRate433');
  const sf433 = document.getElementById('spreadingFactor433');

  // Inputs for 915
  const bw915 = document.getElementById('bandwidth915');
  const cr915 = document.getElementById('codingRate915');
  const sf915 = document.getElementById('spreadingFactor915');

  // ---- WebSocket bridge (resilient, quiet) ----
  let ws = null;
  let wsReady = false;
  let wsQueue = [];
  let reconnectTimer = null;
  let backoffMs = 1000;           // start at 1s
  const maxBackoffMs = 30000;     // cap 30s
  const statusEl = document.querySelector('#bridgeStatus [data-bridge-state]');

  function setBridgeState(txt) {
    if (statusEl) statusEl.textContent = txt;
  }

  function bridgeURL() {
    const host = (window.SETTINGS_WS_HOST || window.location.hostname || '127.0.0.1');
    const port = (window.SETTINGS_WS_PORT || 8766);
    return `ws://${host}:${port}`;
  }

  function scheduleReconnect(reason) {
    if (document.hidden || !navigator.onLine) {
      setBridgeState(navigator.onLine ? 'paused (hidden)' : 'offline');
      return;
    }
    if (reconnectTimer) return;
    const jitter = Math.floor(Math.random() * 400); // 0–400ms
    const delay = Math.min(backoffMs + jitter, maxBackoffMs);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      backoffMs = Math.min(backoffMs * 2, maxBackoffMs);
      openWS();
    }, delay);
    if (reason && reason !== 'init') {
      setBridgeState(`reconnecting in ${Math.round(delay/1000)}s…`);
    }
  }

  function resetBackoff() { backoffMs = 1000; }

  function openWS() {
    try {
      setBridgeState('connecting…');
      ws = new WebSocket(bridgeURL());
    } catch (e) {
      console.debug('[settings] WS constructor failed:', e);
      scheduleReconnect('constructor_error');
      return;
    }

    ws.onopen = () => {
      wsReady = true;
      resetBackoff();
      setBridgeState('online');
      try {
        wsQueue.forEach(m => ws.send(m));
        wsQueue = [];
      } catch (e) {
        console.debug('[settings] WS flush failed:', e);
      }
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg?.type === 'hello') {
          // optionally reflect DRYRUN state if present
          if (typeof msg.dryrun !== 'undefined') {
            setBridgeState(`online${msg.dryrun ? ' (dry-run)' : ''}`);
          }
        }
        if (msg?.type === 'ack' && msg.for === 'radio_settings') {
          console.debug('[settings] bridge ack:', msg);
        }
      } catch (e) {
        console.debug('[settings] WS parse error:', e);
      }
    };

    ws.onclose = () => {
      wsReady = false;
      setBridgeState('offline');
      scheduleReconnect('close');
    };

    ws.onerror = () => {
      // Avoid loud console errors; onclose will schedule retry.
      console.debug('[settings] WS error (will retry quietly)');
      try { ws.close(); } catch (_) {}
    };
  }

  function sendOverWS(obj) {
    const text = JSON.stringify(obj);
    if (ws && wsReady && ws.readyState === WebSocket.OPEN) {
      try { ws.send(text); } catch (e) { wsQueue.push(text); }
    } else {
      wsQueue.push(text); // queue silently; will flush when online
      if (!ws || ws.readyState === WebSocket.CLOSED) {
        scheduleReconnect('init');
      }
    }
  }

  // Pause noisy reconnects while hidden/offline; resume on visible/online
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && navigator.onLine && (!ws || ws.readyState !== WebSocket.OPEN)) {
      scheduleReconnect('visible');
    }
  });
  window.addEventListener('online',  () => scheduleReconnect('online'));
  window.addEventListener('offline', () => setBridgeState('offline'));

  // Open once now (quiet auto-retry thereafter)
  openWS();

  // ---- Load settings on page load ----
  loadSettings();

  async function loadSettings() {
    try {
      const res = await fetch('/api/radio/settings', { method: 'GET' });
      if (!res.ok) throw new Error('Failed to load settings');
      const data = await res.json();
      applySettingsToForms(data);
      localStorage.setItem('radio_settings_cache', JSON.stringify(data));
    } catch (err) {
      console.debug('Error loading settings:', err);
      const cache = localStorage.getItem('radio_settings_cache');
      if (cache) {
        try { applySettingsToForms(JSON.parse(cache)); } catch {}
      }
    }
  }

  function applySettingsToForms(data) {
    if (data && data['433']) {
      if (bw433) bw433.value = data['433'].bandwidth;
      if (cr433) cr433.value = data['433'].codingRate;
      if (sf433) sf433.value = data['433'].spreadingFactor;
    }
    if (data && data['915']) {
      if (bw915) bw915.value = data['915'].bandwidth;
      if (cr915) cr915.value = data['915'].codingRate;
      if (sf915) sf915.value = data['915'].spreadingFactor;
    }
  }

  // ---- Submit handler (persist + bridge push) ----
  function handleFormSubmit(event, frequency) {
    event.preventDefault();

    const formData = new FormData(event.target);
    const settings = {
      bandwidth: parseFloat(formData.get('bandwidth')),
      codingRate: formData.get('codingRate'),
      spreadingFactor: parseInt(formData.get('spreadingFactor')),
      frequency: frequency
    };

    const submitBtn = event.submitter || event.target.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;

    fetch('/api/radio/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings)
    })
    .then(async (response) => {
      const json = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(json.error || 'Save failed');

      if (json.settings) {
        localStorage.setItem('radio_settings_cache', JSON.stringify(json.settings));
      }

      // Emit over WS bridge; queues if offline and flushes later
      sendOverWS({
        type: 'radio_settings',
        radio: String(frequency),           // "433" | "915"
        bandwidth: settings.bandwidth,      // kHz
        codingRate: settings.codingRate,    // "4/5" etc
        spreadingFactor: settings.spreadingFactor
      });

      alert(`${frequency} MHz radio settings updated successfully`);
    })
    .catch(error => {
      console.debug('Error updating radio settings:', error);
      alert(`Error updating ${frequency} MHz radio settings: ${error.message}`);
    })
    .finally(() => {
      if (submitBtn) submitBtn.disabled = false;
    });
  }

  if (radio433Form) {
    radio433Form.addEventListener('submit', (e) => handleFormSubmit(e, 433));
  }
  if (radio915Form) {
    radio915Form.addEventListener('submit', (e) => handleFormSubmit(e, 915));
  }
});
