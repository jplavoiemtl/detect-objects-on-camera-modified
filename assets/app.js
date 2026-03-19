const socket = io(`http://${window.location.host}`); // Initialize socket.io connection
const errorContainer = document.getElementById('error-container');
const confidenceSlider = document.getElementById('confidenceSlider');
const confidenceValue = document.getElementById('confidenceValue');
const labelSelect = document.getElementById('labelSelect');
const DEFAULT_CONFIDENCE = 0.6;

// Stream health elements
const streamHealth = document.getElementById('stream-health');
const healthDot = document.getElementById('healthDot');
const healthText = document.getElementById('healthText');

// Navigation elements
const btnLive = document.getElementById('btnLive');
const btnLatest = document.getElementById('btnLatest');
const btnOldest = document.getElementById('btnOldest');
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
const liveDateTime = document.getElementById('liveDateTime');

// New interaction elements
const btnFullscreen = document.getElementById('btnFullscreen');
const btnWakeLock = document.getElementById('btnWakeLock');
const btnDownload = document.getElementById('btnDownload');
const btnLiveSnapshot = document.getElementById('btnLiveSnapshot');
const btnDownloadVideo = document.getElementById('btnDownloadVideo');
const imageActions = document.getElementById('imageActions');
const toastContainer = document.getElementById('toast-container');
const videoFeedContainer = document.getElementById('videoFeedContainer');
const detectionVideo = document.getElementById('detectionVideo');
const btnPlayVideo = document.getElementById('btnPlayVideo');

// History state
let viewMode = 'live'; // 'live' | 'history'
let historyIndex = -1; // Current position in history (0 = oldest, length-1 = newest)
let detectionHistory = []; // Array of detection records from backend

document.addEventListener('DOMContentLoaded', () => {
    initControls();
    initNavigation();
    initSocketIO();
    initLiveDateTime();
});

