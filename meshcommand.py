#!/usr/bin/env python3
"""MeshCommand - Meshtastic remote control service for Raspberry Pi.

Listens on a private Meshtastic channel for command messages,
executes them, and sends chunked responses back over the mesh.
"""

import logging
import logging.handlers
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml
from pubsub import pub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            "meshcommand.log", maxBytes=2 * 1024 * 1024, backupCount=3
        ),
    ],
)
log = logging.getLogger("meshcommand")


class MeshCommand:
    def __init__(self, config_path="config.yaml"):
        self.config = self._load_config(config_path)
        self.commands = self._build_command_registry()
        self.interface = None
        self.my_node_id = None
        self.start_time = time.time()
        self.last_rssi = None
        self.running = True
        # Serializes mesh sends so scheduled broadcasts and command responses
        # don't interleave their chunks
        self._send_lock = threading.Lock()

    def _load_config(self, config_path):
        path = Path(config_path)
        if not path.exists():
            log.error("Config file not found: %s", config_path)
            sys.exit(1)
        with open(path) as f:
            config = yaml.safe_load(f)
        log.info("Loaded config from %s", config_path)
        return config

    def _build_command_registry(self):
        registry = {}
        for cmd in self.config.get("commands", []):
            registry[cmd["name"]] = cmd
        log.info("Registered %d commands: %s", len(registry), ", ".join(registry))
        return registry

    def _on_connect(self, interface, topic=pub.AUTO_TOPIC):
        try:
            self.my_node_id = interface.myInfo.my_node_num
        except AttributeError:
            log.warning("Could not read node ID from device info")
            return
        log.info("Connected to Meshtastic device (node %s)", self.my_node_id)

    def _on_receive(self, packet, interface):
        try:
            self._handle_packet(packet)
        except Exception:
            log.exception("Error handling packet")

    def _handle_packet(self, packet):
        # Ignore our own messages
        if packet.get("from") == self.my_node_id:
            return

        # Must be on our configured channel
        channel = packet.get("channel", 0)
        if channel != self.config.get("channel", 1):
            return

        # Must be a text message
        decoded = packet.get("decoded", {})
        text = decoded.get("text")
        if not text:
            return

        prefix = self.config.get("command_prefix", "!")
        if not text.startswith(prefix):
            return

        # Track RSSI for !status
        self.last_rssi = packet.get("rxRssi")
        sender = packet.get("fromId", "unknown")
        log.info("Command from %s: %s", sender, text)

        # Parse command and arguments
        parts = text[len(prefix):].strip().split()
        if not parts:
            return
        cmd_name = parts[0].lower()
        args = parts[1:]

        # Handle built-in commands
        if cmd_name == "help":
            self._send_help()
            return
        if cmd_name == "ping":
            self._send_response("pong")
            return
        if cmd_name == "status":
            self._send_status()
            return
        if cmd_name == "battery":
            self._send_battery()
            return
        if cmd_name == "radio":
            self._send_radio()
            return
        if cmd_name == "airtime":
            self._send_airtime()
            return
        if cmd_name == "nodes":
            self._send_nodes()
            return
        if cmd_name == "signal":
            self._send_signal()
            return

        # Look up user-defined command
        cmd = self.commands.get(cmd_name)
        if not cmd:
            self._send_response(
                f"Unknown command: {cmd_name}\nType {prefix}help for available commands."
            )
            return

        # Check confirmation requirement
        if cmd.get("confirm") and "confirm" not in args:
            self._send_response(
                f"Dangerous command. Send: {prefix}{cmd_name} confirm"
            )
            return

        # Execute
        result = self._execute(cmd["command"])
        self._send_response(result)

    def _execute(self, shell_cmd):
        timeout = self.config.get("command_timeout", 10)
        log.info("Executing: %s", shell_cmd)
        try:
            result = subprocess.run(
                shell_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout.strip()
            if result.returncode != 0 and result.stderr.strip():
                output = output + "\n" + result.stderr.strip() if output else result.stderr.strip()
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        except Exception as e:
            return f"Error: {e}"

    def _send_help(self):
        prefix = self.config.get("command_prefix", "!")
        lines = [
            f"{prefix}help - List commands",
            f"{prefix}ping - Connectivity test",
            f"{prefix}status - Service status",
            f"{prefix}battery - Battery level and voltage",
            f"{prefix}radio - Firmware, hardware, region",
            f"{prefix}airtime - Channel utilization and TX airtime",
            f"{prefix}nodes - List known mesh nodes",
            f"{prefix}signal - Per-node signal quality",
        ]
        for name, cmd in sorted(self.commands.items()):
            lines.append(f"{prefix}{name} - {cmd.get('help', 'No description')}")
        self._send_response("\n".join(lines))

    def _send_status(self):
        uptime_secs = int(time.time() - self.start_time)
        hours, remainder = divmod(uptime_secs, 3600)
        minutes, seconds = divmod(remainder, 60)
        lines = [
            f"MeshCommand service",
            f"Uptime: {hours}h {minutes}m {seconds}s",
            f"Commands loaded: {len(self.commands)}",
            f"Last RSSI: {self.last_rssi if self.last_rssi is not None else 'N/A'}",
        ]
        self._send_response("\n".join(lines))

    def _get_local_node(self):
        """Return the local node's data dict from the node database."""
        if not self.interface or not self.interface.nodes:
            return None
        for node in self.interface.nodes.values():
            if node.get("num") == self.my_node_id:
                return node
        return None

    def _send_battery(self):
        try:
            node = self._get_local_node()
            metrics = node.get("deviceMetrics", {}) if node else {}
            level = metrics.get("batteryLevel")
            voltage = metrics.get("voltage")
            if level is not None and level > 100:
                source = "external power"
            elif level is not None:
                source = f"{level}%"
            else:
                source = "N/A"
            volts = f"{voltage:.2f}V" if voltage is not None else "N/A"
            self._send_response(f"Battery: {source}\nVoltage: {volts}")
        except Exception:
            log.exception("Error reading battery")
            self._send_response("Error reading battery info")

    def _send_radio(self):
        try:
            meta = getattr(self.interface, "metadata", None)
            fw = getattr(meta, "firmware_version", "N/A") if meta else "N/A"
            hw = getattr(meta, "hw_model", "N/A") if meta else "N/A"
            region = getattr(meta, "region", "N/A") if meta else "N/A"
            lines = [
                f"Firmware: {fw}",
                f"Hardware: {hw}",
                f"Region: {region}",
            ]
            self._send_response("\n".join(lines))
        except Exception:
            log.exception("Error reading radio info")
            self._send_response("Error reading radio info")

    def _send_airtime(self):
        try:
            node = self._get_local_node()
            metrics = node.get("deviceMetrics", {}) if node else {}
            chan_util = metrics.get("channelUtilization")
            air_util = metrics.get("airUtilTx")
            chan_str = f"{chan_util:.1f}%" if chan_util is not None else "N/A"
            air_str = f"{air_util:.1f}%" if air_util is not None else "N/A"
            self._send_response(f"Channel util: {chan_str}\nTX airtime: {air_str}")
        except Exception:
            log.exception("Error reading airtime")
            self._send_response("Error reading airtime info")

    def _send_nodes(self):
        try:
            if not self.interface or not self.interface.nodes:
                self._send_response("No nodes known")
                return
            lines = []
            now = time.time()
            for node in self.interface.nodes.values():
                if node.get("num") == self.my_node_id:
                    continue
                user = node.get("user", {})
                name = user.get("shortName") or user.get("longName") or "?"
                hops = node.get("hopsAway", "?")
                snr = node.get("snr")
                snr_str = f"{snr}dB" if snr is not None else "?"
                last = node.get("lastHeard")
                if last:
                    ago = int(now - last)
                    if ago < 60:
                        age = f"{ago}s"
                    elif ago < 3600:
                        age = f"{ago // 60}m"
                    else:
                        age = f"{ago // 3600}h"
                else:
                    age = "?"
                lines.append(f"{name} | hops:{hops} snr:{snr_str} seen:{age}")
            self._send_response("\n".join(lines) if lines else "No other nodes seen")
        except Exception:
            log.exception("Error reading nodes")
            self._send_response("Error reading node list")

    def _send_signal(self):
        try:
            if not self.interface or not self.interface.nodes:
                self._send_response("No signal data")
                return
            lines = []
            for node in self.interface.nodes.values():
                if node.get("num") == self.my_node_id:
                    continue
                user = node.get("user", {})
                name = user.get("shortName") or user.get("longName") or "?"
                snr = node.get("snr")
                snr_str = f"{snr}dB" if snr is not None else "N/A"
                hops = node.get("hopsAway", "?")
                lines.append(f"{name} | SNR:{snr_str} hops:{hops}")
            self._send_response("\n".join(lines) if lines else "No signal data")
        except Exception:
            log.exception("Error reading signal data")
            self._send_response("Error reading signal data")

    def _split_bytes(self, text, max_bytes):
        """Split text into chunks that each fit within max_bytes when UTF-8 encoded."""
        # Must fit at least one UTF-8 character (max 4 bytes) to guarantee progress
        max_bytes = max(max_bytes, 4)
        chunks = []
        encoded = text.encode("utf-8")
        while encoded:
            if len(encoded) <= max_bytes:
                chunks.append(encoded.decode("utf-8", errors="replace"))
                break
            # Find the last valid UTF-8 boundary at or before max_bytes
            cut = max_bytes
            while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
                cut -= 1
            if cut == 0:
                # Pathological input; force progress to avoid infinite loop
                cut = max_bytes
            chunks.append(encoded[:cut].decode("utf-8", errors="replace"))
            encoded = encoded[cut:]
        return chunks

    def _send_response(self, text):
        with self._send_lock:
            channel = self.config.get("channel", 1)
            max_bytes = self.config.get("max_chunk_bytes", 200)
            chunk_delay = self.config.get("chunk_delay", 3.0)
            max_chunks = self.config.get("max_chunks", 10)

            # Reserve space for chunk prefix like "[10/10] "
            prefix_reserve = 10
            chunk_limit = max_bytes - prefix_reserve

            # Truncate if too long
            max_total = chunk_limit * max_chunks
            text_bytes = text.encode("utf-8")
            if len(text_bytes) > max_total:
                text = text_bytes[:max_total - 20].decode("utf-8", errors="ignore") + "\n... (truncated)"

            # Split into chunks by byte size
            chunks = self._split_bytes(text, chunk_limit)

            total = len(chunks)
            for i, chunk in enumerate(chunks):
                if total > 1:
                    msg = f"[{i + 1}/{total}] {chunk}"
                else:
                    msg = chunk

                log.info("Sending chunk %d/%d (%d bytes)", i + 1, total, len(msg))
                try:
                    self.interface.sendText(msg, channelIndex=channel)
                except Exception:
                    log.exception("Failed to send chunk %d/%d, reconnecting", i + 1, total)
                    self._reconnect()
                    break

                if i < total - 1:
                    time.sleep(chunk_delay)

    def _connect(self):
        from meshtastic.serial_interface import SerialInterface

        device = self.config.get("device", "auto")
        delay = 5
        max_delay = 60

        while self.running:
            try:
                if device == "auto":
                    log.info("Auto-detecting Meshtastic device...")
                    self.interface = SerialInterface()
                else:
                    log.info("Connecting to Meshtastic device on %s...", device)
                    self.interface = SerialInterface(devPath=device)
                return
            except Exception:
                log.exception("Connection failed, retrying in %ds...", delay)
                time.sleep(delay)
                delay = min(delay * 2, max_delay)

    def _start_schedules(self):
        schedules = self.config.get("schedules") or []
        for sched in schedules:
            name = sched.get("name")
            shell_cmd = sched.get("command")
            interval = sched.get("interval")
            if not name or not shell_cmd or not isinstance(interval, (int, float)) or interval <= 0:
                log.warning("Skipping invalid schedule entry: %s", sched)
                continue
            thread = threading.Thread(
                target=self._schedule_loop,
                args=(name, shell_cmd, int(interval)),
                daemon=True,
                name=f"sched-{name}",
            )
            thread.start()
            log.info("Schedule '%s' started: %s (every %ds)", name, shell_cmd, interval)

    def _schedule_loop(self, name, shell_cmd, interval):
        # Wait one full interval before first fire so restarts don't spam the mesh
        self._sleep_until(time.time() + interval)
        while self.running:
            log.info("Schedule '%s' firing: %s", name, shell_cmd)
            try:
                output = self._execute(shell_cmd)
                self._send_response(f"[sched:{name}]\n{output}")
            except Exception:
                log.exception("Schedule '%s' failed", name)
            self._sleep_until(time.time() + interval)

    def _sleep_until(self, deadline):
        """Sleep in short slices so shutdown is responsive."""
        while self.running:
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            time.sleep(min(1.0, remaining))

    def _reconnect(self):
        log.warning("Attempting to reconnect...")
        try:
            if self.interface:
                self.interface.close()
        except Exception:
            pass
        self.interface = None
        self._connect()

    def run(self):
        # Signal handling (register before connect so CTRL-C during startup is caught)
        def shutdown(signum, frame):
            log.info("Received shutdown signal.")
            self.running = False

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        # Subscribe to Meshtastic events
        pub.subscribe(self._on_connect, "meshtastic.connection.established")
        pub.subscribe(self._on_receive, "meshtastic.receive")

        # Connect
        self._connect()

        log.info("MeshCommand service running. Listening on channel %d for '%s' commands.",
                 self.config.get("channel", 1), self.config.get("command_prefix", "!"))

        # Announce startup on the mesh channel
        try:
            ip = subprocess.run("hostname -I", shell=True, capture_output=True, text=True, timeout=5)
            addr = ip.stdout.strip().split()[0] if ip.stdout.strip() else "unknown"
            now = time.strftime("%a %I:%M %p")
            self._send_response(f"MeshCommand up at {addr} at {now}")
        except Exception:
            log.exception("Startup announcement failed")

        # Start scheduled broadcasts
        self._start_schedules()

        # Main loop
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Interrupted by user.")
        finally:
            log.info("Closing Meshtastic interface...")
            if self.interface:
                self.interface.close()
            log.info("MeshCommand stopped.")


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    service = MeshCommand(config_path)
    service.run()


if __name__ == "__main__":
    main()
