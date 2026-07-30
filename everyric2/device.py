"""추론 디바이스 결정 — cuda > mps > cpu.

Mac(Apple Silicon)은 CUDA가 없어 기존 `cuda if available else cpu` 이분법이 전부 CPU로
떨어졌다. MPS(Metal)를 가운데 끼운 우선순위를 한 곳에 두고 demucs·RMVPE·CTC가 공유한다.

MPS 미구현 연산은 PYTORCH_ENABLE_MPS_FALLBACK=1로 CPU에 흘린다 — 이 env는 torch 임포트
전에 있어야 하므로 everyric2/__init__.py가 setdefault한다(이 모듈이 아니라).
"""

from functools import lru_cache


@lru_cache(maxsize=1)
def resolve_device(pref: str = "auto") -> str:
    """'auto'를 실제 디바이스 문자열로. 명시값(cuda/mps/cpu)은 그대로 통과."""
    if pref != "auto":
        return pref
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"
