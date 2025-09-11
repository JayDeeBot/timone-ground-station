class GraphManager {
    constructor() {
        this.charts = {};
        this.draggedElement = null;
        this.resizedElement = null;
        this.initialSize = { width: 0, height: 0 };
        this.initialPos = { x: 0, y: 0 };
    }

    initializeGraph(graphElement, graphId) {
        this.initializeDragAndDrop(graphElement);
        this.initializeResize(graphElement);
        this.addGraphEventListeners(graphElement, graphId);
    }

    initializeDragAndDrop(element) {
        element.setAttribute('draggable', 'true');
        
        element.addEventListener('dragstart', (e) => {
            this.draggedElement = element;
            element.classList.add('dragging');
            e.dataTransfer.setData('text/plain', ''); // Required for Firefox
        });

        element.addEventListener('dragend', () => {
            element.classList.remove('dragging');
            document.querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target'));
        });

        element.addEventListener('dragover', (e) => {
            e.preventDefault();
            if (this.draggedElement !== element) {
                element.classList.add('drop-target');
            }
        });

        element.addEventListener('dragleave', () => {
            element.classList.remove('drop-target');
        });

        element.addEventListener('drop', (e) => {
            e.preventDefault();
            if (this.draggedElement && this.draggedElement !== element) {
                const container = document.getElementById('graphContainer');
                const rect = element.getBoundingClientRect();
                const draggedRect = this.draggedElement.getBoundingClientRect();
                
                if (e.clientY < rect.top + rect.height / 2) {
                    container.insertBefore(this.draggedElement, element);
                } else {
                    container.insertBefore(this.draggedElement, element.nextSibling);
                }
                
                // Update charts after moving
                Object.values(this.charts).forEach(chart => chart.update());
            }
            element.classList.remove('drop-target');
        });
    }

    initializeResize(element) {
        const resizeHandle = document.createElement('div');
        resizeHandle.className = 'graph-resize-handle';
        element.querySelector('.card-body').appendChild(resizeHandle);

        resizeHandle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            this.resizedElement = element;
            this.initialSize = {
                width: element.offsetWidth,
                height: element.offsetHeight
            };
            this.initialPos = {
                x: e.clientX,
                y: e.clientY
            };
            
            document.addEventListener('mousemove', this.handleResize);
            document.addEventListener('mouseup', this.stopResize);
        });
    }

    handleResize = (e) => {
        if (!this.resizedElement) return;
        
        const deltaX = e.clientX - this.initialPos.x;
        const deltaY = e.clientY - this.initialPos.y;
        
        const newWidth = this.initialSize.width + deltaX;
        const newHeight = this.initialSize.height + deltaY;
        
        this.resizedElement.style.width = `${newWidth}px`;
        this.resizedElement.style.height = `${newHeight}px`;
        
        // Update chart
        const chartId = this.resizedElement.querySelector('canvas').id;
        if (this.charts[chartId]) {
            this.charts[chartId].resize();
        }
    }

    stopResize = () => {
        this.resizedElement = null;
        document.removeEventListener('mousemove', this.handleResize);
        document.removeEventListener('mouseup', this.stopResize);
    }

    addGraphEventListeners(graphElement, graphId) {
        // Remove graph
        graphElement.querySelector('.remove-graph')?.addEventListener('click', () => {
            if (this.charts[graphId]) {
                this.charts[graphId].destroy();
                delete this.charts[graphId];
            }
            graphElement.remove();
        });
    }
}

// Initialize graph manager
const graphManager = new GraphManager();