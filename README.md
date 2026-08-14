# Amcrest PTZ Bridge - *VibeCoded*

Amcrest PTZ Bridge exposes the local Dahua DVRIP pan and tilt controls of an Amcrest SmartHome camera as a small ONVIF service that Frigate can use. Video and audio continue to flow directly between the camera and Frigate/go2rtc; this container handles movement commands only. I absolutely hate the amcrest smarthome app and want to use it as little as possible so i wanted all ptz controls through frigate so i vibecoded this. It doesnt reach out to the internet, it doesnt send creds anywhere.  

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

## Connect the bridge to Frigate

The bridge does not replace the camera's existing `ffmpeg` or go2rtc stream configuration. It adds the ONVIF endpoint that Frigate uses for the PTZ buttons.

1. In Frigate, open **Settings > Configuration Editor**. If you edit the file directly, use `config.yml` in the host directory mapped to Frigate's `/config` directory (commonly `/mnt/user/appdata/frigate/config.yml` on Unraid).
2. Find the existing camera under the top-level `cameras:` section.
3. Add `onvif:` inside that camera, at the same indentation level as `ffmpeg:`, `detect:`, `objects:`, and `zones:`. Do not add a second copy of the camera name and do not put `onvif:` inside `ffmpeg:`.
4. Set `host` to the Unraid server's LAN address, not the camera's address. Set `port` to the **ONVIF host port** chosen for this bridge container.

The following example uses the documentation-only address `192.0.2.10`; replace it with your Unraid server address. Keep the camera's existing stream and detection keys unchanged.

```yaml
cameras:
  patio_camera:
    # Existing ffmpeg, detect, objects, zones, and other settings stay here.

    # Add this block to this existing camera definition.
    onvif:
      host: 192.0.2.10  # Replace with the Unraid server LAN address.
      port: 18880       # Match this bridge instance's ONVIF host port.
      user: ""
      password: ""
```

The bridge does not require ONVIF authentication, but Frigate may require the explicit empty `user` and `password` values. Validate the configuration, then use **Save & Restart**. Open that camera's Live page after Frigate restarts; the PTZ controls should be available there.

For additional cameras, install one bridge instance per camera and assign each instance a unique host port (`18880`, `18881`, and so on). Add the corresponding `onvif:` block under each Frigate camera. Do not point Frigate at the camera's DVRIP port `37777`; that connection is made privately by the bridge.

If the controls do not appear, open `http://UNRAID_SERVER_IP:ONVIF_HOST_PORT/health` from your LAN and confirm that it reports healthy, then check the Frigate logs for ONVIF connection errors. This bridge implements basic continuous pan/tilt and stop commands; it does not add optical zoom, presets, or Frigate autotracking support.

For Frigate's complete ONVIF settings, see the [official camera configuration documentation](https://docs.frigate.video/configuration/cameras/#setting-up-camera-ptz-controls).

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

At runtime the application listens for ONVIF requests, connects to the configured camera over local TCP port `37777`, and checks its own localhost health endpoint. It contains no telemetry, advertising, or cloud integration. The image explicitly disables the optional update checker inherited from the pwntools dependency, so it does not perform a runtime version check.

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

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python scripts/validate_repository.py
docker build -t amcrest-ptz-bridge:dev .
```

## Security and credentials

The published app template ships with a blank camera password field and contains no developer camera address, name, username, or credential. Each installer must provide credentials for their own camera.

After installation, Unraid stores the value locally in that server's per-container DockerMan template and Docker configuration so the existing container can be edited or recreated. Depending on the Unraid version, an administrator may be able to view that local value on the Edit page or through Docker inspection. It is never uploaded to this repository by the app. For file-based secret handling, set `CAMERA_PASSWORD_FILE` and mount a read-only secret file instead of using `CAMERA_PASSWORD`.

The container runs as an unprivileged user, drops Linux capabilities in the Unraid template, uses a read-only root filesystem, and does not require access to Unraid shares or the Docker socket.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License and attribution

Bridge code is MIT licensed. The image uses a pinned revision of the MIT-licensed [mcw0/DahuaConsole](https://github.com/mcw0/DahuaConsole) project for DVRIP connectivity. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This project is not affiliated with or endorsed by Amcrest, Dahua, Frigate, or Unraid.
