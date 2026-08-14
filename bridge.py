#!/usr/bin/env python3
"""Small ONVIF PT facade for Amcrest SmartHome cameras using Dahua DVRIP."""

from __future__ import annotations

import ipaddress
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import yaml

sys.path.insert(0, "/opt/dahua")
from dahua import DahuaFunctions  # noqa: E402


LOG = logging.getLogger("amcrest-ptz-bridge")

SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
TDS = "http://www.onvif.org/ver10/device/wsdl"
TRT = "http://www.onvif.org/ver10/media/wsdl"
TPTZ = "http://www.onvif.org/ver20/ptz/wsdl"
TT = "http://www.onvif.org/ver10/schema"

for prefix, uri in {
    "s": SOAP_ENV,
    "tds": TDS,
    "trt": TRT,
    "tptz": TPTZ,
    "tt": TT,
}.items():
    ET.register_namespace(prefix, uri)


def q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def envelope(response_namespace: str, response_name: str) -> tuple[ET.Element, ET.Element]:
    root = ET.Element(q(SOAP_ENV, "Envelope"))
    body = ET.SubElement(root, q(SOAP_ENV, "Body"))
    response = ET.SubElement(body, q(response_namespace, response_name))
    return root, response


def child(parent: ET.Element, namespace: str, name: str, text: object | None = None, **attrs: object) -> ET.Element:
    element = ET.SubElement(parent, q(namespace, name), {key: str(value) for key, value in attrs.items()})
    if text is not None:
        element.text = str(text)
    return element


def soap_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def direction_from_velocity(x: float, y: float) -> str | None:
    deadzone = 0.05
    horizontal = "Right" if x > deadzone else "Left" if x < -deadzone else ""
    vertical = "Up" if y > deadzone else "Down" if y < -deadzone else ""
    return f"{horizontal}{vertical}" or None


def external_base_url(host_header: str | None, fallback: str) -> str:
    """Use the request Host header so ONVIF works behind a Docker port mapping."""
    host = (host_header or "").strip()
    if host and "/" not in host and "\\" not in host and not any(char.isspace() for char in host):
        return f"http://{host}"
    return fallback


