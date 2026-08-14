# Amcrest PTZ Bridge

Amcrest PTZ Bridge exposes the local Dahua DVRIP pan and tilt controls of an Amcrest SmartHome camera as a small ONVIF service that Frigate can use. Video and audio continue to flow directly between the camera and Frigate/go2rtc; this container handles movement commands only.

The project is designed for one camera per container instance. Install another instance with a different name and host port for each additional camera.

## Data flow

```text
Frigate PTZ controls
        |
        | ONVIF ContinuousMove / Stop
        v
Amcrest PTZ Bridge :18880
        |
        | local authenticated DVRIP
        v
Amcrest camera :37777
```

Every movement has a configurable fail-safe timer, so the bridge stops the camera even if the ONVIF client loses its connection before sending `Stop`.

## Unraid installation

After this repository and its GHCR package are public, search for **Amcrest PTZ Bridge** in Community Applications. Until the app is accepted, install the raw template URL through Unraid Docker authoring mode.

Configure:

- a unique ONVIF host port, beginning with `18880`;
- the camera's LAN IP address;
- a local camera username and password;
- optional movement timeout and client allowlist settings.

For a second camera, install a second instance and use port `18881`, then `18882`, and so on.

### Frigate example

```yaml
cameras:
  living_room:
    onvif:
      host: 192.0.2.20
      port: 18880
```

Replace the host with the Unraid server address and the port with the host port selected in the app template.

## Configuration

| Environment variable | Default | Purpose |
|---|---:|---|
| `CAMERA_HOST` | required | Camera LAN IP address or hostname |
| `CAMERA_USERNAME` | `admin` | Local camera account |
| `CAMERA_PASSWORD` | required | Local camera password |
| `CAMERA_PASSWORD_FILE` | empty | Optional file containing the password; takes precedence over `CAMERA_PASSWORD` |
| `CAMERA_NAME` | `amcrest-camera` | ONVIF device/profile name |
| `CAMERA_MODEL` | `Amcrest SmartHome PTZ` | Model string reported through ONVIF |
| `CAMERA_PORT` | `37777` | Dahua DVRIP port |
| `CAMERA_CHANNEL` | `0` | DVRIP channel |
| `LISTEN_PORT` | `18880` | Internal ONVIF listener port |
| `MAX_MOVE_SECONDS` | `1.25` | Movement fail-safe timeout |
| `ALLOWED_CLIENTS` | empty | Optional comma-separated IP/CIDR allowlist |
| `LOG_LEVEL` | `INFO` | Python log level |

When `ALLOWED_CLIENTS` is empty, any client able to reach the published Docker port can issue PTZ commands. Docker NAT can make local clients appear as a `172.16.0.0/12` gateway address, so test an allowlist carefully. Loopback health checks are always permitted.

### Advanced YAML mode

Set `BRIDGE_CONFIG=/config/config.yml` and mount a YAML file to use the original multi-camera configuration mode. The Community Applications template intentionally uses environment variables and one instance per camera because that produces a safer and clearer Unraid installation form.

## Network and internet access

At runtime the application listens for ONVIF requests, connects to the configured camera over local TCP port `37777`, and checks its own localhost health endpoint. It contains no update checker, telemetry, advertising, or cloud integration.

Building or updating the image requires internet access to pull the Python base image, download the pinned MIT-licensed DahuaConsole source, and install Python dependencies. Unraid also contacts GHCR to pull images and check for updates. Docker bridge networking technically permits outbound internet access unless the administrator adds an egress firewall rule.

The `www.onvif.org` strings in `bridge.py` are XML namespace identifiers; they are not outbound web requests.

## Local Docker run

```bash
docker run -d \
  --name amcrest-ptz-bridge \
  -p 18880:18880/tcp \
  -e CAMERA_HOST=192.0.2.25 \
  -e CAMERA_USERNAME=admin \
  -e CAMERA_PASSWORD='replace-me' \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  ghcr.io/gaming09/amcrest-ptz-bridge:latest
```

## Publishing

This repository is configured for the `gaming09` GitHub account. Before submitting it to Community Applications:

1. Create a public GitHub repository named `amcrest-ptz-bridge`.
2. Push this repository to its `main` branch.
3. Confirm the Validate workflow passes. The container workflow builds and publishes `ghcr.io/gaming09/amcrest-ptz-bridge`.
4. Make the GHCR package public after its first build.
5. Create a `v1.0.0` tag for the first versioned image.
6. Run Validate and Scan at the [Unraid Community Apps submission portal](https://ca.unraid.net/submit).
7. Submit after all checks pass.

The repository layout and metadata follow Unraid's official Community Apps starter repository. The submission portal remains the source of truth if its requirements change.

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python scripts/validate_repository.py
docker build -t amcrest-ptz-bridge:dev .
```

## Security and credentials

The Unraid template masks the camera password in the form, but Docker must still retain it as container configuration and an Unraid administrator can inspect it. For file-based secret handling, set `CAMERA_PASSWORD_FILE` and mount a read-only secret file instead of using `CAMERA_PASSWORD`.

The container runs as an unprivileged user, drops Linux capabilities in the Unraid template, uses a read-only root filesystem, and does not require access to Unraid shares or the Docker socket.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License and attribution

Bridge code is MIT licensed. The image uses a pinned revision of the MIT-licensed [mcw0/DahuaConsole](https://github.com/mcw0/DahuaConsole) project for DVRIP connectivity. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This project is not affiliated with or endorsed by Amcrest, Dahua, Frigate, or Unraid.