function initControls() {
    // Set initial displayed value
    updateConfidenceDisplay(confidenceSlider?.value ?? DEFAULT_CONFIDENCE);

    if (confidenceSlider) {
        confidenceSlider.addEventListener('input', () => {
            const value = parseFloat(confidenceSlider.value);
            if (Number.isFinite(value)) {
                updateConfidenceDisplay(value);
            }
        });

        confidenceSlider.addEventListener('change', () => {
            const value = parseFloat(confidenceSlider.value);
            if (Number.isFinite(value)) {
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
    if (btnOldest) {
        btnOldest.addEventListener('click', () => goToOldest());
    }
    if (btnBack) {
        btnBack.addEventListener('click', () => goBack());
    }
    if (btnForward) {
        btnForward.addEventListener('click', () => goForward());
    }
    
    if (btnFullscreen) {
        btnFullscreen.addEventListener('click', toggleFullscreen);
    }
    if (btnWakeLock) {
        btnWakeLock.addEventListener('click', toggleWakeLock);
    }
    if (btnDownload) {
        btnDownload.addEventListener('click', downloadCurrentDetection);
    }
    if (btnLiveSnapshot) {
        btnLiveSnapshot.addEventListener('click', requestLiveSnapshot);
    }
    if (btnDownloadVideo) {
        btnDownloadVideo.addEventListener('click', downloadCurrentVideo);
    }
    if (btnPlayVideo) {
        btnPlayVideo.addEventListener('click', playDetectionVideo);
    }
    if (detectionVideo) {
        detectionVideo.addEventListener('ended', stopDetectionVideo);
        detectionVideo.addEventListener('error', () => {
            showToast('Video not available');
            stopDetectionVideo();
        });
    }
}

function setLiveMode() {
    viewMode = 'live';
    historyIndex = -1;

    // Stop any playing video
    stopDetectionVideo();

    // Show iframe, hide saved image
    if (iframeWrapper) iframeWrapper.style.display = 'block';
    if (savedImageWrapper) savedImageWrapper.style.display = 'none';
    if (btnLiveSnapshot) btnLiveSnapshot.style.display = 'flex';
    if (imageActions) imageActions.style.display = 'none';
    if (btnPlayVideo) btnPlayVideo.style.display = 'none';

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

function goToOldest() {
    if (detectionHistory.length === 0) return;

    viewMode = 'history';
    historyIndex = 0;

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

    // Stop any playing video
    stopDetectionVideo();

    // Hide iframe, show saved image
    if (iframeWrapper) iframeWrapper.style.display = 'none';
    if (savedImageWrapper) savedImageWrapper.style.display = 'block';
    if (btnLiveSnapshot) btnLiveSnapshot.style.display = 'none';
    if (imageActions) imageActions.style.display = 'flex';

    // Show play/download video buttons if this detection has a video
    const hasVideo = !!entry.video_filename;
    if (btnPlayVideo) btnPlayVideo.style.display = hasVideo ? 'flex' : 'none';
    if (btnDownloadVideo) btnDownloadVideo.style.display = hasVideo ? 'flex' : 'none';

    // Load the image (served from assets/images/)
    if (savedImage && entry.filename) {
        savedImage.src = `/images/${entry.filename}`;
    }

    // Update detection info
    showDetectionInfo(entry);
    updatePositionIndicator();
    updateButtonStates();
}

function playDetectionVideo() {
    if (historyIndex < 0 || historyIndex >= detectionHistory.length) return;
    const entry = detectionHistory[historyIndex];
    if (!entry.video_filename || !detectionVideo) return;

    // Build source paths: WebM for Chrome/Firefox, MP4 for Safari/iOS
    const webmFile = entry.video_filename.replace('.mp4', '.webm');
    const mp4File = entry.video_filename;

    // Hide image, show video
    if (savedImageWrapper) savedImageWrapper.style.display = 'none';
    if (btnPlayVideo) btnPlayVideo.style.display = 'none';
    if (imageActions) imageActions.style.display = 'none';

    // Use <source> elements for dual-format support
    detectionVideo.innerHTML = '';
    const webmSource = document.createElement('source');
    webmSource.src = `/videos/${webmFile}`;
    webmSource.type = 'video/webm';
    const mp4Source = document.createElement('source');
    mp4Source.src = `/videos/${mp4File}`;
    mp4Source.type = 'video/mp4';
    detectionVideo.appendChild(webmSource);
    detectionVideo.appendChild(mp4Source);

    detectionVideo.style.display = 'block';
    detectionVideo.load();
    detectionVideo.play().catch(() => {
        showToast('Video not available');
        stopDetectionVideo();
    });
}

function stopDetectionVideo() {
    if (!detectionVideo) return;
    detectionVideo.pause();
    detectionVideo.innerHTML = '';
    detectionVideo.removeAttribute('src');
    detectionVideo.load();
    detectionVideo.style.display = 'none';

    // Restore image view if browsing history
    if (viewMode === 'history' && historyIndex >= 0 && historyIndex < detectionHistory.length) {
        if (savedImageWrapper) savedImageWrapper.style.display = 'block';
        if (imageActions) imageActions.style.display = 'flex';
        const entry = detectionHistory[historyIndex];
        if (btnPlayVideo) btnPlayVideo.style.display = entry.video_filename ? 'flex' : 'none';
    }
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

    // Latest button - disabled if no history or already at latest
    if (btnLatest) {
        btnLatest.disabled = detectionHistory.length === 0 || (viewMode === 'history' && historyIndex >= detectionHistory.length - 1);
    }

    // Oldest button - disabled if no history or already at oldest
    if (btnOldest) {
        btnOldest.disabled = detectionHistory.length === 0 || (viewMode === 'history' && historyIndex <= 0);
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

    // Stream health updates
    socket.on('stream_health', handleStreamHealth);

    // Live snapshot response
    socket.on('snapshot', handleSnapshot);
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

        // Enforce max limit on client side too (in case backend rotated)
        let removedCount = 0;
        while (detectionHistory.length > 40) {
            detectionHistory.shift();
            removedCount++;
        }

        // Keep historyIndex aligned if user is browsing history when rotation occurs,
        // but skip adjustment if we are about to auto-advance from the previous-latest.
        if (removedCount > 0 && viewMode === 'history' && historyIndex >= 0) {
            historyIndex = Math.max(0, historyIndex - removedCount);
            if (historyIndex >= detectionHistory.length) {
                historyIndex = detectionHistory.length - 1;
            }
        }

        if (viewMode === 'live') {
            // Stay in live mode but surface the newest detection info
            showDetectionInfo(data.entry);
        } else {
            // Toast notification if looking at older history
            if (historyIndex < detectionHistory.length - 1) {
                const confPercent = data.entry.confidence ? Math.round(data.entry.confidence * 100) : '--';
                showToast(`New detection: ${data.entry.label} (${confPercent}%)`);
            }
            // ALWAYS jump to the newest detection if we are in history mode
            historyIndex = detectionHistory.length - 1;
            showHistoryImage();
        }

        updateButtonStates();
        updatePositionIndicator();
    }
}

function initLiveDateTime() {
    if (!liveDateTime) return;

    const update = () => {
        liveDateTime.textContent = formatDateTimeForDisplay(new Date());
    };

    // Initial paint
    update();

    // Align to the next minute boundary, then update every minute
    const now = new Date();
    const msToNextMinute = Math.max(0, (60 - now.getSeconds()) * 1000 - now.getMilliseconds());

    setTimeout(() => {
        update();
        setInterval(update, 60 * 1000);
    }, msToNextMinute);
}

function handleStreamHealth(payload) {
    // Support wrapped payload
    const data = payload?.connected !== undefined ? payload : (payload?.message || {});
    if (data.connected === undefined) return;

    const container = document.getElementById('stream-health');
    if (container) container.style.display = 'flex';

    // Determine health status
    let status = 'good';
    let label = `${data.fps} fps`;

    if (!data.connected) {
        status = 'bad';
        label = 'disconnected';
    } else if (data.disconnects > 0) {
        status = 'bad';
        label = `${data.fps} fps / ${data.disconnects} drop${data.disconnects > 1 ? 's' : ''}`;
    } else if (data.max_gap > 2.0) {
        status = 'degraded';
        label = `${data.fps} fps / gap ${data.max_gap}s`;
    } else if (data.fps > 0 && data.fps < 5) {
        status = 'degraded';
        label = `${data.fps} fps (low)`;
    }

    if (healthDot) {
        healthDot.className = 'health-dot ' + status;
    }
    if (healthText) {
        healthText.textContent = label;
    }

    // Log significant events to console for diagnostics
    if (status !== 'good') {
        console.log('[STREAM]', JSON.stringify(data));
    }
}

function formatDateTimeForDisplay(date) {
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const day = String(date.getDate());
    const month = months[date.getMonth()];
    const year = date.getFullYear();
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');

    return `${day} ${month} ${year}, ${hours}:${minutes}`;
}

// --- DSR: Step 3 Interactive Features ---
function toggleFullscreen() {
    if (!document.fullscreenElement) {
        if (videoFeedContainer.requestFullscreen) {
            videoFeedContainer.requestFullscreen();
        } else if (videoFeedContainer.webkitRequestFullscreen) {
            videoFeedContainer.webkitRequestFullscreen();
        }
    } else {
        if (document.exitFullscreen) {
            document.exitFullscreen();
        } else if (document.webkitExitFullscreen) {
            document.webkitExitFullscreen();
        }
    }
}

// --- Screen Awake (NoSleep.js v0.12.0) ---
// navigator.wakeLock is masked in index.html on HTTP so NoSleep.js uses its
// video fallback (the native API exists on iOS but silently fails over HTTP).
const _noSleep = new NoSleep();
let wakeLockEnabled = false;

function toggleWakeLock() {
    if (wakeLockEnabled) {
        _noSleep.disable();
        wakeLockEnabled = false;
        updateWakeLockButton();
        showToast('Screen Awake off');
    } else {
        _noSleep.enable().then(() => {
            wakeLockEnabled = true;
            updateWakeLockButton();
            showToast('Screen Awake on');
        }).catch(() => {
            showToast('Screen Awake failed — tap again');
        });
    }
}

function updateWakeLockButton() {
    if (!btnWakeLock) return;
    if (wakeLockEnabled) {
        btnWakeLock.classList.add('active');
        btnWakeLock.title = 'Screen Awake is on';
        // Open eye icon
        document.getElementById('wakeLockIcon').innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
    } else {
        btnWakeLock.classList.remove('active');
        btnWakeLock.title = 'Keep screen awake';
        // Closed eye icon (eye-off)
        document.getElementById('wakeLockIcon').innerHTML = '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/>';
    }
}

function requestLiveSnapshot() {
    if (!socket || !socket.connected) {
        showToast('Not connected');
        return;
    }
    btnLiveSnapshot.disabled = true;
    socket.emit('request_snapshot', {});
}

function handleSnapshot(data) {
    if (btnLiveSnapshot) btnLiveSnapshot.disabled = false;
    if (data.error) {
        showToast('No frame available');
        return;
    }
    const byteString = atob(data.jpeg);
    const ab = new ArrayBuffer(byteString.length);
    const ia = new Uint8Array(ab);
    for (let i = 0; i < byteString.length; i++) ia[i] = byteString.charCodeAt(i);
    const blob = new Blob([ab], { type: 'image/jpeg' });
    const filename = 'snapshot_' + new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19) + '.jpg';

    const file = new File([blob], filename, { type: 'image/jpeg' });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
        navigator.share({ files: [file] }).catch(() => {});
    } else {
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        setTimeout(() => { document.body.removeChild(link); URL.revokeObjectURL(url); }, 100);
    }
}

async function downloadCurrentDetection() {
    if (historyIndex < 0 || historyIndex >= detectionHistory.length) return;
    const entry = detectionHistory[historyIndex];
    if (entry && entry.filename) {
        try {
            const response = await fetch(`/images/${entry.filename}`);
            const blob = await response.blob();
            
            const file = new File([blob], entry.filename, { type: blob.type || 'image/jpeg' });
            if (navigator.canShare && navigator.canShare({ files: [file] })) {
                await navigator.share({
                    files: [file]
                });
            } else {
                const link = document.createElement('a');
                const objectUrl = URL.createObjectURL(blob);
                link.href = objectUrl;
                link.download = entry.filename;
                document.body.appendChild(link);
                link.click();
                setTimeout(() => {
                    document.body.removeChild(link);
                    URL.revokeObjectURL(objectUrl);
                }, 100);
            }
        } catch (err) {
            console.error('Download error:', err);
            const link = document.createElement('a');
            link.href = `/images/${entry.filename}`;
            link.download = entry.filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    }
}

async function downloadCurrentVideo() {
    if (historyIndex < 0 || historyIndex >= detectionHistory.length) return;
    const entry = detectionHistory[historyIndex];
    if (entry && entry.video_filename) {
        try {
            const response = await fetch(`/videos/${entry.video_filename}`);
            const blob = await response.blob();

            const file = new File([blob], entry.video_filename, { type: 'video/mp4' });
            if (navigator.canShare && navigator.canShare({ files: [file] })) {
                await navigator.share({ files: [file] });
            } else {
                const link = document.createElement('a');
                const objectUrl = URL.createObjectURL(blob);
                link.href = objectUrl;
                link.download = entry.video_filename;
                document.body.appendChild(link);
                link.click();
                setTimeout(() => {
                    document.body.removeChild(link);
                    URL.revokeObjectURL(objectUrl);
                }, 100);
            }
        } catch (err) {
            console.error('Video download error:', err);
            const link = document.createElement('a');
            link.href = `/videos/${entry.video_filename}`;
            link.download = entry.video_filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    }
}

function showToast(message) {
    if (!toastContainer) return;
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    
    toast.style.background = 'var(--bg-surface)';
    toast.style.color = 'var(--text-primary)';
    toast.style.padding = '12px 16px';
    toast.style.borderRadius = '8px';
    toast.style.boxShadow = 'var(--shadow-soft)';
    toast.style.border = '1px solid var(--accent-primary)';
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-20px)';
    toast.style.transition = 'opacity 0.3s, transform 0.3s';
    toast.style.fontSize = '0.9rem';
    toast.style.backdropFilter = 'blur(8px)';
    toast.style.webkitBackdropFilter = 'blur(8px)';
    
    toastContainer.appendChild(toast);
    
    requestAnimationFrame(() => {
        toast.style.opacity = '1';
        toast.style.transform = 'translateY(0)';
    });
    
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-20px)';
        setTimeout(() => {
            if (toastContainer.contains(toast)) {
                toastContainer.removeChild(toast);
            }
        }, 300);
    }, 4000);
}


// --- DSR: Step 4 Touch Gestures & Custom Zoom ---
let transform = { x: 0, y: 0, scale: 1 };
let pointers = [];
let startScale = 1;
let startDistance = 0;
let isPanning = false;

let touchStartX = 0;
let touchEndX = 0;
let swipePossible = true; 

const liveOverlay = document.getElementById('live-overlay');

if (savedImageWrapper && savedImage) {
    savedImageWrapper.style.touchAction = 'none';
    savedImageWrapper.addEventListener('pointerdown', onPointerDown);
    savedImageWrapper.addEventListener('pointermove', onPointerMove);
    savedImageWrapper.addEventListener('pointerup', onPointerUp);
    savedImageWrapper.addEventListener('pointercancel', onPointerUp);
    savedImageWrapper.addEventListener('pointerleave', onPointerUp);
    savedImageWrapper.addEventListener('wheel', onWheel, {passive: false});
}

if (liveOverlay) {
    liveOverlay.addEventListener('pointerdown', onPointerDown);
    liveOverlay.addEventListener('pointermove', onPointerMove);
    liveOverlay.addEventListener('pointerup', onPointerUp);
    liveOverlay.addEventListener('pointercancel', onPointerUp);
    liveOverlay.addEventListener('pointerleave', onPointerUp);
    liveOverlay.addEventListener('wheel', onWheel, {passive: false});
}

function resetTransform() {
    transform = { x: 0, y: 0, scale: 1 };
    applyTransform();
}

function applyTransform() {
    if (videoFeedContainer) {
        videoFeedContainer.style.setProperty('--pan-x', `${transform.x}px`);
        videoFeedContainer.style.setProperty('--pan-y', `${transform.y}px`);
        videoFeedContainer.style.setProperty('--zoom-scale', transform.scale);
    }
}


function onWheel(e) {
    e.preventDefault(); // Stop page from scrolling
    
    const zoomSensitivity = 1.1;
    let newScale = transform.scale;
    
    if (e.deltaY < 0) {
        newScale *= zoomSensitivity;
    } else {
        newScale /= zoomSensitivity;
    }
    
    newScale = Math.max(1, Math.min(newScale, 5));
    transform.scale = newScale;
    
    if (transform.scale <= 1) {
        resetTransform();
    } else {
        applyTransform();
    }
}

function getDistance(p1, p2) {
    return Math.hypot(p1.clientX - p2.clientX, p1.clientY - p2.clientY);
}

function onPointerDown(e) {
    e.preventDefault();
    pointers.push(e);

    if (pointers.length === 1) {
        isPanning = transform.scale > 1;
        swipePossible = transform.scale === 1;
        touchStartX = e.clientX;
    } else if (pointers.length === 2) {
        isPanning = false;
        swipePossible = false;
        startDistance = getDistance(pointers[0], pointers[1]);
        startScale = transform.scale;
    }
}

function onPointerMove(e) {
    e.preventDefault();
    const index = pointers.findIndex(p => p.pointerId === e.pointerId);
    if (index !== -1) pointers[index] = e;

    if (pointers.length === 1 && isPanning) {
        transform.x += (e.movementX || 0);
        transform.y += (e.movementY || 0);
        applyTransform();
    } else if (pointers.length === 2) {
        const currentDistance = getDistance(pointers[0], pointers[1]);
        let newScale = startScale * (currentDistance / startDistance);
        newScale = Math.max(1, Math.min(newScale, 5)); 
        transform.scale = newScale;
        applyTransform();
    }
}

function onPointerUp(e) {
    e.preventDefault();
    const index = pointers.findIndex(p => p.pointerId === e.pointerId);
    if (index !== -1) {
        if (pointers.length === 1 && swipePossible) {
            touchEndX = e.clientX;
            handleSwipe();
        }
        pointers.splice(index, 1);
    }
    
    if (pointers.length === 0) {
        if (transform.scale <= 1) {
            resetTransform();
        }
    } else if (pointers.length === 1) {
        isPanning = transform.scale > 1;
    }
}

function handleSwipe() {
    if (!swipePossible || transform.scale > 1) return;
    const swipeThreshold = 50;
    if (touchEndX < touchStartX - swipeThreshold) goForward();
    else if (touchEndX > touchStartX + swipeThreshold) goBack();
}

const origShowImage = showHistoryImage;
showHistoryImage = function() {
    if (typeof resetTransform === 'function') resetTransform();
    origShowImage();
};

const origSetLiveMode = setLiveMode;
setLiveMode = function() {
    if (typeof resetTransform === 'function') resetTransform();
    origSetLiveMode();
};
