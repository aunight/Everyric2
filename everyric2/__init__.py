"""Everyric2 - Lyrics synchronization using Qwen3-Omni multimodal LLM."""

import os

# MPS(Apple GPU)에 커널이 없는 연산을 CPU로 흘린다 — torch 임포트 **전**에 있어야
# 효력이 있으므로 패키지 루트에서 setdefault한다 (everyric2.device 주석 참고).
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

__version__ = "0.1.0"