class DVRIPController:
    """Thread-safe persistent DVRIP connection with reconnect and stop fail-safe."""

    def __init__(self, camera: dict):
        self.name = camera["name"]
        self.host = camera["host"]
        self.port = int(camera.get("dvrip_port", 37777))
        self.username = camera["username"]
        self.password = camera.get("password")
        if self.password is None and camera.get("password_env"):
            password_env = camera["password_env"]
            self.password = os.environ.get(password_env)
            if self.password is None:
                raise ValueError(f"{self.name}: environment variable {password_env} is not set")
        if self.password is None:
            raise ValueError(f"{self.name}: camera password is not configured")
        self.channel = int(camera.get("channel", 0))
        self.max_move_seconds = float(camera.get("max_move_seconds", 1.25))
        self._lock = threading.RLock()
        self._client: DahuaFunctions | None = None
        self._object_id: int | None = None
        self._active_code: str | None = None
        self._timer: threading.Timer | None = None

    def _args(self) -> SimpleNamespace:
        return SimpleNamespace(
            debug=0,
            calls=False,
            test=False,
            dump=True,
            save=False,
            force=False,
            events=False,
            ssl=False,
        )

    def _disconnect(self) -> None:
        client, self._client = self._client, None
        self._object_id = None
        if client is not None:
            try:
                client.logout()
            except Exception:
                try:
                    client.remote.close()
                except Exception:
                    pass

    def _connect(self) -> None:
        if self._client is not None and self._object_id is not None:
            return
        client = DahuaFunctions(
            rhost=self.host,
            rport=self.port,
            proto="dvrip",
            events=False,
            ssl=False,
            relay_host=None,
            timeout=5,
            udp_server=None,
            dargs=self._args(),
        )
        if not client.dh_connect(
            username=self.username,
            password=self.password,
            logon="default",
        ):
            raise ConnectionError(f"{self.name}: DVRIP login failed")
        factory = client.send_call(
            {
                "method": "ptz.factory.instance",
                "params": {"channel": self.channel},
            },
            errorcodes=True,
        )
        if not isinstance(factory, dict) or not isinstance(factory.get("result"), int):
            try:
                client.logout()
            finally:
                raise ConnectionError(f"{self.name}: PTZ factory unavailable: {factory!r}")
        self._client = client
        self._object_id = factory["result"]
        LOG.info("%s connected locally over DVRIP to %s:%s", self.name, self.host, self.port)

    def _rpc(self, method: str, params: dict) -> None:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                self._connect()
                response = self._client.send_call(
                    {
                        "method": method,
                        "params": params,
                        "object": self._object_id,
                    },
                    errorcodes=True,
                )
                if not isinstance(response, dict) or response.get("result") is not True:
                    raise ConnectionError(f"RPC {method} failed: {response!r}")
                return
            except Exception as exc:
                last_error = exc
                self._disconnect()
                if attempt == 0:
                    LOG.warning("%s reconnecting after %s", self.name, exc)
        raise ConnectionError(str(last_error))

    @staticmethod
    def _params(code: str, speed: int, channel: int) -> dict:
        pan_speed = speed if "Left" in code or "Right" in code else 0
        tilt_speed = speed if "Up" in code or "Down" in code else 0
        return {
            "channel": channel,
            "code": code,
            "arg1": pan_speed,
            "arg2": tilt_speed,
            "arg3": 0,
        }

    def move(self, x: float, y: float) -> None:
        code = direction_from_velocity(x, y)
        if code is None:
            self.stop()
            return
        speed = max(1, min(8, round(max(abs(x), abs(y)) * 8)))
        with self._lock:
            if self._active_code:
                self._stop_locked()
            self._rpc("ptz.start", self._params(code, speed, self.channel))
            self._active_code = code
            self._timer = threading.Timer(self.max_move_seconds, self.stop)
            self._timer.daemon = True
            self._timer.start()
            LOG.info("%s move %s speed %s", self.name, code, speed)

    def _stop_locked(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        code = self._active_code
        if code is None:
            return
        self._rpc("ptz.stop", self._params(code, 1, self.channel))
        self._active_code = None
        LOG.info("%s stopped", self.name)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def close(self) -> None:
        with self._lock:
            try:
                self._stop_locked()
            finally:
                self._disconnect()


class CameraServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], camera: dict, global_config: dict):
        self.camera = camera
        self.controller = DVRIPController(camera)
        self.advertise_host = camera.get("advertise_host", global_config["advertise_host"])
        self.allowed_clients = [
            ipaddress.ip_network(value, strict=False)
            for value in camera.get("allowed_clients", global_config.get("allowed_clients", []))
        ]
        super().__init__(address, ONVIFHandler)

    def client_allowed(self, value: str) -> bool:
        address = ipaddress.ip_address(value)
        if address.is_loopback or not self.allowed_clients:
            return True
        return any(address in network for network in self.allowed_clients)

    @property
    def base_url(self) -> str:
        return f"http://{self.advertise_host}:{self.server_port}"


