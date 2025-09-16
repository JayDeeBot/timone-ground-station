class LogManager {
  constructor() {
    console.log('[LogManager] init');
    // Refs (may be null initially if Logs tab not in DOM yet)
    this.logContainer = document.querySelector('.log-content');
    this.scrollContainer = document.getElementById('logContent');
    this.exportButton = document.getElementById('exportLogs');

    // State
    this.isSaving = false;
    this.autoScroll = true;
    this.es = null;
    this.buffer = []; // hold lines until container exists

    // Export button wiring
    if (this.exportButton) {
      this.exportButton.addEventListener('click', () => this.exportLogs());
    }

    // Observe DOM so if the Logs panel is injected later, we rebind refs & flush
    this.domObserver = new MutationObserver(() => this.refreshRefs());
    this.domObserver.observe(document.documentElement, { childList: true, subtree: true });

    // Kick off SSE regardless of container presence
    if (typeof window.EventSource !== 'undefined') {
      this.connectStream();
      window.addEventListener('beforeunload', () => this.stopStream());
    } else {
      this.consoleAndLog('[log-stream] EventSource not supported in this browser', 'error');
    }

    // Try initial ref refresh (helps single-page/tab layouts)
    this.refreshRefs();
  }

  refreshRefs() {
    const prevHadContainer = !!this.logContainer;
    if (!this.logContainer) {
      this.logContainer = document.querySelector('.log-content');
    }
    if (!this.scrollContainer) {
      this.scrollContainer = document.getElementById('logContent');
    }

    // If the container just showed up, flush any buffered lines
    if (!prevHadContainer && this.logContainer) {
      console.log('[LogManager] logs container detected; flushing buffer of', this.buffer.length, 'lines');
      if (this.buffer.length) {
        for (const { msg, level } of this.buffer) {
          this._append(msg, level);
        }
        this.buffer = [];
        this.scrollToBottom();
      }
    }
  }

  connectStream() {
    try {
      console.log('[LogManager] connecting SSE → /api/logs/stream');
      this.es = new EventSource('/api/logs/stream');

      this.es.onopen = () => {
        this.consoleAndLog('[log-stream] connected', 'info');
      };

      this.es.onmessage = (ev) => {
        if (typeof ev.data === 'string' && ev.data.length) {
          this.addLogEntry(ev.data, 'info');
        }
      };

      this.es.onerror = () => {
        this.consoleAndLog('[log-stream] disconnected; retrying...', 'warn');
        try { this.es.close(); } catch (_) {}
        setTimeout(() => this.connectStream(), 2000);
      };
    } catch (e) {
      console.error(e);
      this.consoleAndLog(`[log-stream] failed to connect: ${e.message}`, 'error');
    }
  }

  stopStream() {
    if (this.es) {
      try { this.es.close(); } catch (_) {}
      this.es = null;
    }
  }

  // unified console + UI log
  consoleAndLog(message, level = 'info') {
    (level === 'error' ? console.error : level === 'warn' ? console.warn : console.log)(message);
    this.addLogEntry(message, level);
  }

  addLogEntry(message, level = 'info') {
    // If container not ready yet, buffer it
    if (!this.logContainer) {
      this.buffer.push({ msg: message, level });
      return;
    }
    this._append(message, level);
    if (this.autoScroll) this.scrollToBottom();
  }

  _append(message, level) {
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    const timestamp = new Date().toISOString();
    entry.innerHTML = `
      <span class="log-timestamp">${timestamp}</span>
      <span class="log-level ${level}">${level.toUpperCase()}</span>
      <span class="log-message">${message}</span>
    `;
    this.logContainer.appendChild(entry);
  }

  scrollToBottom() {
    const container = this.scrollContainer || document.getElementById('logContent');
    if (container) container.scrollTop = container.scrollHeight;
  }

  // existing export feature retained
  async exportLogs() {
    if (this.isSaving) return;
    this.isSaving = true;

    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const suggestedName = `system_logs_${timestamp}.txt`;

    try {
      let content = `System Logs Export\n`;
      content += `Generated: ${new Date().toLocaleString()}\n`;
      content += `----------------------------------------\n\n`;

      if (this.logContainer && this.logContainer.children.length > 0) {
        content += Array.from(this.logContainer.children).map(e => e.textContent).join('\n');
      } else if (this.buffer.length) {
        content += this.buffer.map(b => `[buffered] ${b.msg}`).join('\n');
      } else {
        content += 'No logs recorded.';
      }

      if (window.showSaveFilePicker) {
        const fileHandle = await window.showSaveFilePicker({
          suggestedName,
          types: [{ description: 'Text Files', accept: { 'text/plain': ['.txt'] } }],
        });
        const writable = await fileHandle.createWritable();
        await writable.write(content);
        await writable.close();
      } else {
        const blob = new Blob([content], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = suggestedName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }

      this.addLogEntry('Logs exported successfully', 'info');
    } catch (err) {
      if (err?.name !== 'AbortError') {
        console.error('Error exporting logs:', err);
        this.addLogEntry('Failed to export logs: ' + err.message, 'error');
      }
    } finally {
      this.isSaving = false;
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.logManager = new LogManager();
});
