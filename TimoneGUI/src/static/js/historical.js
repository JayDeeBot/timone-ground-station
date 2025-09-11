class FileExplorer {
    constructor() {
        this.currentPath = '/';
        console.log('FileExplorer initialized');
        this.init();
    }

    init() {
        console.log('Initializing FileExplorer');
        this.loadDirectory();
    }

    async loadDirectory() {
        try {
            console.log('Loading directory:', this.currentPath);
            const response = await fetch(`/api/files/list?path=${encodeURIComponent(this.currentPath)}`);
            const files = await response.json();
            console.log('Directory data:', files);
            this.displayFiles(files);
            this.updatePath();
        } catch (error) {
            console.error('Error loading directory:', error);
        }
    }

    displayFiles(files) {
        const fileList = document.getElementById('fileList');
        if (!fileList) return;

        fileList.innerHTML = '';

        // Add parent directory option if not at root
        if (this.currentPath !== '/') {
            const backItem = document.createElement('a');
            backItem.className = 'list-group-item list-group-item-action';
            backItem.innerHTML = '<i class="bi bi-arrow-up"></i> ..';
            backItem.addEventListener('click', () => {
                this.currentPath = this.getParentPath();
                this.loadDirectory();
            });
            fileList.appendChild(backItem);
        }

        // Sort directories first, then files
        files.sort((a, b) => {
            if (a.type === b.type) return a.name.localeCompare(b.name);
            return a.type === 'directory' ? -1 : 1;
        });

        files.forEach(file => {
            const item = document.createElement('a');
            item.className = 'list-group-item list-group-item-action';
            item.dataset.path = file.path;
            item.dataset.type = file.type;
            item.innerHTML = `
                <i class="bi ${file.type === 'directory' ? 'bi-folder' : 'bi-file-text'}"></i>
                ${file.name}
            `;
            
            item.addEventListener('click', () => this.handleItemClick(file));
            fileList.appendChild(item);
        });
    }

    async handleItemClick(file) {
        if (file.type === 'directory') {
            this.currentPath = file.path;
            await this.loadDirectory();
        } else {
            await this.viewFile(file.path);
        }
    }

    async viewFile(path) {
        try {
            const response = await fetch(`/api/files/view?path=${encodeURIComponent(path)}`);
            const content = await response.text();
            
            // Create and show modal
            const modal = document.createElement('div');
            modal.className = 'modal fade';
            modal.innerHTML = `
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">${path.split('/').pop()}</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <pre class="p-3 bg-light">${content}</pre>
                        </div>
                    </div>
                </div>
            `;
            
            document.body.appendChild(modal);
            const bsModal = new bootstrap.Modal(modal);
            bsModal.show();
            
            modal.addEventListener('hidden.bs.modal', () => {
                document.body.removeChild(modal);
            });
        } catch (error) {
            console.error('Error viewing file:', error);
        }
    }

    closeFile() {
        document.getElementById('fileList').classList.remove('d-none');
        document.getElementById('fileViewer').classList.add('d-none');
        document.getElementById('fileContent').textContent = '';
    }

    updatePath() {
        const pathElement = document.getElementById('currentPath');
        if (pathElement) {
            pathElement.textContent = this.currentPath || '/';
        }
    }

    getParentPath() {
        const parts = this.currentPath.split('/').filter(Boolean);
        return parts.length > 0 ? '/' + parts.slice(0, -1).join('/') : '/';
    }
}

// Single initialization for the historical tab
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded');
    const historicalTab = document.getElementById('historical-tab');
    if (historicalTab) {
        historicalTab.addEventListener('shown.bs.tab', () => {
            console.log('Historical tab shown');
            new FileExplorer();
        });
    }
});
