class LogManager {
    constructor() {
        // Get references to DOM elements
        this.logContainer = document.querySelector('.log-content');
        this.exportButton = document.getElementById('exportLogs');
        
        // Add click handler for export button
        if (this.exportButton) {
            console.log('Export button found, adding handler');
            this.exportButton.addEventListener('click', () => {
                console.log('Export button clicked');
                this.exportLogs();
            });
        } else {
            console.error('Export button not found');
        }
    }

    async exportLogs() {
        // Create a file even if no logs exist
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        const suggestedName = `system_logs_${timestamp}.txt`;

        try {
            // Create content
            let content = `System Logs Export\n`;
            content += `Generated: ${new Date().toLocaleString()}\n`;
            content += `----------------------------------------\n\n`;
            
            if (this.logContainer && this.logContainer.children.length > 0) {
                content += Array.from(this.logContainer.children)
                    .map(entry => entry.textContent)
                    .join('\n');
            } else {
                content += 'No logs recorded.';
            }

            // Show file save dialog
            const fileHandle = await window.showSaveFilePicker({
                suggestedName: suggestedName,
                types: [{
                    description: 'Text Files',
                    accept: {'text/plain': ['.txt']},
                }],
            });

            // Write the file
            const writable = await fileHandle.createWritable();
            await writable.write(content);
            await writable.close();

        } catch (err) {
            // Silently handle abort errors when user cancels file save
            if (err.name !== 'AbortError') {
                console.error('Error exporting logs:', err);
            }
        }
    }

    addLogEntry(message, level = 'info') {
        if (!this.logContainer) return;

        const entry = document.createElement('div');
        entry.className = 'log-entry';
        
        const timestamp = new Date().toISOString();
        entry.innerHTML = `
            <span class="log-timestamp">${timestamp}</span>
            <span class="log-level ${level}">${level.toUpperCase()}</span>
            <span class="log-message">${message}</span>
        `;
        
        this.logContainer.appendChild(entry);

        // Auto-scroll if enabled
        if (this.autoScroll) {
            this.scrollToBottom();
        }
    }

    scrollToBottom() {
        const container = document.getElementById('logContainer');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    console.log('Initializing LogManager');
    window.logManager = new LogManager();
});

document.addEventListener('shown.bs.tab', function (event) {
    if (event.target.id === 'logs-tab') {
        const exportBtn = document.getElementById('exportLogs');
        if (exportBtn) {
            exportBtn.onclick = async function() {
                const logContent = document.querySelector('.log-content');
                const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
                const suggestedName = `system_logs_${timestamp}.txt`;

                try {
                    // Create content
                    let content = `System Logs Export\n`;
                    content += `Generated: ${new Date().toLocaleString()}\n`;
                    content += `----------------------------------------\n\n`;
                    
                    if (logContent && logContent.children.length > 0) {
                        content += Array.from(logContent.children)
                            .map(log => log.textContent)
                            .join('\n');
                    } else {
                        content += 'No logs recorded.';
                    }

                    // Show file save dialog
                    const fileHandle = await window.showSaveFilePicker({
                        suggestedName: suggestedName,
                        types: [{
                            description: 'Text Files',
                            accept: {'text/plain': ['.txt']},
                        }],
                    });

                    // Create a FileSystemWritableFileStream to write to
                    const writable = await fileHandle.createWritable();
                    
                    // Write the contents
                    await writable.write(content);
                    await writable.close();

                    // Add success message to logs
                    if (window.logManager) {
                        window.logManager.addLogEntry('Logs exported successfully', 'info');
                    }

                } catch (err) {
                    console.error('Failed to save file:', err);
                    if (window.logManager) {
                        window.logManager.addLogEntry('Failed to export logs: ' + err.message, 'error');
                    }
                }
            };
        }
    }
});