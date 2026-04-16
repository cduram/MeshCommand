# MeshCommand Roadmap

Future features under consideration. Entries here are ideas, not commitments — they may be reshaped or dropped.

## Per-sender ACL

Today anyone with the channel PSK can execute any registered command, including `!reboot confirm`. The security boundary is entirely the channel key. An ACL would let operators restrict sensitive commands to specific mesh node IDs.

**Proposed config:**

```yaml
# Global allowlist — if set, only these nodes can issue any command
allowed_nodes:
  - "!a1b2c3d4"
  - "!deadbeef"

commands:
  - name: reboot
    command: "sudo reboot"
    confirm: true
    # Per-command allowlist overrides the global one
    allowed_nodes:
      - "!a1b2c3d4"
```

**Behavior:**

- If `allowed_nodes` is unset at both levels, any node on the channel can run the command (current behavior).
- If a per-command list is set, only those node IDs can run it.
- Otherwise, the global `allowed_nodes` applies.
- Rejected commands log the sender ID and respond with an "unauthorized" message (or silently drop, configurable).

**Implementation notes:**

- Sender node ID is already available in `_handle_packet` via `packet.get("fromId")`.
- Check happens before `_execute` and before the `confirm` check.
- Keep it opt-in so existing deployments don't break.

## Sensor / GPIO bridge

Turn the Pi into a mesh-accessible IoT node — read sensors, toggle relays, query I2C devices without needing to shell in.

**Proposed shape:**

```yaml
sensors:
  - name: door
    type: gpio_in
    pin: 17
    help: "Front door reed switch"

  - name: porchlight
    type: gpio_out
    pin: 22
    confirm: true
    help: "Porch light relay (on/off)"

  - name: bme280
    type: i2c
    driver: bme280
    address: 0x76
    help: "Temperature/humidity/pressure"
```

Usage would be `!door`, `!porchlight on`, `!bme280`.

**Key design consideration (security):**

This is the first feature that would accept sender-supplied arguments and feed them into device operations. The current code is injection-safe *only* because mesh args are never passed to the shell. A sensor/GPIO bridge must:

- Use a **typed arg schema** per sensor (e.g., `on` / `off` for `gpio_out`) — not string interpolation into a shell command.
- Validate against an allowlist of argument values before dispatch.
- Never expand user input into a `subprocess(shell=True)` call.

The cleanest path is to use a Python library (`gpiozero`, `smbus2`, `adafruit-circuitpython-*`) rather than shelling out to `gpio`/`i2cget`. That keeps the arg path entirely in Python without a shell in the middle.

**Dependencies:**

- `gpiozero` for GPIO (pure Python on Pi)
- Device-specific libraries per sensor type

Should be optional (only loaded if `sensors` is configured) so the core service stays lightweight.

## Other ideas (unprioritized)

- **Store-and-forward messaging** — Pi as a mesh BBS: leave messages for specific nodes, delivered when they come online.
- **Cron-style schedules** — Current `interval` is seconds-only. Add `cron: "0 8 * * *"` for daily-at-8am patterns. Requires `croniter`.
- **Response compression** — Zstd-compress output and send base85 when response would otherwise be truncated. Trades decode effort for more info through the pipe.
- **Home Assistant bridge** — Expose MeshCommand as a Home Assistant integration over MQTT or REST.
- **Multi-radio support** — Run one service, fanning messages across multiple radios on different channels.
