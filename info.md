# Remote System Monitor for Home Assistant

Real-time hardware monitoring integration for **TRIGONE Remote System Monitor Server** (v3.98, protocol 311).

## Highlights
- **Direct TLS connection** to your Remote System Monitor server on port `19150`
- **Zero brokers** - no MQTT needed, low latency, direct streaming
- **Full hardware tree discovery**: CPU, GPU (NVIDIA/AMD/Intel), RAM, Motherboard, Storage Disks, Network Interfaces, Fans, Voltages, and Temperatures
- **Hierarchical Devices**: Cleanly maps every physical component (CPU, GPU, Disks) as child devices of the host computer
- **Config Flow & Options Flow**: Configure IP, port, password, and polling rate directly in the Home Assistant UI
