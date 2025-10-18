(() => {
  const MAX_ENTRIES = 5000; // storage safety cap

  // Map channel => { storageKey, title }
  const CHANNELS = {
    ground:  { key: 'timone_log_ground_v1',  title: 'Ground Station (gui_status.py)' },
    lora:    { key: 'timone_log_lora_v1',    title: 'LoRa Radio (915 & 433)' },
    periph1: { key: 'timone_log_p1_v1',      title: 'Peripheral 1 (gui_peripherals.py)' },
    periph2: { key: 'timone_log_p2_v1',      title: 'Peripheral 2 (gui_peripherals.py)' },
    periph3: { key: 'timone_log_p3_v1',      title: 'Peripheral 3 (gui_peripherals.py)' },
    periph4: { key: 'timone_log_p4_v1',      title: 'Peripheral 4 (gui_peripherals.py)' }
  };

  class PanelLog {
    /**
     * @param {string} channel - one of CHANNELS keys
     * @param {HTMLElement} rootCardEl - the card element for this panel
     */
    constructor(channel, rootCardEl) {
      this.channel = channel;
      this.cfg = CHANNELS[channel];
      this.root = rootCardEl;

      // DOM hooks
      this.exportBtn   = this.root.querySelector(`[data-export="${channel}"]`);
      this.scrollWrap  = this.root.querySelector(`#logContent-${channel}`);
      this.logContent  = this.root.querySelector(`.log-content[data-channel="${channel}"]`);

      // State
      this.autoScroll = true;

      // Restore persisted entries
      this._restore();

      // Wire export
      if (this.exportBtn) {
        this.exportBtn.addEventListener('click', () => this.export());
      }
    }

    // Public: add one entry
    addLogEntry(message, level = 'info', isoTimestamp = null, origin = null) {
      if (!this.logContent) return;
      const ts = isoTimestamp || new Date().toISOString();

      // If an origin is provided, prefix the message e.g. "[915] link up"
      const displayMsg = origin ? `[${origin}] ${message}` : message;

      // UI row (no angle-bracket stamp here; clean UI)
      const row = document.createElement('div');
      row.className = 'log-entry';
      row.innerHTML = `
        <span class="log-timestamp">${ts}</span>
        <span class="log-level ${level}">${String(level).toUpperCase()}</span>
        <span class="log-message">${escapeHtml(displayMsg)}</span>
      `;
      this.logContent.appendChild(row);

      // Persist compact form
      this._persist({ ts, level, message: displayMsg });

      // Scroll pin
      if (this.autoScroll) this._scrollToBottom();
    }

    clear() {
      if (this.logContent) this.logContent.innerHTML = '';
      localStorage.removeItem(this.cfg.key);
    }

    // Public: export with the approved format
    async export() {
      const entries = this._readAll();
      const nowIso = new Date().toISOString();
      const suggestedName = `${this.channel}_logs_${nowIso.replace(/[:.]/g, '-')}.txt`;

      // Single top "stamp" only
      let stampTs = nowIso;
      let stampLevel = 'INFO';
      let stampMsg = `[export] ${this.channel}-logs`;
      if (entries.length > 0) {
        stampTs = entries[0].ts || stampTs;
        stampLevel = (entries[0].level || 'info').toUpperCase();
        stampMsg = `[${entries[0].message}]`;
      }

      // Messages with no per-line stamps
      const body = entries.map(e => e.message).join('\n');
      const content = `<${stampTs}>\n<${stampLevel}>\n<${stampMsg}>\n\n${body}`;

      try {
        const fileHandle = await window.showSaveFilePicker({
          suggestedName,
          types: [{ description: 'Text Files', accept: { 'text/plain': ['.txt'] } }]
        });
        const writable = await fileHandle.createWritable();
        await writable.write(content);
        await writable.close();
        // Also add an in-panel info line
        this.addLogEntry('Logs exported successfully', 'info');
      } catch (err) {
        if (err && err.name !== 'AbortError') {
          console.error(`Export failed (${this.channel}):`, err);
          this.addLogEntry(`Failed to export logs: ${err.message || err}`, 'error');
        }
      }
    }

    // ---- internals ----
    _scrollToBottom() {
      if (!this.scrollWrap) return;
      requestAnimationFrame(() => {
        this.scrollWrap.scrollTop = this.scrollWrap.scrollHeight;
      });
    }

    _persist(entry) {
      const all = this._readAll();
      all.push(entry);
      if (all.length > MAX_ENTRIES) all.splice(0, all.length - MAX_ENTRIES);
      localStorage.setItem(this.cfg.key, JSON.stringify(all));
    }

    _readAll() {
      try {
        const raw = localStorage.getItem(this.cfg.key);
        const arr = raw ? JSON.parse(raw) : [];
        if (!Array.isArray(arr)) return [];
        return arr
          .filter(e => e && typeof e === 'object')
          .map(e => ({
            ts: e.ts || new Date().toISOString(),
            level: e.level || 'info',
            message: String(e.message ?? '')
          }));
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
        row.innerHTML = `
          <span class="log-timestamp">${e.ts}</span>
          <span class="log-level ${e.level}">${String(e.level).toUpperCase()}</span>
          <span class="log-message">${escapeHtml(e.message)}</span>
        `;
        this.logContent.appendChild(row);
      }
      if (entries.length) this._scrollToBottom();
    }
  }

  class LogsHub {
    constructor() {
      /** @type {Record<string, PanelLog>} */
      this.panels = {};
      this._initPanels();

      // Expose multi-panel API
      window.logs = {
        /**
         * Add a log line to any panel.
         * @param {'ground'|'lora'|'periph1'|'periph2'|'periph3'|'periph4'} channel
         * @param {string} message
         * @param {'info'|'warn'|'warning'|'error'|'debug'} [level]
         * @param {string|null} isoTimestamp
         * @param {string|null} origin - optional origin label (e.g. "915", "433", "GUI")
         */
        add: (channel, message, level = 'info', isoTimestamp = null, origin = null) => {
          const panel = this.panels[channel];
          if (!panel) return;
          panel.addLogEntry(message, level, isoTimestamp, origin);
        },
        clear: (channel) => {
          const panel = this.panels[channel];
          if (!panel) return;
          panel.clear();
        },
        export: (channel) => {
          const panel = this.panels[channel];
          if (!panel) return;
          panel.export();
        }
      };

      // Backward compatibility with previous single-panel usage:
      // window.logManager.addLogEntry(msg, level, ts) -> writes to "ground"
      window.logManager = {
        addLogEntry: (message, level = 'info', isoTimestamp = null) => {
          this.panels.ground?.addLogEntry(message, level, isoTimestamp, null);
        },
        clearLog: () => this.panels.ground?.clear(),
        exportLogs: () => this.panels.ground?.export()
      };
    }

    _initPanels() {
      for (const chan of Object.keys(CHANNELS)) {
        const root = document.querySelector(`.log-card[data-channel="${chan}"]`);
        if (!root) continue;
        this.panels[chan] = new PanelLog(chan, root);
      }
    }
  }

  // Minimal HTML escaping for safe text injection
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  // Instantiate once DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    if (!window.__logsHub) window.__logsHub = new LogsHub();
  });
})();
