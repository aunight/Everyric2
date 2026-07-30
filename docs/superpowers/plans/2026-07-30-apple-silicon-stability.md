# Apple Silicon Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep CTC, RMVPE, and Demucs accelerated on Apple Silicon without exhausting unified memory or showing false 100% stage progress.

**Architecture:** Add narrow MPS branches to existing device, chunking, progress, and cleanup boundaries. Preserve CUDA behavior while serializing only the MPS-heavy work and limiting only MPS CTC forward windows.

**Tech Stack:** Python 3.13, PyTorch 2.8 MPS, torchaudio CTC, RMVPE, Demucs, pytest

---

### Task 1: Add MPS memory-reclamation coverage

**Files:**
- Modify: `tests/test_gpu_mem.py`
- Modify: `everyric2/gpu_mem.py`

- [ ] **Step 1: Write the failing MPS cleanup test**

Inject a fake `torch` module whose CUDA backend is unavailable and whose MPS backend records calls:

```py
def test_release_scratch_reclaims_mps(monkeypatch):
    calls: list[str] = []
    fake_mps = SimpleNamespace(
        synchronize=lambda: calls.append("sync"),
        empty_cache=lambda: calls.append("empty"),
        driver_allocated_memory=lambda: 3 * 1024**3,
    )
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
        mps=fake_mps,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert gpu_mem.release_scratch() == 3.0
    assert calls == ["sync", "empty"]
```

- [ ] **Step 2: Run RED**

Run `pytest -q tests/test_gpu_mem.py::test_release_scratch_reclaims_mps`; expected: returns `None`.

- [ ] **Step 3: Implement MPS cleanup**

Keep the CUDA branch unchanged, then add:

```py
if torch.backends.mps.is_available():
    torch.mps.synchronize()
    torch.mps.empty_cache()
    return float(torch.mps.driver_allocated_memory()) / (1024**3)
```

After clearing warm-cache singletons, run `gc.collect()` so released model references reach the MPS allocator.

- [ ] **Step 4: Run GREEN**

Run `pytest -q tests/test_gpu_mem.py`; expected: all tests pass.

### Task 2: Limit CTC windows only on MPS

**Files:**
- Modify: `tests/test_chunking.py`
- Modify: `everyric2/alignment/ctc_engine.py`

- [ ] **Step 1: Write the failing effective-window test**

Create an engine with configured 360-second chunks, set its cached device to `torch.device("mps")`, and
assert a 181-second waveform produces at least three windows, each at most 90 seconds. A CPU engine with
the same configuration must still return one window.

- [ ] **Step 2: Run RED**

Run the new test; expected: MPS currently returns one window.

- [ ] **Step 3: Implement the MPS cap**

Introduce:

```py
MPS_ALIGN_CHUNK_SEC = 90.0

def _effective_chunk_sec(self) -> float:
    configured = float(getattr(self.config, "align_chunk_sec", 0.0) or 0.0)
    if self._get_device().type == "mps":
        return min(configured, MPS_ALIGN_CHUNK_SEC) if configured > 0 else MPS_ALIGN_CHUNK_SEC
    return configured
```

Use `_effective_chunk_sec()` inside `_chunk_windows`; preserve existing overlap and stitching.

- [ ] **Step 4: Run GREEN**

Run `pytest -q tests/test_chunking.py`; expected: all chunking equivalence tests pass.

### Task 3: Serialize RMVPE and CTC on MPS

**Files:**
- Modify: `tests/test_f0_parallel.py`
- Modify: `everyric2/server/worker.py`

- [ ] **Step 1: Write the failing policy test**

Add:

```py
def test_f0_precompute_is_serial_on_mps_and_parallel_elsewhere():
    assert worker_core._should_precompute_f0("mps") is False
    assert worker_core._should_precompute_f0("cuda") is True
    assert worker_core._should_precompute_f0("cpu") is True
```

- [ ] **Step 2: Run RED**

Run the new test; expected: helper is missing.

