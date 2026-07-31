# ovos-microphone-plugin-pyaudio

This microphone plugin for OVOS runs on Linux, macOS, and Windows through
PortAudio via `pyaudio`. It does not depend on `speech_recognition`. It
calls the PyAudio API directly and maps `sample_width` to the matching
PyAudio format for int16, int32, and float32 capture.

**Platform**: Linux, macOS, Windows (requires the `libportaudio2` system library).

**Source**: `PyAudioMicrophone` in `ovos_microphone_plugin_pyaudio/__init__.py`

**Entry point**: `opm.microphone = ovos-microphone-plugin-pyaudio`

---

## Configuration

| Field | Type | Default | Description |
|---|---|---|---|
| `device` | `str\|int\|None` | config hierarchy / `"default"` | Device name, integer index, or `"default"` |
| `period_size` | `int` | `1024` | Frames per `stream.read()` call |
| `timeout` | `float` | `5.0` | Seconds to block in `read_chunk()` before returning `None` |
| `multiplier` | `float` | `1.0` | Gain multiplier (skipped for float32, requires `audioop`) |
| `float32_output` | `bool` | `False` | Use `paFloat32`, required by ggwave |
| `muted` | `bool` | `False` | Enqueue silence instead of real audio |
| `queue_maxsize` | `int` | `8` | Max buffered chunks, oldest evicted when full |
| `sample_rate` | `int` | `16000` | Sample rate in Hz (base class) |
| `sample_width` | `int` | `2` | Bytes per sample (base class) |
| `sample_channels` | `int` | `1` | Channels (base class) |
| `chunk_size` | `int` | `4096` | Output chunk size in bytes (base class) |

### sample_width → PyAudio format mapping

| `sample_width` | PyAudio format |
|---|---|
| 1 | `paInt8` |
| 2 | `paInt16` |
| 3 | `paInt24` |
| 4 | `paInt32` |
| `float32_output=True` | `paFloat32` (overrides width) |

---

## float32 output (ggwave)

```json
{
  "listener": {
    "microphone": {
      "module": "ovos-microphone-plugin-pyaudio",
      "ovos-microphone-plugin-pyaudio": {
        "sample_rate": 48000,
        "sample_width": 4,
        "sample_channels": 1,
        "float32_output": true
      }
    }
  }
}
```

---

## Device selection

`find_input_device(name)` tries, in order: integer passthrough, exact name,
substring, then regex. `list_input_devices()` returns `(index, info_dict)`
pairs for all input-capable devices.

### Device resolution order

1. `listener.microphone.ovos-microphone-plugin-pyaudio.device`
2. `listener.device`
3. `"default"`

---

## Known limitations

- No resampling or channel conversion
- No stream-open fallback chain
- `multiplier` gain requires `audioop` (removed from Python 3.13 stdlib)