class ONVIFHandler(BaseHTTPRequestHandler):
    server: CameraServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.debug("%s %s", self.client_address[0], fmt % args)

    def _send(self, status: int, payload: bytes, content_type: str = "application/soap+xml; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if not self.server.client_allowed(self.client_address[0]):
            self._send(403, b"forbidden", "text/plain")
            return
        if urlparse(self.path).path == "/health":
            self._send(200, b"ok\n", "text/plain")
        else:
            self._send(404, b"not found\n", "text/plain")

    def do_POST(self) -> None:
        if not self.server.client_allowed(self.client_address[0]):
            self._send(403, b"forbidden", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            root = ET.fromstring(self.rfile.read(length))
            body = root.find(q(SOAP_ENV, "Body"))
            if body is None or len(body) == 0:
                raise ValueError("missing SOAP body")
            request = body[0]
            action = local_name(request.tag)
            payload = self.dispatch(action, request)
            self._send(200, payload)
        except Exception as exc:
            LOG.exception("%s request failed", self.server.camera["name"])
            fault, body = envelope(SOAP_ENV, "Fault")
            code = child(body, SOAP_ENV, "Code")
            child(code, SOAP_ENV, "Value", "s:Receiver")
            reason = child(body, SOAP_ENV, "Reason")
            child(reason, SOAP_ENV, "Text", str(exc), **{"xml:lang": "en"})
            self._send(500, soap_bytes(fault))

    def dispatch(self, action: str, request: ET.Element) -> bytes:
        handler = getattr(self, f"on_{action}", None)
        if handler is None:
            raise NotImplementedError(action)
        return handler(request)

    @property
    def request_base_url(self) -> str:
        return external_base_url(self.headers.get("Host"), self.server.base_url)

    def on_GetCapabilities(self, _request: ET.Element) -> bytes:
        root, response = envelope(TDS, "GetCapabilitiesResponse")
        capabilities = child(response, TDS, "Capabilities")
        device = child(capabilities, TT, "Device")
        child(device, TT, "XAddr", f"{self.request_base_url}/onvif/device_service")
        media = child(capabilities, TT, "Media")
        child(media, TT, "XAddr", f"{self.request_base_url}/onvif/media_service")
        ptz = child(capabilities, TT, "PTZ")
        child(ptz, TT, "XAddr", f"{self.request_base_url}/onvif/ptz_service")
        return soap_bytes(root)

    def on_GetServices(self, _request: ET.Element) -> bytes:
        root, response = envelope(TDS, "GetServicesResponse")
        for namespace, path in ((TDS, "device_service"), (TRT, "media_service"), (TPTZ, "ptz_service")):
            service = child(response, TDS, "Service")
            child(service, TDS, "Namespace", namespace)
            child(service, TDS, "XAddr", f"{self.request_base_url}/onvif/{path}")
            version = child(service, TDS, "Version")
            child(version, TT, "Major", 2)
            child(version, TT, "Minor", 0)
        return soap_bytes(root)

    def on_GetDeviceInformation(self, _request: ET.Element) -> bytes:
        root, response = envelope(TDS, "GetDeviceInformationResponse")
        child(response, TDS, "Manufacturer", "Local Amcrest bridge")
        child(response, TDS, "Model", self.server.camera.get("model", "Amcrest SmartHome PTZ"))
        child(response, TDS, "FirmwareVersion", "DVRIP local control")
        child(response, TDS, "SerialNumber", self.server.camera["name"])
        child(response, TDS, "HardwareId", "amcrest-ptz-bridge")
        return soap_bytes(root)

    def on_GetSystemDateAndTime(self, _request: ET.Element) -> bytes:
        now = time.gmtime()
        root, response = envelope(TDS, "GetSystemDateAndTimeResponse")
        system = child(response, TDS, "SystemDateAndTime")
        child(system, TT, "DateTimeType", "NTP")
        child(system, TT, "DaylightSavings", "false")
        utc = child(system, TT, "UTCDateTime")
        tm = child(utc, TT, "Time")
        child(tm, TT, "Hour", now.tm_hour)
        child(tm, TT, "Minute", now.tm_min)
        child(tm, TT, "Second", now.tm_sec)
        date = child(utc, TT, "Date")
        child(date, TT, "Year", now.tm_year)
        child(date, TT, "Month", now.tm_mon)
        child(date, TT, "Day", now.tm_mday)
        return soap_bytes(root)

    def on_GetProfiles(self, _request: ET.Element) -> bytes:
        root, response = envelope(TRT, "GetProfilesResponse")
        profile = child(response, TRT, "Profiles", token="profile_1", fixed="true")
        child(profile, TT, "Name", self.server.camera["name"])
        source = child(profile, TT, "VideoSourceConfiguration", token="video_source_config_1")
        child(source, TT, "Name", "VideoSource")
        child(source, TT, "UseCount", 1)
        child(source, TT, "SourceToken", "video_source_1")
        child(source, TT, "Bounds", x=0, y=0, width=1920, height=1080)
        encoder = child(profile, TT, "VideoEncoderConfiguration", token="video_encoder_1")
        child(encoder, TT, "Name", "H265 main")
        child(encoder, TT, "UseCount", 1)
        child(encoder, TT, "Encoding", "H265")
        resolution = child(encoder, TT, "Resolution")
        child(resolution, TT, "Width", 1920)
        child(resolution, TT, "Height", 1080)
        child(encoder, TT, "Quality", 5)
        rate = child(encoder, TT, "RateControl")
        child(rate, TT, "FrameRateLimit", 30)
        child(rate, TT, "EncodingInterval", 1)
        child(rate, TT, "BitrateLimit", 1024)
        ptz = child(profile, TT, "PTZConfiguration", token="ptz_config_1")
        child(ptz, TT, "Name", "PanTilt")
        child(ptz, TT, "UseCount", 1)
        child(ptz, TT, "NodeToken", "ptz_node_1")
        velocity_uri = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"
        child(ptz, TT, "DefaultContinuousPanTiltVelocitySpace", velocity_uri)
        speed = child(ptz, TT, "DefaultPTZSpeed")
        child(speed, TT, "PanTilt", x=0.5, y=0.5, space=velocity_uri)
        child(ptz, TT, "DefaultPTZTimeout", "PT1S")
        return soap_bytes(root)

    def on_GetVideoSources(self, _request: ET.Element) -> bytes:
        root, response = envelope(TRT, "GetVideoSourcesResponse")
        source = child(response, TRT, "VideoSources", token="video_source_1")
        child(source, TT, "Framerate", 30)
        resolution = child(source, TT, "Resolution")
        child(resolution, TT, "Width", 1920)
        child(resolution, TT, "Height", 1080)
        return soap_bytes(root)

    def on_GetStreamUri(self, _request: ET.Element) -> bytes:
        root, response = envelope(TRT, "GetStreamUriResponse")
        uri = child(response, TRT, "MediaUri")
        rtsp = self.server.camera.get(
            "rtsp_uri",
            f"rtsp://{self.server.camera['host']}:554/cam/realmonitor?channel=1&subtype=0",
        )
        child(uri, TT, "Uri", rtsp)
        child(uri, TT, "InvalidAfterConnect", "false")
        child(uri, TT, "InvalidAfterReboot", "false")
        child(uri, TT, "Timeout", "PT60S")
        return soap_bytes(root)

    def on_GetServiceCapabilities(self, _request: ET.Element) -> bytes:
        root, response = envelope(TPTZ, "GetServiceCapabilitiesResponse")
        child(response, TPTZ, "Capabilities", EFlip="false", Reverse="false", GetCompatibleConfigurations="true")
        return soap_bytes(root)

    def on_GetConfigurations(self, _request: ET.Element) -> bytes:
        root, response = envelope(TPTZ, "GetConfigurationsResponse")
        config = child(response, TPTZ, "PTZConfiguration", token="ptz_config_1")
        child(config, TT, "Name", "PanTilt")
        child(config, TT, "UseCount", 1)
        child(config, TT, "NodeToken", "ptz_node_1")
        child(config, TT, "DefaultContinuousPanTiltVelocitySpace", "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace")
        return soap_bytes(root)

    def on_GetConfigurationOptions(self, _request: ET.Element) -> bytes:
        root, response = envelope(TPTZ, "GetConfigurationOptionsResponse")
        spaces = child(response, TPTZ, "PTZConfigurationOptions")
        supported = child(spaces, TT, "Spaces")
        velocity = child(supported, TT, "ContinuousPanTiltVelocitySpace")
        child(velocity, TT, "URI", "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace")
        xr = child(velocity, TT, "XRange")
        child(xr, TT, "Min", -1)
        child(xr, TT, "Max", 1)
        yr = child(velocity, TT, "YRange")
        child(yr, TT, "Min", -1)
        child(yr, TT, "Max", 1)
        child(spaces, TT, "PTZTimeout", Min="PT0.1S", Max="PT2S")
        return soap_bytes(root)

    def on_GetPresets(self, _request: ET.Element) -> bytes:
        root, _response = envelope(TPTZ, "GetPresetsResponse")
        return soap_bytes(root)

    def on_GetStatus(self, _request: ET.Element) -> bytes:
        root, response = envelope(TPTZ, "GetStatusResponse")
        status = child(response, TPTZ, "PTZStatus")
        position = child(status, TT, "Position")
        child(position, TT, "PanTilt", x=0, y=0)
        move = child(status, TT, "MoveStatus")
        child(move, TT, "PanTilt", "IDLE")
        child(status, TT, "UtcTime", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return soap_bytes(root)

    def on_ContinuousMove(self, request: ET.Element) -> bytes:
        pan_tilt = request.find(f".//{q(TT, 'PanTilt')}")
        x = float(pan_tilt.get("x", "0")) if pan_tilt is not None else 0.0
        y = float(pan_tilt.get("y", "0")) if pan_tilt is not None else 0.0
        self.server.controller.move(x, y)
        root, _response = envelope(TPTZ, "ContinuousMoveResponse")
        return soap_bytes(root)

    def on_Stop(self, _request: ET.Element) -> bytes:
        self.server.controller.stop()
        root, _response = envelope(TPTZ, "StopResponse")
        return soap_bytes(root)


def validate_config(config: object) -> dict:
    if not isinstance(config, dict) or not config.get("cameras"):
        raise ValueError("config must contain at least one camera")
    if not config.get("advertise_host"):
        raise ValueError("config.advertise_host is required")
    return config


def load_yaml_config(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    return validate_config(config)


def _required_env(environ: dict[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ValueError(f"environment variable {name} is required")
    return value


def _password_from_env(environ: dict[str, str]) -> str:
    password_file = environ.get("CAMERA_PASSWORD_FILE", "").strip()
    if password_file:
        with open(password_file, encoding="utf-8") as handle:
            password = handle.read().rstrip("\r\n")
        if not password:
            raise ValueError("CAMERA_PASSWORD_FILE is empty")
        return password
    return _required_env(environ, "CAMERA_PASSWORD")


def config_from_env(environ: dict[str, str] | None = None) -> dict:
    environ = dict(os.environ if environ is None else environ)
    allowed = [
        item.strip()
        for item in environ.get("ALLOWED_CLIENTS", "").split(",")
        if item.strip()
    ]
    config = {
        "bind": environ.get("BIND_ADDRESS", "0.0.0.0").strip() or "0.0.0.0",
        "advertise_host": environ.get("ADVERTISE_HOST", "127.0.0.1").strip() or "127.0.0.1",
        "allowed_clients": allowed,
        "cameras": [
            {
                "name": environ.get("CAMERA_NAME", "amcrest-camera").strip() or "amcrest-camera",
                "model": environ.get("CAMERA_MODEL", "Amcrest SmartHome PTZ").strip(),
                "listen_port": int(environ.get("LISTEN_PORT", "18880")),
                "host": _required_env(environ, "CAMERA_HOST"),
                "dvrip_port": int(environ.get("CAMERA_PORT", "37777")),
                "username": environ.get("CAMERA_USERNAME", "admin").strip() or "admin",
                "password": _password_from_env(environ),
                "channel": int(environ.get("CAMERA_CHANNEL", "0")),
                "max_move_seconds": float(environ.get("MAX_MOVE_SECONDS", "1.25")),
            }
        ],
    }
    return validate_config(config)


def load_config(path: str | None = None) -> dict:
    return load_yaml_config(path) if path else config_from_env()


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(os.environ.get("BRIDGE_CONFIG"))
    bind = config.get("bind", "0.0.0.0")
    servers: list[CameraServer] = []
    threads: list[threading.Thread] = []
    for camera in config["cameras"]:
        server = CameraServer((bind, int(camera["listen_port"])), camera, config)
        thread = threading.Thread(target=server.serve_forever, daemon=True, name=camera["name"])
        thread.start()
        servers.append(server)
        threads.append(thread)
        LOG.info(
            "%s ONVIF facade listening on %s:%s (camera %s:%s)",
            camera["name"],
            bind,
            camera["listen_port"],
            camera["host"],
            camera.get("dvrip_port", 37777),
        )

    stopping = threading.Event()

    def shutdown(_signum: int, _frame: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        for server in servers:
            server.shutdown()
            server.controller.close()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while not stopping.wait(1):
        if any(not thread.is_alive() for thread in threads):
            LOG.error("an ONVIF listener stopped unexpectedly")
            shutdown(signal.SIGTERM, None)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
