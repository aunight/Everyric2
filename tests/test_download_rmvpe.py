"""Verified RMVPE model downloader tests."""

import hashlib

import pytest

from scripts.download_rmvpe import download_rmvpe


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size: int):
        return (
            self.payload[index : index + chunk_size]
            for index in range(0, len(self.payload), chunk_size)
        )


class FakeSession:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return FakeResponse(self.payload)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_download_verifies_then_atomically_installs(tmp_path):
    payload = b"verified-rmvpe-weights"
    destination = tmp_path / "models" / "rmvpe.pt"
    session = FakeSession(payload)

    result = download_rmvpe(
        destination,
        url="https://example.invalid/rmvpe.pt",
        expected_sha256=sha256(payload),
        session=session,
    )

    assert result == destination
    assert destination.read_bytes() == payload
    assert session.calls == 1
    assert list(destination.parent.glob(".rmvpe-*.part")) == []


def test_checksum_mismatch_never_installs_partial_weights(tmp_path):
    destination = tmp_path / "rmvpe.pt"

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        download_rmvpe(
            destination,
            url="https://example.invalid/rmvpe.pt",
            expected_sha256=sha256(b"expected"),
            session=FakeSession(b"tampered"),
        )

    assert not destination.exists()
    assert list(tmp_path.glob(".rmvpe-*.part")) == []


def test_existing_verified_weights_are_reused_without_network(tmp_path):
    payload = b"already-installed"
    destination = tmp_path / "rmvpe.pt"
    destination.write_bytes(payload)

    class OfflineSession:
        @staticmethod
        def get(*_args, **_kwargs):
            raise AssertionError("network should not be used")

    result = download_rmvpe(
        destination,
        expected_sha256=sha256(payload),
        session=OfflineSession(),
    )

    assert result == destination
    assert destination.read_bytes() == payload
