import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


sys.modules.setdefault("dahua", types.SimpleNamespace(DahuaFunctions=object))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge import (  # noqa: E402
    DVRIPController,
    config_from_env,
    direction_from_velocity,
    external_base_url,
)


class DirectionTests(unittest.TestCase):
    def test_cardinal_directions(self):
        self.assertEqual(direction_from_velocity(1, 0), "Right")
        self.assertEqual(direction_from_velocity(-1, 0), "Left")
        self.assertEqual(direction_from_velocity(0, 1), "Up")
        self.assertEqual(direction_from_velocity(0, -1), "Down")

    def test_diagonal_and_deadzone(self):
        self.assertEqual(direction_from_velocity(0.5, 0.5), "RightUp")
        self.assertEqual(direction_from_velocity(-0.5, -0.5), "LeftDown")
        self.assertIsNone(direction_from_velocity(0.01, -0.01))


class DVRIPParameterTests(unittest.TestCase):
    def test_pan_speed_uses_arg1(self):
        self.assertEqual(
            DVRIPController._params("Left", 4, 0),
            {"channel": 0, "code": "Left", "arg1": 4, "arg2": 0, "arg3": 0},
        )

    def test_tilt_speed_uses_arg2(self):
        self.assertEqual(
            DVRIPController._params("Up", 4, 0),
            {"channel": 0, "code": "Up", "arg1": 0, "arg2": 4, "arg3": 0},
        )

    def test_diagonal_speed_uses_both_axes(self):
        self.assertEqual(
            DVRIPController._params("RightDown", 4, 0),
            {"channel": 0, "code": "RightDown", "arg1": 4, "arg2": 4, "arg3": 0},
        )


class EnvironmentConfigTests(unittest.TestCase):
    def base_env(self):
        return {
            "CAMERA_HOST": "192.0.2.25",
            "CAMERA_PASSWORD": "secret",
        }

    def test_defaults_and_overrides(self):
        environ = self.base_env() | {
            "CAMERA_NAME": "living-room",
            "CAMERA_USERNAME": "viewer",
            "CAMERA_PORT": "37778",
            "MAX_MOVE_SECONDS": "0.8",
            "ALLOWED_CLIENTS": "192.0.2.10/32, 172.16.0.0/12",
        }
        config = config_from_env(environ)
        camera = config["cameras"][0]
        self.assertEqual(camera["name"], "living-room")
        self.assertEqual(camera["listen_port"], 18880)
        self.assertEqual(camera["username"], "viewer")
        self.assertEqual(camera["dvrip_port"], 37778)
        self.assertEqual(camera["password"], "secret")
        self.assertEqual(camera["max_move_seconds"], 0.8)
        self.assertEqual(
            config["allowed_clients"],
            ["192.0.2.10/32", "172.16.0.0/12"],
        )

    def test_password_file_takes_precedence(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write("file-secret\n")
            password_path = handle.name
        try:
            config = config_from_env(
                self.base_env()
                | {
                    "CAMERA_PASSWORD": "environment-secret",
                    "CAMERA_PASSWORD_FILE": password_path,
                }
            )
            self.assertEqual(config["cameras"][0]["password"], "file-secret")
        finally:
            os.unlink(password_path)

    def test_required_camera_host(self):
        with self.assertRaisesRegex(ValueError, "CAMERA_HOST"):
            config_from_env({"CAMERA_PASSWORD": "secret"})

    def test_required_camera_password(self):
        with self.assertRaisesRegex(ValueError, "CAMERA_PASSWORD"):
            config_from_env({"CAMERA_HOST": "192.0.2.25"})


class ExternalAddressTests(unittest.TestCase):
    def test_request_host_supports_docker_port_mapping(self):
        self.assertEqual(
            external_base_url("192.0.2.10:18881", "http://127.0.0.1:18880"),
            "http://192.0.2.10:18881",
        )

    def test_invalid_host_falls_back(self):
        fallback = "http://127.0.0.1:18880"
        self.assertEqual(external_base_url("bad/host", fallback), fallback)


if __name__ == "__main__":
    unittest.main()
