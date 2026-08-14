# Third-party notices

## DahuaConsole

- Project: <https://github.com/mcw0/DahuaConsole>
- Pinned revision: `5711bc865e8831c2297ea19f719c69bdaa9e9fd3`
- License: MIT
- Use: authenticated local DVRIP connection and JSON-RPC transport

The Docker build fetches this exact revision and verifies the checked-out commit before copying it into the runtime image. DahuaConsole's license file remains at `/opt/dahua/LICENSE` in the built image.

The upstream project includes capabilities beyond those used by this bridge. Amcrest PTZ Bridge invokes only login, service-factory, `ptz.start`, `ptz.stop`, logout, and transport methods.
