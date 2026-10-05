#!/usr/bin/env python3
"""Maintainer tool for MCA support data transport files (not packaged).

    mca_support_key.py genkey
        Creates a key pair. Store the private key in your password manager;
        put the public key into lib/transport.py (MAINTAINER_PUBLIC_KEY).

    mca_support_key.py decrypt FILE.json.enc [OUTPUT.json]
        Decrypts a transport file. The private key is read from the
        environment variable MCA_SUPPORT_KEY or asked for (hidden input).

Needs the Python package "cryptography".
"""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import sys

_TRANSPORT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "local", "lib", "python3", "cmk_addons",
    "plugins", "monitoring_coverage_analyzer", "lib", "transport.py",
)


def _transport():
    spec = importlib.util.spec_from_file_location("mca_transport", _TRANSPORT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    t = _transport()
    if argv[:1] == ["genkey"]:
        private, public = t.generate_keypair()
        print("Private key (store it in your password manager, never commit it):")
        print(f"  {private}")
        print("Public key (lib/transport.py, MAINTAINER_PUBLIC_KEY):")
        print(f"  {public}")
        return 0
    if argv[:1] == ["decrypt"] and len(argv) in (2, 3):
        key = os.environ.get("MCA_SUPPORT_KEY") or getpass.getpass("Private key: ")
        with open(argv[1], "rb") as handle:
            blob = handle.read()
        try:
            doc = t.decrypt(blob, key)
        except t.TransportError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        out = argv[2] if len(argv) == 3 else argv[1].removesuffix(".enc")
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, indent=1, sort_keys=True)
            handle.write("\n")
        print(f"Written: {out}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
