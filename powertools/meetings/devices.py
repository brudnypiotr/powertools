"""WASAPI device names for the meeting recorder settings (microphones and outputs to capture)."""

from __future__ import annotations


def meeting_devices() -> dict:
    try:
        import pyaudiowpatch as pa
    except Exception:
        return {"inputs": [], "outputs": []}
    p = pa.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)["index"]
        inputs, outputs = [], []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi:
                continue
            if d.get("isLoopbackDevice"):
                continue
            if d["maxInputChannels"] > 0:
                inputs.append(d["name"])
            if d["maxOutputChannels"] > 0:
                outputs.append(d["name"])
        return {"inputs": inputs, "outputs": outputs}
    finally:
        p.terminate()
