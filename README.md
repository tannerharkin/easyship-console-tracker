# easyship-console-tracker

Command-line utility to check Easyship parcel status from the terminal.

Reverse-engineered from [trackmyshipment.co](https://www.trackmyshipment.co). Handles the reCAPTCHA v3 handshake necessary to make requests to this API.

## Requirements

```
pip install requests rich
```

## Usage

```
python track.py <TRACKING_NUMBER> [--format pretty|json|xml|kv]
```

```
python track.py ESUS123456789
python track.py ESUS123456789 --format json
python track.py ESUS123456789 --format xml
python track.py ESUS123456789 --format kv
```

## Output formats

| Format   | Description                                      |
|----------|--------------------------------------------------|
| `pretty` | Rich-formatted terminal output (default)         |
| `json`   | Full raw API response                            |
| `xml`    | Summary + checkpoints as an XML document         |
| `kv`     | `KEY=VALUE` pairs, suitable for shell scripting  |

## Notes

- The `kv` format quotes values containing spaces and indexes checkpoints as `CHECKPOINT_0_*`, `CHECKPOINT_1_*`, etc.
