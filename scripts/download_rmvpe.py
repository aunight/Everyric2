#!/usr/bin/env python3
"""Download the RMVPE weights used by Everyric2 and verify their checksum."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Protocol

import requests

RMVPE_URL = (
    "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/"
    "rmvpe.pt?download=true"
)
RMVPE_SHA256 = "6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193"
DEFAULT_DESTINATION = Path(__file__).resolve().parents[1] / "models" / "rmvpe" / "rmvpe.pt"


class DownloadResponse(Protocol):
    def __enter__(self) -> DownloadResponse: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def raise_for_status(self) -> None: ...

    def iter_content(self, chunk_size: int) -> Iterator[bytes]: ...


class DownloadSession(Protocol):
    def get(
        self,
        url: str,
        *,
        stream: bool,
        timeout: tuple[int, int],
    ) -> DownloadResponse: ...


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_rmvpe(
    destination: Path = DEFAULT_DESTINATION,
    *,
    url: str = RMVPE_URL,
    expected_sha256: str = RMVPE_SHA256,
    session: DownloadSession | None = None,
    force: bool = False,
) -> Path:
    """Install verified RMVPE weights without exposing a partial destination file."""
    destination = Path(destination)
    expected_sha256 = expected_sha256.lower()
    if destination.is_file() and not force:
        if file_sha256(destination) == expected_sha256:
            return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    client = session or requests
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".rmvpe-",
            suffix=".part",
            dir=destination.parent,
            delete=False,
        ) as output:
            temp_path = Path(output.name)
            digest = hashlib.sha256()
            with client.get(url, stream=True, timeout=(15, 300)) as response:
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output.write(chunk)
                    digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())

        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                "RMVPE SHA-256 mismatch: "
                f"expected {expected_sha256}, received {actual_sha256}"
            )
        os.replace(temp_path, destination)
        temp_path = None
        return destination
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and verify Everyric2's RMVPE model weights.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DESTINATION,
        help=f"Destination path (default: {DEFAULT_DESTINATION})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Download again even when the existing file has the expected checksum.",
    )
    args = parser.parse_args()
    path = download_rmvpe(args.output, force=args.force)
    print(f"RMVPE ready: {path}")
    print(f"SHA-256: {file_sha256(path)}")


if __name__ == "__main__":
    main()
