"""3곡 실증 비교 벤치 — 반복 정렬 프로토콜 (조사 종합 2026-07-26 · 03절).

가사 정렬 분야에 «같은 입력을 여러 번 돌려 분포로 평가»하는 공개 프로토콜이 없어서
(조사 결과: 0편) 여기서 정한 규약을 따른다:

  · 설정당 곡당 N회 반복 (기본 5). 기본은 **분리부터** 반복한다 — 재실행 편차의 입력
    오염원이 Demucs였으므로(diag_determinism.py 실측) 분리를 고정하면 실제 편차를 못 본다.
  · 라인 이동량은 median/IQR로 보고한다 (평균은 저신뢰 곡의 꼬리에 지배된다).
  · 두 설정 비교는 같은 곡·같은 라인을 짝지어 Wilcoxon signed-rank.
  · 기준(reference)은 사람이 만든 유튜브 자막의 라인 시각 — SRT 해상도 한계(줄 타이밍엔
    유효, 음절엔 무효)와 표시/가창 오프셋 편향이 있지만, 그 편향은 설정 A/B 양쪽에 똑같이
    걸리므로 비교는 공정하다. 매칭률이 낮은 곡은 기준 없이 편차만 잰다.

지표는 mir_eval(MIT, MIREX 표준)을 쓴다 — 자체 재구현하지 않는다. mir_eval이 없으면
AAE/MAE/PCO만 수동 폴백으로 내고 그 사실을 기록한다.

서버(GPU) 전용. 예:

    .venv/bin/python scripts/align_bench.py \
        --video-id zyRt-nBM3dY --video-id VWVtIg5cdDU --video-id BiQs9ABhT7U \
        --runs 5 --out bench/out/bench.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np


def _percentile_spread(vals: list[float]) -> dict:
    """반복 실행에서 한 라인이 움직인 폭들의 분포 요약 (median/IQR/max)."""
    if not vals:
        return {"n": 0}
    q1, q3 = np.percentile(vals, [25, 75])
    return {
        "n": len(vals),
        "median": round(float(np.median(vals)), 3),
        "iqr": [round(float(q1), 3), round(float(q3), 3)],
        "max": round(float(max(vals)), 3),
        "gt_1s": int(sum(1 for v in vals if v > 1.0)),
    }


def _metrics_vs_reference(ref: dict[int, float], est: dict[int, float]) -> dict | None:
    """자막 기준 라인 시작 오차 — mir_eval 우선, 실패 시 수동 AAE/MAE/PCO 폴백."""
    common = sorted(set(ref) & set(est.keys()))
    if len(common) < 5:
        return None
    r = np.array([ref[i] for i in common], dtype=np.float64)
    e = np.array([est[i] for i in common], dtype=np.float64)
    err = e - r
    out = {
        "n": len(common),
        "aae": round(float(np.mean(np.abs(err))), 3),
        "mae": round(float(np.median(np.abs(err))), 3),
        "pco_0.3": round(float(np.mean(np.abs(err) <= 0.3)), 3),
        # 지각 비대칭(Deezer ISMIR 2021: ahead -0.3 / lagging +0.2)을 보려면 부호가 필요하다
        "signed_median": round(float(np.median(err)), 3),
    }
    try:
        import mir_eval.alignment as me_align

        out["mir_eval"] = {
            "absolute_error": [round(float(x), 3) for x in me_align.absolute_error(r, e)],
            "percentage_correct": round(float(me_align.percentage_correct(r, e, window=0.3)), 3),
            "karaoke_perceptual": round(float(me_align.karaoke_perceptual_metric(r, e)), 3),
        }
    except Exception as exc:  # mir_eval 미설치·API 상이 — 수동 지표만 남기고 사실을 기록
        out["mir_eval_unavailable"] = repr(exc)[:120]
    return out


def _caption_tracks(video_id: str, lines: list[str], captions_dir: Path) -> list:
    """자막 트랙 (lang, events) 목록 — 파일 캐시 우선, 없으면 yt-dlp로 받아 저장."""
    path = captions_dir / f"{video_id}.json"
    if path.exists():
        return [tuple(t) for t in json.loads(path.read_text(encoding="utf-8"))]
    from everyric2.alignment.caption_anchors import script_lang_hint
    from everyric2.server.services.youtube_captions import iter_manual_caption_events

    tracks = [
        (lang, events)
        for lang, events in iter_manual_caption_events(
            video_id, script_lang_hint("\n".join(lines)), 5
        )
    ]
    captions_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tracks, ensure_ascii=False), encoding="utf-8")
    return tracks


def _reference_and_spans(video_id: str, lines: list[str], captions_dir: Path, audio_sec: float):
    """(기준 라인 시각 dict, 금지 구간, 트랙 요약). 기준은 매칭률 최고 트랙의 앵커 시각."""
    from everyric2.alignment.caption_anchors import (
        MIN_KEY_LEN,
        anchor_key,
        derive_anchor_plan,
        match_anchors,
    )

    tracks = _caption_tracks(video_id, lines, captions_dir)
    matchable = sum(1 for t in lines if len(anchor_key(t)) >= MIN_KEY_LEN) or 1
    best = (0.0, None, None)  # (rate, lang, anchors)
    for lang, events in tracks:
        anchors = match_anchors(lines, events)
        rate = len(anchors) / matchable
        if rate > best[0]:
            best = (rate, lang, anchors)
    rate, lang, anchors = best
    reference = {a.line_idx: a.start for a in (anchors or [])} if rate >= 0.5 else {}

    # 금지 구간은 프로덕션과 같은 유도 경로·기본값을 쓴다 (caption_anchors 기본 동작)
    plan = derive_anchor_plan(lines, tracks, audio_sec=audio_sec)
    summary = {
        "tracks": [[lang_, len(ev)] for lang_, ev in tracks],
        "best": [lang, round(rate, 3), len(anchors or [])],
        "forbidden": plan.spans,
        "plan_skipped": plan.debug.get("skipped"),
    }
    return reference, plan.spans, summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video-id", action="append", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument(
        "--configs",
        default="base,star",
        help="쉼표 구분: base(보컬 스템, star 평평) / star(보컬 스템, 우세도 성형) / "
        "nosep(믹스 직접 정렬 — 조사 04① 분리 생략 A/B)",
    )
    ap.add_argument("--star-weight", type=float, default=None, help="star 설정 가중치 재정의")
    ap.add_argument("--reuse-vocals", action="store_true", help="분리를 곡당 1회로 고정(편차 원천 제거)")
    ap.add_argument("--no-anchors", action="store_true", help="자막 금지 구간 없이 정렬")
    ap.add_argument("--language", default="auto")
    ap.add_argument("--audio-dir", default="bench/audio")
    ap.add_argument("--lyrics-dir", default="bench/lyrics")
    ap.add_argument("--captions-dir", default="bench/captions")
    ap.add_argument("--out", default="bench/out/bench.json")
    args = ap.parse_args()

    import torch

    from everyric2.alignment.ctc_engine import CTCEngine
    from everyric2.alignment.star_prior import vocal_presence_from_stems
    from everyric2.audio.loader import AudioLoader
    from everyric2.audio.separator import VocalSeparator
    from everyric2.config.settings import get_settings
    from everyric2.inference.prompt import LyricLine

    settings = get_settings()
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    loader = AudioLoader()
    separator = VocalSeparator()
    engine = CTCEngine(settings.alignment)
    star_weight = (
        args.star_weight
        if args.star_weight is not None
        else settings.alignment.star_prior_weight
    )
    # 성형 켜기/끄기는 엔진에 presence를 주느냐로 갈린다 — 설정 객체는 하나로 둔다
    engine.config.star_prior_weight = star_weight

    report: dict = {
        "runs": args.runs,
        "configs": configs,
        "star_weight": star_weight,
        "reuse_vocals": args.reuse_vocals,
        "anchors": not args.no_anchors,
        "demucs_shifts": getattr(settings.audio, "demucs_shifts", None),
        "songs": {},
    }

    for vid in args.video_id:
        wav = Path(args.audio_dir) / f"{vid}.wav"
        lyr = Path(args.lyrics_dir) / f"{vid}.txt"
        lines = [ln for ln in lyr.read_text(encoding="utf-8").splitlines() if ln.strip()]
        audio = loader.load(wav)
        song: dict = {"lines": len(lines), "audio_sec": round(audio.duration, 1)}

        reference, forbidden, cap_summary = ({}, [], {"skipped": "no_anchors"})
        try:
            reference, forbidden, cap_summary = _reference_and_spans(
                vid, lines, Path(args.captions_dir), audio.duration
            )
        except Exception as exc:
            cap_summary = {"error": repr(exc)[:200]}
        if args.no_anchors:
            forbidden = []
        song["captions"] = cap_summary
        song["reference_lines"] = len(reference)

        lyric_lines = [LyricLine(text=t, line_number=i + 1) for i, t in enumerate(lines)]
        shared_vocals = None
        per_config: dict[str, list[dict[int, float]]] = {c: [] for c in configs}
        song["runs"] = {c: [] for c in configs}

        for run in range(args.runs):
            if args.reuse_vocals and shared_vocals is not None:
                vocals, accomp = shared_vocals
            else:
                t0 = time.time()
                res = separator.separate(audio, use_gpu=torch.cuda.is_available())
                vocals, accomp = res.vocals, res.accompaniment
                sep_sec = round(time.time() - t0, 1)
                shared_vocals = (vocals, accomp)
            presence = None
            if "star" in configs:
                # 우세도 기반 — f0 유성 지시자는 분리 스템 위에서 대비가 없다 (star_prior.py)
                presence = vocal_presence_from_stems(
                    vocals.waveform,
                    accomp.waveform,
                    vocals.sample_rate,
                    settings.alignment.star_prior_smooth_sec,
                )
            for cfg in configs:
                t0 = time.time()
                results = engine.align(
                    audio if cfg.startswith("nosep") else vocals,
                    lyric_lines,
                    language=args.language,
                    forbidden_spans=forbidden or None,
                    vocal_presence=presence if cfg == "star" else None,
                )
                starts = {
                    i: r.start_time for i, r in enumerate(results) if r.start_time is not None
                }
                per_config[cfg].append(starts)
                song["runs"][cfg].append(
                    {
                        "align_sec": round(time.time() - t0, 1),
                        "sep_sec": sep_sec if not args.reuse_vocals or run == 0 else 0.0,
                        "star_spans": list(engine._last_star_spans),
                        "star_prior": engine._last_star_prior,
                        "anchor": (engine.get_last_caption_anchor() or {}).get("adopted"),
                        "vs_ref": _metrics_vs_reference(reference, starts),
                    }
                )
                print(f"{vid} run{run} {cfg}: {song['runs'][cfg][-1]['vs_ref']}", flush=True)

        # ── 반복 편차: 라인별 이동폭(max-min) 분포 ──
        song["dispersion"] = {}
        for cfg in configs:
            runs = per_config[cfg]
            spreads = []
            for i in range(len(lines)):
                xs = [r[i] for r in runs if i in r]
                if len(xs) >= 2:
                    spreads.append(max(xs) - min(xs))
            song["dispersion"][cfg] = _percentile_spread(spreads)

        # ── 설정 비교: 라인별 |오차| 중앙값을 짝지어 Wilcoxon ──
        if reference and len(configs) == 2:
            a_cfg, b_cfg = configs
            paired_a, paired_b = [], []
            for i in reference:
                xa = [abs(r[i] - reference[i]) for r in per_config[a_cfg] if i in r]
                xb = [abs(r[i] - reference[i]) for r in per_config[b_cfg] if i in r]
                if xa and xb:
                    paired_a.append(statistics.median(xa))
                    paired_b.append(statistics.median(xb))
            if len(paired_a) >= 8:
                try:
                    from scipy.stats import wilcoxon

                    diff = np.array(paired_a) - np.array(paired_b)
                    if np.any(diff != 0):
                        stat, p = wilcoxon(paired_a, paired_b)
                        song["wilcoxon"] = {
                            "n": len(paired_a),
                            "p": round(float(p), 5),
                            f"{a_cfg}_median_abs_err": round(float(np.median(paired_a)), 3),
                            f"{b_cfg}_median_abs_err": round(float(np.median(paired_b)), 3),
                        }
                    else:
                        song["wilcoxon"] = {"n": len(paired_a), "p": 1.0, "identical": True}
                except Exception as exc:
                    song["wilcoxon"] = {"error": repr(exc)[:120]}

        report["songs"][vid] = song
        print(json.dumps({vid: {k: song[k] for k in ("dispersion",) if k in song}},
                         ensure_ascii=False), flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved:", out)


if __name__ == "__main__":
    main()
