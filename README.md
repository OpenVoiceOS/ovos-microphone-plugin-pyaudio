## Description

`ovos-microphone-plugin-pyaudio` is a microphone plugin for OpenVoiceOS (OVOS). It captures audio through [PyAudio](https://people.csail.mit.edu/hubert/pyaudio/) (PortAudio) and does not depend on `speech_recognition`. The plugin maps `sample_width` to the matching PyAudio format and supports int8, int16, int24, int32, and float32 capture.

## Install

```bash
pip install ovos-microphone-plugin-pyaudio
```

The plugin needs the `libportaudio2` system library on Linux, macOS, and Windows.

## Usage

OVOS loads this plugin through the `opm.microphone` entry point. Select it in `mycroft.conf`:

```json
{
  "listener": {
    "microphone": {
      "module": "ovos-microphone-plugin-pyaudio"
    }
  }
}
```

See [docs/index.md](docs/index.md) for the full configuration reference, the float32 output example for ggwave, and the device selection rules.

## Related projects

OVOS ships other microphone plugins in the same family:

- [ovos-microphone-plugin-sounddevice](https://github.com/OpenVoiceOS/ovos-microphone-plugin-sounddevice)
- [ovos-microphone-plugin-alsa](https://github.com/OpenVoiceOS/ovos-microphone-plugin-alsa)
- [ovos-microphone-plugin-files](https://github.com/OpenVoiceOS/ovos-microphone-plugin-files)

## License

Apache-2.0
