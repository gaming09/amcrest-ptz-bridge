# Security policy

## Supported versions

Security fixes are applied to the current `latest` image and the newest tagged release.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for `gaming09/amcrest-ptz-bridge`. Do not include camera passwords, public camera addresses, packet captures containing credentials, or other secrets in a public issue.

For ordinary bugs and compatibility reports, use the repository issue tracker.

## Security model

- The bridge is intended for trusted local networks.
- The published ONVIF port has no separate authentication layer; use the optional IP allowlist and firewall controls when the LAN is not fully trusted.
- Camera credentials are required for local DVRIP authentication and are never intentionally logged.
- The application does not proxy video, mount Unraid shares, require privileged mode, or access the Docker socket.
- Runtime code does not intentionally contact internet services.
