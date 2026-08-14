#!/usr/bin/env python3
"""Local checks aligned with the public Unraid Community Apps starter guide."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "amcrest-ptz-bridge.xml"
PROFILE = ROOT / "ca_profile.xml"
PLACEHOLDERS = ("GITHUB_OWNER", "YOUR_GITHUB", "YOUR_REPO", "YOUR_SUPPORT")
PLACEHOLDER_SOURCE_FILES = {
    Path("scripts/validate_repository.py"),
}


def text_of(root: ET.Element, tag: str) -> str:
    node = root.find(tag)
    return "" if node is None or node.text is None else node.text.strip()


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--submission",
        action="store_true",
        help="also reject repository identity placeholders",
    )
    args = parser.parse_args()
    errors: list[str] = []

    for required in (
        ROOT / "README.md",
        ROOT / "LICENSE",
        ROOT / "icon.png",
        ROOT / "ca_profile.xml",
        ROOT / "Dockerfile",
        ROOT / "pwn.conf",
        TEMPLATE,
    ):
        require(required.is_file(), f"missing required file: {required.relative_to(ROOT)}", errors)

    try:
        template_root = ET.parse(TEMPLATE).getroot()
    except (ET.ParseError, OSError) as exc:
        errors.append(f"invalid template XML: {exc}")
        template_root = ET.Element("invalid")

    require(template_root.tag == "Container", "template root must be Container", errors)
    require(template_root.get("version") == "2", "template must use Container version 2", errors)
    for tag in ("Name", "Repository", "Overview", "Project", "Support", "TemplateURL"):
        require(bool(text_of(template_root, tag)), f"template requires non-empty <{tag}>", errors)

    repository = text_of(template_root, "Repository")
    require(repository.startswith("ghcr.io/"), "Repository must point to GHCR", errors)
    require(repository.endswith(":latest"), "Repository should use the latest tag", errors)
    require(text_of(template_root, "Network") == "bridge", "published app must use bridge networking", errors)
    require(text_of(template_root, "Privileged").lower() == "false", "Privileged must be false", errors)
    require(text_of(template_root, "License") == "MIT", "template License must be MIT", errors)

    configs = template_root.findall("Config")
    targets = [config.get("Target", "") for config in configs]
    require(len(targets) == len(set(targets)), "Config Target values must be unique", errors)
    for target in ("18880", "CAMERA_HOST", "CAMERA_USERNAME", "CAMERA_PASSWORD"):
        require(target in targets, f"template is missing Config target {target}", errors)
    password_nodes = [node for node in configs if node.get("Target") == "CAMERA_PASSWORD"]
    require(
        bool(password_nodes) and password_nodes[0].get("Mask") == "true",
        "camera password Config must be masked",
        errors,
    )
    if password_nodes:
        password_node = password_nodes[0]
        require(
            not (password_node.text or "").strip(),
            "public template must not prefill a camera password",
            errors,
        )
        require(
            not password_node.get("Default", "").strip(),
            "public template camera password Default must be empty",
            errors,
        )

    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.suffix in {".pyc", ".png", ".jpg"}:
            continue
        if path.relative_to(ROOT) in PLACEHOLDER_SOURCE_FILES:
            continue
        try:
            contents = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        require(
            "192.168." not in contents,
            f"private LAN address found in public file {path.relative_to(ROOT)}; use an RFC 5737 documentation address",
            errors,
        )

    try:
        profile_root = ET.parse(PROFILE).getroot()
    except (ET.ParseError, OSError) as exc:
        errors.append(f"invalid ca_profile.xml: {exc}")
        profile_root = ET.Element("invalid")
    require(profile_root.tag == "CommunityApplications", "profile root must be CommunityApplications", errors)
    require(bool(text_of(profile_root, "Profile")), "ca_profile.xml requires a non-empty Profile", errors)

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for instruction in (
        "USER 10001:10001",
        "HEALTHCHECK",
        "DAHUA_CONSOLE_COMMIT",
        "COPY pwn.conf /etc/pwn.conf",
    ):
        require(instruction in dockerfile, f"Dockerfile is missing {instruction}", errors)

    pwn_config = (ROOT / "pwn.conf").read_text(encoding="utf-8")
    require("[update]" in pwn_config, "pwn.conf must configure the update section", errors)
    require("interval=never" in pwn_config, "pwn.conf must disable pwntools update checks", errors)

    date = text_of(template_root, "Date")
    require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)), "template Date must be YYYY-MM-DD", errors)

    if args.submission:
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or path.suffix in {".pyc", ".png", ".jpg"}:
                continue
            if path.relative_to(ROOT) in PLACEHOLDER_SOURCE_FILES:
                continue
            try:
                contents = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for placeholder in PLACEHOLDERS:
                require(
                    placeholder not in contents,
                    f"submission placeholder {placeholder!r} remains in {path.relative_to(ROOT)}",
                    errors,
                )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    mode = "submission" if args.submission else "source-pack"
    print(f"repository validation passed ({mode} mode)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
