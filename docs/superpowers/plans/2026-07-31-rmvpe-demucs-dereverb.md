# RMVPE、Demucs 與 Dereverb Implementation Plan

**Goal:** Make the configured pitch backend truthful, expose verified high-quality vocal
separation, and optionally dereverberate only the f0 branch.

**Tech Stack:** Python 3.10+, PyTorch/MPS, RMVPE, torchfcpe, Demucs, NARA-WPE, pytest.

---

## Task 1: Lock strict backend behavior

**Files:**
- Modify: `tests/test_melody.py`
- Modify: `everyric2/melody/extractor.py`
- Modify: `everyric2/config/settings.py`

1. Add failing tests for RMVPE availability independent of torchfcpe, missing-weight errors,
   and no FCPE constructor call after an RMVPE load failure.
2. Run the focused tests and confirm they fail for the current fallback behavior.
3. Add `PitchBackendUnavailableError`, backend-aware availability, and strict model loading.
4. Run focused tests and type/lint checks.

## Task 2: Add a verified RMVPE downloader

**Files:**
- Create: `scripts/download_rmvpe.py`
- Create: `tests/test_download_rmvpe.py`

1. Add failing tests for successful atomic install, checksum rejection, and existing-file reuse.
2. Implement streaming download with the published SHA-256 and atomic replacement.
3. Run focused tests.
4. Download the real local weights (ignored by Git) and verify the checksum.

## Task 3: Validate the Demucs high-quality option

**Files:**
- Modify: `everyric2/config/settings.py`
- Modify: `tests/test_settings.py`
- Modify: `README.md`
- Modify: `everyric2-chrome/README.md`

1. Add failing tests that accept `htdemucs_ft` and reject unknown models.
2. Change the field to a validated `Literal`.
3. Document the environment switch and four-times-slower tradeoff.

## Task 4: Add pitch-only Dereverb

**Files:**
- Create: `everyric2/audio/dereverb.py`
- Create: `tests/test_dereverb.py`
- Modify: `everyric2/config/settings.py`
- Modify: `everyric2/melody/extractor.py`
- Modify: `tests/test_melody.py`
- Modify: `pyproject.toml`

1. Add failing tests for missing optional dependency, output length/finite dtype, and pitch-only
   wiring that leaves the input `AudioData` untouched.
2. Implement NARA-WPE wrapper and strict optional dependency handling.
3. Wire it immediately before f0 resampling/chunking.
4. Run focused tests and lint.

## Task 5: Add and run A/B diagnostics

**Files:**
- Create: `scripts/pitch_ab.py`
- Create: `tests/test_pitch_ab.py`

1. Add metric tests over synthetic f0 tracks.
2. Implement backend/runtime reporting.
3. Run RMVPE vs FCPE on available singing clips, and Dereverb off/on where appropriate.
4. Record evidence without claiming GAME/MuScriptor equivalence from incompatible outputs.

## Task 6: Full verification

1. Run `pytest`.
2. Run `ruff check everyric2/ tests/ scripts/`.
3. Run `mypy everyric2/` if the repository baseline permits it; distinguish pre-existing errors.
4. Run the real RMVPE sine-wave integration and confirm `_backend == "rmvpe"` on MPS.
5. Run `git diff --check` and inspect the final diff/status.
