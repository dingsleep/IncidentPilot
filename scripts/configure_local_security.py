from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def configure_local_security(path: Path) -> list[str]:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    names = {
        line.split("=", 1)[0]
        for line in existing.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    approval_private, approval_public = _keypair()
    telemetry_private, telemetry_public = _keypair()
    values = {
        "INCIDENTPILOT_ACTION_ENABLED": "true",
        "INCIDENTPILOT_ACTION_MCP_URL": "http://action-mcp:8102/mcp",
        "INCIDENTPILOT_ACTION_APPROVAL_SIGNING_KEY": approval_private,
        "INCIDENTPILOT_APPROVAL_VERIFYING_KEY": approval_public,
        "INCIDENTPILOT_PRIVATE_MAPPING_ENCRYPTION_KEY": base64.urlsafe_b64encode(
            AESGCM.generate_key(bit_length=256)
        ).decode(),
        "INCIDENTPILOT_TELEMETRY_SIGNING_KEY": telemetry_private,
        "INCIDENTPILOT_TELEMETRY_VERIFYING_KEY": telemetry_public,
    }
    added = [name for name in values if name not in names]
    if not added:
        return []
    prefix = existing.rstrip("\r\n")
    lines = [f"{name}={values[name]}" for name in added]
    content = f"{prefix}\n\n# Local process-scoped security material\n" if prefix else ""
    content += "\n".join(lines) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return added


def _keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    public_pem = private.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return _single_line(private_pem), _single_line(public_pem)


def _single_line(value: str) -> str:
    return value.rstrip("\n").replace("\n", "\\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create missing local-only Telemetry and Action security material"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    added = configure_local_security(args.env_file)
    print("Configured: " + (", ".join(added) if added else "nothing; already configured"))


if __name__ == "__main__":
    main()
