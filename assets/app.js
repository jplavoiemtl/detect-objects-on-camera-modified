const socket = io(`http://${window.location.host}`); // Initialize socket.io connection
const errorContainer = document.getElementById('error-container');
const confidenceSlider = document.getElementById('confidenceSlider');
const confidenceValue = document.getElementById('confidenceValue');
const labelSelect = document.getElementById('labelSelect');
const DEFAULT_CONFIDENCE = 0.6;

document.addEventListener('DOMContentLoaded', () => {
    initControls();
    initSocketIO();
});

function initControls() {
    // Set initial displayed value
    updateConfidenceDisplay(confidenceSlider?.value ?? DEFAULT_CONFIDENCE);

    if (confidenceSlider) {
        confidenceSlider.addEventListener('change', () => {
            const value = parseFloat(confidenceSlider.value);
            if (Number.isFinite(value)) {
                updateConfidenceDisplay(value);
                socket.emit('override_th', value);
            }
        });
    }

    if (labelSelect) {
        labelSelect.addEventListener('change', () => {
            const value = labelSelect.value;
            if (value) {
                socket.emit('override_label', value);
            }
        });
    }
}

function updateConfidenceDisplay(value) {
    if (confidenceValue) {
        confidenceValue.textContent = Number(value).toFixed(2);
    }
}

function updateLabelDropdown(payload) {
    console.log('[DEBUG] labels event received:', JSON.stringify(payload));
    if (!labelSelect) return;

    // Support both direct payload and wrapped { message: {...} } payloads
    const data = payload?.labels ? payload : (payload?.message ? payload.message : {});
    console.log('[DEBUG] parsed data:', JSON.stringify(data));

    const labels = Array.isArray(data?.labels) ? data.labels : [];
    const selected = typeof data?.selected === 'string' ? data.selected : '';
    console.log('[DEBUG] labels:', labels, 'selected:', selected);

    labelSelect.innerHTML = '';

    if (!labels.length) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'No labels yet';
        labelSelect.appendChild(option);
        labelSelect.disabled = true;
        return;
    }

    labels.forEach((label) => {
        const option = document.createElement('option');
        option.value = label;
        option.textContent = label;
        labelSelect.appendChild(option);
    });

    if (selected) {
        labelSelect.value = selected;
    }

    labelSelect.disabled = false;
}

function initSocketIO() {
    socket.on('connect', () => {
        console.log('[DEBUG] Socket connected');
        if (errorContainer) {
            errorContainer.style.display = 'none';
            errorContainer.textContent = '';
        }

        // Re-send current slider value on reconnect to keep backend in sync
        const value = confidenceSlider ? parseFloat(confidenceSlider.value) : DEFAULT_CONFIDENCE;
        if (Number.isFinite(value)) {
            socket.emit('override_th', value);
        }

        console.log('[DEBUG] Emitting request_labels');
        socket.emit('request_labels', null);
    });

    socket.on('disconnect', () => {
        if (errorContainer) {
            errorContainer.textContent = 'Connection to the board lost. Please check the connection.';
            errorContainer.style.display = 'block';
        }

        if (labelSelect) {
            labelSelect.disabled = true;
        }
    });

    // Preserve only overlay/canvas-related socket logic
    // socket.on('detection', ...) — Only keep if it calls overlay/canvas code
    socket.on('labels', updateLabelDropdown);
}
