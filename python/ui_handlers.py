from typing import List, Set


def emit_detection_saved(ui, detection_history: List[dict], entry: dict):
    """Notify UI that a new detection was saved."""
    payload = {
        "entry": entry,
        "total": len(detection_history),
    }
    try:
        ui.send_message("detection_saved", message=payload)
    except Exception as e:
        print(f"[UI] Failed to emit detection_saved: {e}")


def emit_history_list(ui, detection_history: List[dict]):
    """Send full detection history list to UI."""
    payload = {
        "history": detection_history,
        "total": len(detection_history),
    }
    try:
        ui.send_message("history_list", message=payload)
    except Exception as e:
        print(f"[UI] Failed to emit history_list: {e}")


def emit_threshold(ui, detection_confidence: float):
    """Send current detection confidence threshold to UI."""
    payload = {"value": detection_confidence}
    try:
        ui.send_message("threshold", message=payload)
    except Exception as e:
        print(f"[UI] Failed to emit threshold: {e}")


def emit_detected_labels(ui, detected_labels: Set[str], detection_label: str):
    """Broadcast the current detected label list and selected label to the UI."""
    labels_payload = {
        "labels": sorted(detected_labels),
        "selected": detection_label.lower(),
    }
    print(f"[DEBUG] Emitting labels: {labels_payload}")
    try:
        ui.send_message("labels", message=labels_payload)
        print("[DEBUG] Labels emitted successfully")
    except Exception as e:
        print(f"[UI] Failed to emit labels: {e}")


def handle_confidence_override(detection_stream, set_confidence, _sid, value):
    """Handle confidence override messages from the Web UI."""
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        print(f"[UI] Ignoring confidence override (not a number): {value}")
        return

    if not 0.0 <= threshold <= 1.0:
        print(f"[UI] Ignoring confidence override outside [0,1]: {threshold}")
        return

    detection_stream.override_threshold(threshold)
    set_confidence(threshold)
    print(f"[UI] Detection confidence updated to {threshold:.2f}")


def handle_label_override(detected_labels: Set[str], set_label, _sid, value, emit_labels):
    """Handle label override from UI dropdown."""
    if not isinstance(value, str):
        print(f"[UI] Ignoring label override (not a string): {value}")
        return

    label = value.strip().lower()
    if not label:
        print("[UI] Ignoring label override (empty)")
        return

    if label not in detected_labels:
        print(f"[UI] Ignoring label override (unknown): {label}")
        return

    set_label(label)
    print(f"[UI] Detection label updated to '{label}'")
    emit_labels()


def handle_labels_request(emit_labels, _sid, _value):
    """Send current detected labels list to requesting client."""
    print(f"[DEBUG] request_labels received from client sid={_sid}")
    emit_labels()


def handle_history_request(emit_history, _sid, _value):
    """Send detection history list to requesting client."""
    print(f"[DEBUG] request_history received from client sid={_sid}")
    emit_history()


def handle_threshold_request(emit_threshold_fn, _sid, _value):
    """Send current detection threshold to requesting client."""
    print(f"[DEBUG] request_threshold received from client sid={_sid}")
    emit_threshold_fn()


def handle_image_request(ui, detection_history: List[dict], _sid, value):
    """Send specific detection record by index."""
    try:
        index = int(value) if value is not None else -1
    except (TypeError, ValueError):
        index = -1

    if not detection_history:
        return

    if index < 0:
        index = len(detection_history) + index

    if 0 <= index < len(detection_history):
        entry = detection_history[index]
        payload = {
            "entry": entry,
            "index": index,
            "total": len(detection_history),
        }
        try:
            ui.send_message("image_data", message=payload)
        except Exception as e:
            print(f"[UI] Failed to emit image_data: {e}")