- [ ] **Step 3: Implement and apply the policy**

Add the pure helper and resolve the melody device before creating `ThreadPoolExecutor`. Only submit
`precompute_f0` when the helper returns true. On MPS leave `f0_future=None`; the existing melody stage then
calls `annotate_timestamps(..., precomputed_f0=None)` and performs one synchronous inference after CTC.

- [ ] **Step 4: Run GREEN**

Run `pytest -q tests/test_f0_parallel.py`; expected: all tests pass.

### Task 4: Route Demucs to MPS

**Files:**
- Modify: `tests/test_worker_pipeline_defects.py`
- Modify: `everyric2/server/worker.py`
- Modify: `everyric2/melody/extractor.py`

- [ ] **Step 1: Write a failing caller test**

Patch `everyric2.device.resolve_device` to return `"mps"` and a fake shared separator that records
`use_gpu`. Call `_separate_stems(object())` and assert `use_gpu is True`.

- [ ] **Step 2: Run RED**

Expected: current `torch.cuda.is_available()` passes false.

- [ ] **Step 3: Replace CUDA-only caller checks**

In both `_separate_stems` and `MelodyExtractor._maybe_separate`, use:

```py
from everyric2.device import resolve_device

use_accelerator = resolve_device() != "cpu"
```

Pass `use_accelerator` to `separator.separate`; keep its existing MPS/CUDA error fallback.

- [ ] **Step 4: Run GREEN**

Run the new test plus `pytest -q tests/test_worker_pipeline_defects.py tests/test_melody.py`.

### Task 5: Prevent false 100% stage progress

**Files:**
- Modify: `tests/test_line_meta_parallel.py`
- Modify: `everyric2/server/worker.py`

- [ ] **Step 1: Tighten the existing monitor test**

Change the long-wait assertion to:

```py
assert max(progress for progress, _ in reported) < hi
```

Add a transition test confirming that changing the holder from `"전사 정렬"` to `"타이밍 보정"` makes
reported progress reach the next window's `lo` without decreasing.

- [ ] **Step 2: Run RED**

Run both monitor tests; expected: current monitor reaches `hi`.

- [ ] **Step 3: Cap active stages below their boundary**

Change the same-stage update to:

```py
active_cap = max(float(lo), float(hi - 1))
progress = min(active_cap, progress + (hi - lo) / 6.0)
```

- [ ] **Step 4: Run GREEN**

Run `pytest -q tests/test_line_meta_parallel.py -k stage_monitor`; expected: both tests pass.

### Task 6: Verify and restart the local server

**Files:**
- Verify only

- [ ] **Step 1: Run targeted tests**

Run:

```bash
pytest -q tests/test_gpu_mem.py tests/test_chunking.py tests/test_f0_parallel.py \
  tests/test_worker_pipeline_defects.py tests/test_melody.py tests/test_line_meta_parallel.py
```

Expected: zero failures.

- [ ] **Step 2: Run lint for modified Python files**

Run:

```bash
ruff check everyric2/device.py everyric2/gpu_mem.py everyric2/alignment/ctc_engine.py \
  everyric2/server/worker.py everyric2/melody/extractor.py
```

Expected: zero errors.

- [ ] **Step 3: Run the full Python suite**

Run `pytest -q`; expected: zero failures.

- [ ] **Step 4: Restart the local uvicorn process**

Stop only the verified Everyric2 uvicorn parent/child processes, then relaunch the existing command:

```bash
uv run --frozen uvicorn everyric2.server.main:app --host 127.0.0.1 --port 8000
```

Do not kill unrelated Python processes.

- [ ] **Step 5: Verify runtime state**

Confirm:

- `GET /health` returns `gpu_available: true`;
- process physical footprint returns near the cold/warm model baseline rather than 15 GB;
- a new job no longer runs RMVPE concurrently with CTC on MPS;
- an active stage remains below 100% until it transitions.

Do not commit implementation files automatically because the relevant Python files already contain
unrelated user-owned changes.
