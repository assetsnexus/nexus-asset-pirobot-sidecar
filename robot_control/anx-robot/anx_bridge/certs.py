"""Optional lab helper: create a local CA + broker cert (not used by the ipc join).

The sidecar example joins asset-node-ipc-docker Mosquitto and mounts that CA as
MQTT_CA_FILE. Do not run this to invent a second broker for the public example.
Kept for offline lab experiments only.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


class CertError(RuntimeError):
    pass


def ensure_certs(directory: str | os.PathLike[str]) -> dict[str, str]:
    dest = Path(directory)
    dest.mkdir(parents=True, exist_ok=True)
    ca_key = dest / "ca.key"
    ca_crt = dest / "ca.crt"
    broker_key = dest / "broker.key"
    broker_crt = dest / "broker.crt"
    if not (ca_crt.is_file() and broker_crt.is_file() and broker_key.is_file()):
        _generate(dest, ca_key, ca_crt, broker_key, broker_crt)
    for path in (ca_key, broker_key):
        if path.is_file():
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return {
        "ca": str(ca_crt),
        "ca_key": str(ca_key),
        "broker_crt": str(broker_crt),
        "broker_key": str(broker_key),
    }


def _generate(dest: Path, ca_key: Path, ca_crt: Path, broker_key: Path, broker_crt: Path) -> None:
    if not _have_openssl():
        raise CertError("openssl is required to generate the MQTT CA")
    ext = dest / "broker.ext"
    ext.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n", encoding="utf-8")
    csr = dest / "broker.csr"
    _run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
            "-keyout", str(ca_key), "-out", str(ca_crt), "-subj", "/CN=anx-robot-mqtt-ca",
        ]
    )
    _run(
        [
            "openssl", "req", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(broker_key), "-out", str(csr), "-subj", "/CN=localhost",
        ]
    )
    _run(
        [
            "openssl", "x509", "-req", "-in", str(csr),
            "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
            "-out", str(broker_crt), "-days", "825", "-extfile", str(ext),
        ]
    )
    csr.unlink(missing_ok=True)
    ext.unlink(missing_ok=True)


def _have_openssl() -> bool:
    from shutil import which

    return which("openssl") is not None


def _run(argv: list[str]) -> None:
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CertError(proc.stderr.strip() or f"openssl failed: {argv[1]}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2] / "certs"
    paths = ensure_certs(root)
    print(f"MQTT CA: {paths['ca']}")
