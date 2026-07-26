"""정렬 비결정성 확정 실험 — 조사 문서 02절의 실험 5개를 그대로 옮긴 진단 스크립트.

같은 입력에서 재실행 편차가 최대 21.74초(실측)인데, 후보 원인이 셋이다:
  ① emission 자체가 실행마다 갈린다 (TF32가 주범 후보 — 워커는 cli.py가 전역으로 켠다)
  ② Demucs 분리 결과가 실행마다 갈린다 (서브프로세스라 TF32 플래그와 무관한 별도 오염원)
  ③ emission은 같은데 forced_align의 동점 경로가 부동소수점에 뒤집힌다

이 스크립트는 셋을 분리해서 잰다. 실행은 GPU 서버 전용이고 결과는 JSON으로 남긴다.

    .venv/bin/python scripts/diag_determinism.py \
        --wav bench/audio/zyRt-nBM3dY.vocals.wav --lyrics bench/lyrics/zyRt-nBM3dY.txt \
        --tf32 both --noise 30 --out bench/out/det_zyRt.json

프로세스 간 비교(--save-emission / --ref-emission): cuBLAS 알고리즘 선택은 프로세스
안에서는 같고 프로세스 사이에서 갈릴 수 있으므로, 한 번 저장하고 새 프로세스로 다시
실행해 대조해야 «워커 재시작·잡 간» 편차를 본 것이 된다.

결정적 모드(--det)는 CUBLAS_WORKSPACE_CONFIG=:4096:8 환경변수가 프로세스 시작 전에
있어야 한다 — 스크립트 안에서 늦게 넣으면 이미 열린 cuBLAS 핸들에는 적용되지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
from pathlib import Path


def _sha16(arr) -> str:
    return hashlib.sha256(arr.tobytes()).hexdigest()[:16]


def _set_tf32(on: bool) -> None:
    import torch

    torch.backends.cuda.matmul.allow_tf32 = on
    torch.backends.cudnn.allow_tf32 = on
    # cli.py가 켜는 것과 같은 손잡이 — "high"가 TF32 허용, "highest"가 순수 fp32
    torch.set_float32_matmul_precision("high" if on else "highest")


def _build_tokens(lines: list[str], vocab, use_star: bool, star_id: int):
    """_align_cjk의 토큰 빌드를 그대로 옮긴 것 — 라인별 첫 글자 토큰 위치를 함께 낸다."""
    from everyric2.alignment.ctc_engine import _resolve_token_char

    tokens: list[int] = []
    first_char_pos: list[int | None] = []
    pos = 0
    if use_star:
        tokens.append(star_id)
        pos += 1
    for text in lines:
        first: int | None = None
        for ch in text:
            tok = _resolve_token_char(ch, vocab)
            if tok is not None:
                if first is None:
                    first = pos
                tokens.append(vocab[tok])
                pos += 1
        if "|" in vocab:
            tokens.append(vocab["|"])
            pos += 1
        if use_star:
            tokens.append(star_id)
            pos += 1
        first_char_pos.append(first)
    return tokens, first_char_pos


def _line_starts(emission, tokens, blank_id, first_char_pos, ratio):
    """forced_align 1회 → 라인별 시작 시각(초). 정렬 실패 라인은 None."""
    import torch
    import torchaudio.functional as F

    targets = torch.tensor([tokens], dtype=torch.int32, device=emission.device)
    aligned, scores = F.forced_align(emission, targets, blank=blank_id)
    spans = F.merge_tokens(aligned[0], scores[0], blank=blank_id)
    starts: list[float | None] = []
    for p in first_char_pos:
        if p is None or p >= len(spans):
            starts.append(None)
        else:
            starts.append(round(float(spans[p].start) * ratio, 3))
    return starts


def _pairwise_start_diff(a: list, b: list) -> dict:
    ds = [abs(x - y) for x, y in zip(a, b) if x is not None and y is not None]
    if not ds:
        return {"n": 0}
    return {
        "n": len(ds),
        "max": round(max(ds), 3),
        "median": round(statistics.median(ds), 3),
        "moved_gt_0p1": sum(1 for d in ds if d > 0.1),
        "moved_gt_1s": sum(1 for d in ds if d > 1.0),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wav", required=True, help="정렬 입력 오디오 (분리된 보컬이면 그것)")
    ap.add_argument("--lyrics", required=True, help="가사 텍스트 파일 (줄 단위)")
    ap.add_argument("--language", default="ja")
    ap.add_argument("--runs", type=int, default=2, help="조건당 emission 반복 횟수")
    ap.add_argument("--tf32", choices=["on", "off", "both"], default="both")
    ap.add_argument("--demucs", type=int, default=0, help="Demucs 분리 반복·해시 비교 횟수")
    ap.add_argument("--save-vocals", default=None, help="첫 분리의 보컬 스템을 wav로 저장")
    ap.add_argument("--det", action="store_true", help="결정적 모드 비용 실측")
    ap.add_argument("--noise", type=int, default=0, help="emission+잡음 CPU forced_align 반복")
    ap.add_argument("--noise-sigma", type=float, default=1e-3)
    ap.add_argument("--save-emission", default=None, help="첫 emission을 저장(프로세스 간 대조용)")
    ap.add_argument("--ref-emission", default=None, help="저장해 둔 emission과 대조")
    ap.add_argument("--out", default=None, help="JSON 보고 경로")
    args = ap.parse_args()

    import torch

    from everyric2.alignment.ctc_engine import CTCEngine
    from everyric2.audio.loader import AudioLoader
    from everyric2.config.settings import get_settings

    settings = get_settings()
    report: dict = {
        "wav": args.wav,
        "torch": torch.__version__,
        "cuda": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "star_tokens": bool(settings.alignment.star_tokens),
    }

    lines = [ln for ln in Path(args.lyrics).read_text(encoding="utf-8").splitlines() if ln.strip()]
    report["lines"] = len(lines)

    loader = AudioLoader()
    audio = loader.load(Path(args.wav))
    prepared = loader.prepare_for_alignment(audio, target_sr=16000, normalize=True)
    waveform = torch.from_numpy(prepared.waveform.astype("float32"))
    if waveform.dim() == 2:
        waveform = waveform.mean(dim=0)
    audio_sec = waveform.shape[0] / 16000
    report["audio_sec"] = round(audio_sec, 1)

    # ── ② Demucs 분리 결정성 (서브프로세스라 이 프로세스의 TF32와 무관) ──
    if args.demucs > 0:
        from everyric2.audio.separator import VocalSeparator

        sep = VocalSeparator()
        hashes, times = [], []
        for i in range(args.demucs):
            t0 = time.time()
            res = sep.separate(audio, use_gpu=torch.cuda.is_available())
            times.append(round(time.time() - t0, 1))
            hashes.append(_sha16(res.vocals.waveform))
            if i == 0 and args.save_vocals:
                res.vocals.to_file(Path(args.save_vocals))
        report["demucs"] = {
            "hashes": hashes,
            "identical": len(set(hashes)) == 1,
            "sec": times,
        }
        print("demucs:", report["demucs"], flush=True)

    if args.runs <= 0 and not (args.noise or args.det or args.ref_emission or args.save_emission):
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return

    engine = CTCEngine(settings.alignment)
    engine._ensure_model_loaded(args.language)
    device = engine._get_device()
    vocab = engine._processor.tokenizer.get_vocab()
    blank_id = engine._processor.tokenizer.pad_token_id

    use_star = bool(settings.alignment.star_tokens)

    conditions = {"on": [True], "off": [False], "both": [True, False]}[args.tf32]
    report["conditions"] = {}
    kept_emission = None  # 잡음 실험·저장용 (마지막 조건의 1회차)

    for tf32_on in conditions:
        _set_tf32(tf32_on)
        key = f"tf32_{'on' if tf32_on else 'off'}"
        emissions = []
        secs = []
        for _ in range(args.runs):
            torch.cuda.synchronize() if torch.cuda.is_available() else None
            t0 = time.time()
            em = engine._ctc_log_emission(waveform, device)
            torch.cuda.synchronize() if torch.cuda.is_available() else None
            secs.append(round(time.time() - t0, 2))
            emissions.append(em.detach().cpu())

        star_id = emissions[0].shape[-1]
        tokens, first_char_pos = _build_tokens(lines, vocab, use_star, star_id)
        cond: dict = {"emission_sec": secs, "tokens": len(tokens)}

        starts_per_run = []
        for em in emissions:
            if use_star:
                star_col = torch.zeros((em.shape[0], em.shape[1], 1), dtype=em.dtype)
                em = torch.cat([em, star_col], dim=2)
            ratio = audio_sec / em.shape[1]
            starts_per_run.append(_line_starts(em, tokens, blank_id, first_char_pos, ratio))

        pairs = []
        for i in range(len(emissions)):
            for j in range(i + 1, len(emissions)):
                a, b = emissions[i], emissions[j]
                pairs.append(
                    {
                        "equal": bool(torch.equal(a, b)),
                        "max_abs_diff": float((a - b).abs().max()),
                        "diff_frac": float((a != b).float().mean()),
                        "starts": _pairwise_start_diff(starts_per_run[i], starts_per_run[j]),
                    }
                )
        cond["pairs"] = pairs
        cond["emission_sha"] = [_sha16(e.numpy()) for e in emissions]
        report["conditions"][key] = cond
        kept_emission = emissions[0]
        print(key, json.dumps(cond, ensure_ascii=False), flush=True)

    if args.save_emission and kept_emission is not None:
        torch.save(kept_emission, args.save_emission)
        report["saved_emission"] = args.save_emission
    if args.ref_emission and kept_emission is not None:
        ref = torch.load(args.ref_emission, weights_only=True)
        report["cross_process"] = {
            "equal": bool(torch.equal(ref, kept_emission)),
            "max_abs_diff": float((ref - kept_emission).abs().max()),
            "diff_frac": float((ref != kept_emission).float().mean()),
        }
        print("cross_process:", report["cross_process"], flush=True)

    # ── ⑤ 재현성 위험 지표: emission에 잡음을 얹은 CPU forced_align 반복 ──
    # 모델 forward가 없어 비용이 거의 0이다. 분산이 실제 GPU 편차와 같은 자릿수면
    # «평평한 posterior → 잡음 민감»이 확정되고, 그 분산이 곡 단위 신뢰도 지표가 된다.
    if args.noise > 0 and kept_emission is not None:
        base = kept_emission
        if use_star:
            star_col = torch.zeros((base.shape[0], base.shape[1], 1), dtype=base.dtype)
            base = torch.cat([base, star_col], dim=2)
        star_id = kept_emission.shape[-1]
        tokens, first_char_pos = _build_tokens(lines, vocab, use_star, star_id)
        ratio = audio_sec / base.shape[1]
        gen = torch.Generator().manual_seed(20260727)
        runs = []
        t0 = time.time()
        for _ in range(args.noise):
            noisy = base + torch.randn(base.shape, generator=gen) * args.noise_sigma
            runs.append(_line_starts(noisy, tokens, blank_id, first_char_pos, ratio))
        elapsed = round(time.time() - t0, 1)
        per_line = list(zip(*runs))
        stds, spreads = [], []
        for vals in per_line:
            xs = [v for v in vals if v is not None]
            if len(xs) >= 2:
                stds.append(statistics.pstdev(xs))
                spreads.append(max(xs) - min(xs))
        report["noise"] = {
            "runs": args.noise,
            "sigma": args.noise_sigma,
            "sec": elapsed,
            "line_std_median": round(statistics.median(stds), 3) if stds else None,
            "line_std_max": round(max(stds), 3) if stds else None,
            "line_spread_median": round(statistics.median(spreads), 3) if spreads else None,
            "line_spread_max": round(max(spreads), 3) if spreads else None,
            "lines_spread_gt_1s": sum(1 for s in spreads if s > 1.0),
        }
        print("noise:", json.dumps(report["noise"]), flush=True)

    # ── ④ 결정적 모드 비용 ──
    if args.det:
        ws = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
        report["det"] = {"cublas_workspace": ws}
        if not ws:
            print("WARN: CUBLAS_WORKSPACE_CONFIG가 없다 — 결정적 모드가 cuBLAS에 적용되지 않는다", flush=True)
        try:
            torch.use_deterministic_algorithms(True)
            secs = []
            for _ in range(max(2, args.runs)):
                torch.cuda.synchronize() if torch.cuda.is_available() else None
                t0 = time.time()
                engine._ctc_log_emission(waveform, device)
                torch.cuda.synchronize() if torch.cuda.is_available() else None
                secs.append(round(time.time() - t0, 2))
            report["det"]["emission_sec"] = secs
        except Exception as e:  # 결정적 구현이 없는 연산이 있으면 여기서 드러난다
            report["det"]["error"] = repr(e)
        finally:
            torch.use_deterministic_algorithms(False)
        print("det:", json.dumps(report["det"]), flush=True)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
