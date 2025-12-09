const socket = io(`http://${window.location.host}`); // Initialize socket.io connection
const errorContainer = document.getElementById('error-container');
const confidenceSlider = document.getElementById('confidenceSlider');
const confidenceValue = document.getElementById('confidenceValue');
const labelSelect = document.getElementById('labelSelect');
const DEFAULT_CONFIDENCE = 0.6;

// Navigation elements
const btnLive = document.getElementById('btnLive');
const btnLatest = document.getElementById('btnLatest');
const btnBack = document.getElementById('btnBack');
const btnForward = document.getElementById('btnForward');
const positionIndicator = document.getElementById('positionIndicator');
const detectionInfo = document.getElementById('detectionInfo');
const infoLabel = document.getElementById('infoLabel');
const infoConfidence = document.getElementById('infoConfidence');
const infoTime = document.getElementById('infoTime');
const iframeWrapper = document.getElementById('iframe-wrapper');
const savedImageWrapper = document.getElementById('saved-image-wrapper');
const savedImage = document.getElementById('savedImage');

// History state
let viewMode = 'live'; // 'live' | 'history'
let historyIndex = -1; // Current position in history (0 = oldest, length-1 = newest)
let detectionHistory = []; // Array of detection records from backend

document.addEventListener('DOMContentLoaded', () => {
    initControls();
    initNavigation();
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

function initNavigation() {
    if (btnLive) {
        btnLive.addEventListener('click', () => setLiveMode());
    }
    if (btnLatest) {
        btnLatest.addEventListener('click', () => goToLatest());
    }
    if (btnBack) {
        btnBack.addEventListener('click', () => goBack());
    }
    if (btnForward) {
        btnForward.addEventListener('click', () => goForward());
    }
}

function setLiveMode() {
    viewMode = 'live';
    historyIndex = -1;
    
    // Show iframe, hide saved image
    if (iframeWrapper) iframeWrapper.style.display = 'block';
    if (savedImageWrapper) savedImageWrapper.style.display = 'none';
    
    // Update UI
    updatePositionIndicator();
    updateButtonStates();
    hideDetectionInfo();
}

function goToLatest() {
    if (detectionHistory.length === 0) return;
    
    viewMode = 'history';
    historyIndex = detectionHistory.length - 1;
    
    showHistoryImage();
}

function goBack() {
    if (detectionHistory.length === 0) return;
    
    if (viewMode === 'live') {
        // Switch to history mode at latest
        viewMode = 'history';
        historyIndex = detectionHistory.length - 1;
    } else if (historyIndex > 0) {
        historyIndex--;
    }
    
    showHistoryImage();
}

function goForward() {
    if (viewMode !== 'history' || detectionHistory.length === 0) return;
    
    if (historyIndex < detectionHistory.length - 1) {
        historyIndex++;
        showHistoryImage();
    }
}

function showHistoryImage() {
    if (historyIndex < 0 || historyIndex >= detectionHistory.length) return;
    
    const entry = detectionHistory[historyIndex];
    
    // Hide iframe, show saved image
    if (iframeWrapper) iframeWrapper.style.display = 'none';
    if (savedImageWrapper) savedImageWrapper.style.display = 'block';
    
    // Load the image (served from assets/images/)
    if (savedImage && entry.filename) {
        savedImage.src = `/images/${entry.filename}`;
    }
    
    // Update detection info
    showDetectionInfo(entry);
    updatePositionIndicator();
    updateButtonStates();
}

function showDetectionInfo(entry) {
    if (!detectionInfo) return;
    
    detectionInfo.style.display = 'flex';
    if (infoLabel) infoLabel.textContent = entry.label || '-';
    if (infoConfidence) infoConfidence.textContent = entry.confidence ? `${(entry.confidence * 100).toFixed(1)}%` : '-';
    if (infoTime) infoTime.textContent = entry.time_formatted || '-';
}

function hideDetectionInfo() {
    if (detectionInfo) {
        detectionInfo.style.display = 'none';
    }
}

function updatePositionIndicator() {
    if (!positionIndicator) return;
    
    if (viewMode === 'live') {
        positionIndicator.textContent = 'Live';
    } else {
        positionIndicator.textContent = `${historyIndex + 1} of ${detectionHistory.length}`;
    }
}

function updateButtonStates() {
    // Live button active state
    if (btnLive) {
        btnLive.classList.toggle('active', viewMode === 'live');
    }
    
    // Latest button
    if (btnLatest) {
        btnLatest.disabled = detectionHistory.length === 0;
    }
    
    // Back button - disabled if at oldest or no history
    if (btnBack) {
        btnBack.disabled = detectionHistory.length === 0 || (viewMode === 'history' && historyIndex <= 0);
    }
    
    // Forward button - disabled if in live mode or at newest
    if (btnForward) {
        btnForward.disabled = viewMode === 'live' || detectionHistory.length === 0 || historyIndex >= detectionHistory.length - 1;
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

        console.log('[DEBUG] Emitting request_labels');
        socket.emit('request_labels', null);
        
        // Request detection history
        console.log('[DEBUG] Emitting request_history');
        socket.emit('request_history', null);

        // Request current threshold to sync UI without overwriting backend state
        console.log('[DEBUG] Emitting request_threshold');
        socket.emit('request_threshold', null);
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

    // Label dropdown updates
    socket.on('labels', updateLabelDropdown);
    socket.on('threshold', handleThreshold);
    
    // History list from backend
    socket.on('history_list', handleHistoryList);
    
    // New detection saved
    socket.on('detection_saved', handleDetectionSaved);
}

function handleThreshold(payload) {
    const value = payload?.value ?? payload?.message?.value;
    if (!Number.isFinite(value)) return;
    if (confidenceSlider) {
        confidenceSlider.value = value;
    }
    updateConfidenceDisplay(value);
}

function handleHistoryList(payload) {
    console.log('[DEBUG] history_list received:', payload);
    
    // Support wrapped payload
    const data = payload?.history ? payload : (payload?.message ? payload.message : {});
    
    if (Array.isArray(data?.history)) {
        detectionHistory = data.history;
    }
    
    updateButtonStates();
    updatePositionIndicator();
    console.log(`[DEBUG] Loaded ${detectionHistory.length} history entries`);
}

function handleDetectionSaved(payload) {
    console.log('[DEBUG] detection_saved received:', payload);
    
    // Support wrapped payload
    const data = payload?.entry ? payload : (payload?.message ? payload.message : {});
    
    if (data?.entry) {
        detectionHistory.push(data.entry);
        
        // Determine if the user was viewing the latest item *before* any rotation
        const wasViewingLatest = viewMode === 'history' && historyIndex === detectionHistory.length - 2;
        
        // Enforce max limit on client side too (in case backend rotated)
        let removedCount = 0;
        while (detectionHistory.length > 40) {
            detectionHistory.shift();
            removedCount++;
        }

        // Keep historyIndex aligned if user is browsing history when rotation occurs,
        // but skip adjustment if we are about to auto-advance from the previous-latest.
        if (!wasViewingLatest && removedCount > 0 && viewMode === 'history' && historyIndex >= 0) {
            historyIndex = Math.max(0, historyIndex - removedCount);
            if (historyIndex >= detectionHistory.length) {
                historyIndex = detectionHistory.length - 1;
            }
        }

        if (viewMode === 'live') {
            // Stay in live mode but surface the newest detection info
            showDetectionInfo(data.entry);
        } else if (wasViewingLatest) {
            // If viewing latest in history mode, advance to the newest saved image
            historyIndex = detectionHistory.length - 1;
            showHistoryImage();
        }

        updateButtonStates();
        updatePositionIndicator();
    }
}