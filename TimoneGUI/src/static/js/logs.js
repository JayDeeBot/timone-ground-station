(() => {
  const MAX_ENTRIES = 5000;

  const CHANNELS = {
    ground:  { key: 'timone_log_ground_v1',  title: 'Ground Station (gui_status.py)' },
    lora:    { key: 'timone_log_lora_v1',    title: 'LoRa Radio (915 & 433)' },
    periph1: { key: 'timone_log_p1_v1',      title: 'Peripheral 1' }, // ← current/voltage (+ barometer if present)
    periph2: { key: 'timone_log_p2_v1',      title: 'Peripheral 2' },
    periph3: { key: 'timone_log_p3_v1',      title: 'Peripheral 3' },
    periph4: { key: 'timone_log_p4_v1',      title: 'Peripheral 4' }
  };

  class PanelLog {
    constructor(channel, root) {
      this.channel   = channel;
      this.root      = root;
      this.cfg       = CHANNELS[channel];

      this.exportBtn  = this.root.querySelector(`[data-export="${channel}"]`);
      this.scrollWrap = this.root.querySelector(`#logContent-${channel}`);
      this.logContent = this.root.querySelector(`.log-content[data-channel="${channel}"]`);

      this.autoScroll = true;

      this._restore();

      // Export button event listener
      if (this.exportBtn) {
        this.exportBtn.addEventListener('click', () => this.export());
      }

      // Scroll event listener
      if (this.scrollWrap) {
        this.scrollWrap.addEventListener('scroll', () => {
          const nearBottom = (this.scrollWrap.scrollTop + this.scrollWrap.clientHeight) >= (this.scrollWrap.scrollHeight - 4);
          this.autoScroll = nearBottom;
        });
      }

      // Clear button event listener
      const clearBtn = this.root.querySelector(`[data-clear="${channel}"]`);
      if (clearBtn) {
        clearBtn.addEventListener('click', () => {
          if (confirm(`Clear all logs for ${this.cfg.title}?`)
          ) {
            this.clear();
          }
        });
      }
    }

    clear() {
      if (!this.logContent) return;
      this.logContent.innerHTML = '';
      localStorage.setItem(this.cfg.key, JSON.stringify([]));
    }

    // Render message-only (no timestamp, no level)
    addLogEntry(message, _level = 'info', _isoTimestamp = null, origin = null) {
      if (!this.logContent) return;
      const displayMsg = origin ? `[${origin}] ${message}` : message;

      const row = document.createElement('div');
      row.className = 'log-entry';
      row.innerHTML = `<span class="log-message">${escapeHtml(displayMsg)}</span>`;
      this.logContent.appendChild(row);

      this._persist({ message: displayMsg });

      if (this.autoScroll) this._scrollToBottom();
    }

    async export() {
      try {
        const entries = this._readAll();
        const content = entries.map(e => e.message).join('\n') + '\n';
        const fileHandle = await window.showSaveFilePicker({
          suggestedName: `${this.cfg.title.replace(/\s+/g, '_').toLowerCase()}_logs.txt`,
          types: [{ description: 'Text', accept: { 'text/plain': ['.txt'] } }]
        });
        const writable = await fileHandle.createWritable();
        await writable.write(content);
        await writable.close();
        this.addLogEntry('Logs exported successfully');
      } catch (err) {
        if (err && err.name !== 'AbortError') {
          console.error(`Export failed (${this.channel}):`, err);
          this.addLogEntry(`Failed to export logs: ${err.message || err}`, 'error');
        }
      }
    }

    _scrollToBottom() {
      if (!this.scrollWrap) return;
      requestAnimationFrame(() => {
        this.scrollWrap.scrollTop = this.scrollWrap.scrollHeight;
      });
    }

    _persist(entry) {
      const all = this._readAll();
      all.push({ message: String(entry.message ?? '') });
      if (all.length > MAX_ENTRIES) all.splice(0, all.length - MAX_ENTRIES);
      localStorage.setItem(this.cfg.key, JSON.stringify(all));
    }

    _readAll() {
      try {
        const raw = localStorage.getItem(this.cfg.key);
        const arr = raw ? JSON.parse(raw) : [];
        return Array.isArray(arr) ? arr.map(e => ({ message: String(e.message ?? '') })) : [];
      } catch {
        return [];
      }
    }

    _restore() {
      const entries = this._readAll();
      if (!this.logContent) return;
      this.logContent.innerHTML = '';
      for (const e of entries) {
        const row = document.createElement('div');
        row.className = 'log-entry';
        row.innerHTML = `<span class="log-message">${escapeHtml(e.message)}</span>`;
        this.logContent.appendChild(row);
      }
      if (entries.length) this._scrollToBottom();
    }
  }

  class LogsHub {
    constructor() {
      this.panels = {};
      this._initPanels();

      window.logs = {
        add: (channel, message, level = 'info', isoTimestamp = null, origin = null) => {
          this.panels[channel]?.addLogEntry(message, level, isoTimestamp, origin);
        },
        clear: (channel) => this.panels[channel]?.clear(),
        export: (channel) => this.panels[channel]?.export()
      };

      // Back-compat single-panel helpers map to ground only
      window.logManager = {
        addLogEntry: (message) => this.panels.ground?.addLogEntry(message),
        clearLog: () => this.panels.ground?.clear(),
        exportLogs: () => this.panels.ground?.export()
      };

      this._startSSE();
    }

    _initPanels() {
      for (const chan of Object.keys(CHANNELS)) {
        const root = document.querySelector(`.log-card[data-channel="${chan}"]`);
        if (!root) continue;
        this.panels[chan] = new PanelLog(chan, root);
      }
    }

    _startSSE() {
      if (typeof window.EventSource === 'undefined') {
        console.warn('[logs] EventSource not available');
        return;
      }
      const connect = () => {
        let es = new EventSource('/api/logs/stream');
        es.onopen = () => console.log('[logs] connected');
        es.onmessage = (ev) => { if (ev.data) this._routeIncomingLine(ev.data); };
        es.onerror = () => { try { es.close(); } catch {} ; setTimeout(connect, 2000); };
      };
      connect();
    }

    _routeIncomingLine(lineRaw) {
      let line = String(lineRaw).trim();
      if (!line) return;

      // Default
      let channel = 'ground';
      let origin  = null;

      // LoRa panel (both 915 & 433)
      if (/\[LoRa915\]/i.test(line)) {
        channel = 'lora'; origin = '915';
        line = line.replace(/\[LoRa915\]\s*/i, '');
      } else if (/\[Radio433\]/i.test(line)) {
        channel = 'lora'; origin = '433';
        line = line.replace(/\[Radio433\]\s*/i, '');
      }
      // Current/Voltage and Barometer - send only to ground
      else if (/\[(Current|CURR)\]/i.test(line)) {
        const modifiedLine = line.replace(/\[(Current|CURR)\]\s*/i, '');
        this.panels['ground']?.addLogEntry(modifiedLine, 'info', null, 'CURR');
        return;
      } else if (/\[(Barometer|BARO)\]/i.test(line)) {
        const modifiedLine = line.replace(/\[(Barometer|BARO)\]\s*/i, '');
        this.panels['ground']?.addLogEntry(modifiedLine, 'info', null, 'BARO');
        return;
      }
      // Ground/system
      else if (/\[(Status|Ground|Peripherals)\]/i.test(line)) {
        channel = 'ground'; origin = 'STATUS';
        line = line.replace(/\[(Status|Ground|Peripherals)\]\s*/i, '');
      }

      this.panels[channel]?.addLogEntry(line, 'info', null, origin);
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!window.__logsHub) window.__logsHub = new LogsHub();
  });
})();
