# MeshCommand

Remote command execution over Meshtastic mesh radio networks. Run shell commands on a Raspberry Pi from anywhere within radio range — no internet required.

MeshCommand listens on a private, encrypted Meshtastic channel for `!command` messages, executes them, and sends chunked responses back over the mesh.

## Prerequisites

- Raspberry Pi (any model)
- Meshtastic-compatible radio (e.g., T-Beam, Heltec, RAK) connected via USB serial
- Python 3.7+
- A second Meshtastic device (phone app or another radio) to send commands from

## Setup

### 1. Clone and install dependencies

```bash
cd /home/pi
git clone <repo-url> meshcommand
cd meshcommand
pip install -r requirements.txt
```

### 2. Configure your Meshtastic radio

Make sure your Meshtastic devices share a private channel. MeshCommand defaults to **channel 2** (channel 0 is the public default). Both the Pi's radio and your sending device must be on the same channel with the same encryption key.

### 3. Edit config.yaml

```yaml
device: auto       # "auto" to auto-detect, or a serial port like /dev/ttyUSB0
channel: 2         # Meshtastic channel index to listen on
```

See [Configuration](#configuration) for full details.

### 4. Test manually

```bash
python3 meshcommand.py
```

Send `!ping` from another Meshtastic device on the same channel. You should get `pong` back.

### 5. Install as a systemd service

The included install script copies files to `/opt/meshcommand`, installs dependencies, and enables the service:

```bash
sudo ./install.sh
```

Or install manually:

```bash
sudo cp meshcommand.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable meshcommand
sudo systemctl start meshcommand
```

Check status:

```bash
sudo systemctl status meshcommand
```

View logs:

```bash
journalctl -u meshcommand -f
```

A log file is also written to `meshcommand.log` in the working directory.

## Usage

From any Meshtastic device on the same channel, send text messages prefixed with `!`:

### Built-in commands

These are hardcoded in `meshcommand.py` and always available:

| Command | Description |
|---------|-------------|
| `!help` | List all available commands |
| `!ping` | Connectivity test (responds with "pong") |
| `!status` | Service uptime, command count, last RSSI |
| `!battery` | Battery level and voltage |
| `!radio` | Firmware version, hardware model, region |
| `!airtime` | Channel utilization and TX airtime |
| `!nodes` | List known mesh nodes with signal and last-seen time |
| `!signal` | Per-node signal quality (SNR and hops) |

### Default custom commands

These ship in `config.yaml` and can be modified or removed:

| Command | Description |
|---------|-------------|
| `!sysinfo` | System info (uname + IP addresses) |
| `!uptime` | System uptime |
| `!whoami` | Current user |
| `!temp` | CPU temperature |
| `!mem` | Memory usage |
| `!ip` | Network interfaces |
| `!extip` | External IP address |
| `!reboot confirm` | Reboot the Pi (requires confirmation) |

Commands with `confirm: true` in the config require the word `confirm` as an argument to prevent accidental execution.

## Configuration

All settings are in `config.yaml`:

### Device settings

| Key | Default | Description |
|-----|---------|-------------|
| `device` | `auto` | Serial port or `"auto"` to auto-detect |
| `channel` | `2` | Meshtastic channel index (0 = public, 1+ = private) |

### Response settings

| Key | Default | Description |
|-----|---------|-------------|
| `max_chunk_bytes` | `200` | Max text per mesh message (Meshtastic limit is ~233) |
| `chunk_delay` | `3.0` | Seconds between chunks (mesh needs time to transmit) |
| `command_timeout` | `10` | Max seconds for shell command execution |
| `max_chunks` | `10` | Max chunks per response (prevents mesh flooding) |
| `command_prefix` | `!` | Prefix that triggers command parsing |

### Adding custom commands

Add entries to the `commands` list in `config.yaml`:

```yaml
commands:
  - name: mycommand
    help: "Description shown in !help"
    command: "some-shell-command --flags"

  - name: dangerous-thing
    help: "Does something risky (requires !dangerous-thing confirm)"
    command: "sudo do-the-thing"
    confirm: true
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Command name (invoked as `!name`) |
| `help` | Yes | Description shown in `!help` output |
| `command` | Yes | Shell command to execute |
| `confirm` | No | If `true`, requires `confirm` argument to run |

### Scheduled broadcasts

The `schedules` list runs commands automatically and broadcasts their output to the channel — useful for periodic telemetry (uptime, temperature, sensor readings) without anyone needing to ask.

```yaml
schedules:
  - name: hourly-uptime
    command: "uptime -p"
    interval: 3600       # seconds
  - name: temp-watch
    command: "vcgencmd measure_temp"
    interval: 1800
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Label included in the broadcast as `[sched:<name>]` |
| `command` | Yes | Shell command to execute |
| `interval` | Yes | Seconds between runs |

Each schedule waits one full interval before its first run, so restarting the service doesn't immediately flood the mesh. Output is sent on the configured channel prefixed with `[sched:<name>]` so recipients can distinguish scheduled broadcasts from responses to their own commands.

## How it works

1. MeshCommand connects to a Meshtastic radio over USB serial
2. It subscribes to incoming mesh messages on the configured channel
3. On startup, it announces itself on the mesh with its IP address
4. Messages starting with the command prefix are parsed and dispatched
5. Shell commands run via `subprocess` with a configurable timeout
6. Output is split into chunks (LoRa bandwidth is limited) and sent back over the mesh with delays between each chunk
7. If the radio disconnects, MeshCommand reconnects automatically with exponential backoff

Responses longer than `max_chunk_bytes * max_chunks` (default: 2KB) are truncated.

## Project structure

```
meshcommand/
├── meshcommand.py        # Main service
├── config.yaml           # Configuration
├── install.sh            # One-line installer (copies to /opt, enables systemd)
├── meshcommand.service   # Systemd unit file
├── requirements.txt      # Python dependencies
└── README.md
```
