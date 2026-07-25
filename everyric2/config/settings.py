"""Configuration settings for Everyric2."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelSettings(BaseSettings):
    """Model configuration."""

    model_config = SettingsConfigDict(env_prefix="EVERYRIC_MODEL_")

    # Model path - can be HuggingFace hub ID or local path
    path: str = Field(
        default="cpatonn/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit",
        description="HuggingFace model ID or local path",
    )

    # HuggingFace cache directory (for D: drive sharing)
    cache_dir: Path | None = Field(
        default=None,
        description="HuggingFace cache directory. Set to /mnt/d/huggingface_cache for WSL.",
    )

    # Inference settings
    device_map: str = Field(default="auto", description="Device mapping strategy")
    torch_dtype: Literal["float16", "bfloat16", "float32", "auto"] = Field(
        default="auto", description="Torch dtype for model weights"
    )
    use_flash_attention: bool = Field(
        default=True, description="Use Flash Attention 2 if available"
    )

    # Generation settings
    max_new_tokens: int = Field(default=4096, description="Maximum tokens to generate")
    temperature: float = Field(default=0.1, description="Sampling temperature")

    # Audio limits
    max_audio_duration: int = Field(
        default=2400, description="Maximum audio duration in seconds (40 min)"
    )
    chunk_duration: int = Field(
        default=1800, description="Chunk duration for long audio in seconds (30 min)"
    )
    chunk_overlap: int = Field(default=30, description="Overlap between chunks in seconds")


class AudioSettings(BaseSettings):
    """Audio processing configuration."""

    model_config = SettingsConfigDict(env_prefix="EVERYRIC_AUDIO_")

    # Sample rate - Qwen-Omni native is 24kHz
    target_sample_rate: int = Field(default=24000, description="Target sample rate for model input")

    # Demucs settings
    demucs_model: str = Field(default="htdemucs", description="Demucs model for vocal separation")

    # Temp directory
    temp_dir: Path = Field(
        default=Path("/tmp/everyric2"), description="Temporary directory for processing"
    )

    # YouTube cookie settings
    cookies_from_browser: str | None = Field(
        default=None,
        description="Browser to extract cookies from (chrome, firefox, edge, brave, opera, chromium)",
    )
    cookie_file: Path | None = Field(
        default=None, description="Path to Netscape format cookie file"
    )

    # Multi-NIC download routing
    source_address: str | None = Field(
        default=None,
        description="Local IP to bind yt-dlp connections to. On multi-NIC machines this "
        "routes downloads through a different public IP when YouTube throttles the "
        "default one with HTTP 403",
    )

    @field_validator("temp_dir", mode="after")
    @classmethod
    def ensure_temp_dir_exists(cls, v: Path) -> Path:
        """Ensure temp directory exists."""
        v.mkdir(parents=True, exist_ok=True)
        return v


class AlignmentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVERYRIC_ALIGNMENT_")

    engine: Literal["ctc", "nemo", "gpu-hybrid", "sofa"] = Field(
        default="ctc", description="Alignment engine to use"
    )
    language: Literal["auto", "en", "ja", "ko"] = Field(
        default="auto", description="Language for transcription/alignment"
    )

    nemo_model_en: str = Field(
        default="nvidia/stt_en_conformer_ctc_large",
        description="NeMo model for English",
    )

    alignment_sample_rate: int = Field(
        default=16000, description="Sample rate for alignment engines"
    )

    align_chunk_sec: float = Field(
        default=360.0,
        description="CTC 정렬 시 모델 forward에 한 번에 넣는 오디오 최대 길이(초, 겹침 포함). "
        "긴 오디오를 통짜로 넣으면 wav2vec2/MMS 인코더 활성값이 길이 비례로 커져 공유 GPU에서 "
        "OOM이 난다(실사고 2026-07-24: 17분 곡). 이 값을 넘는 오디오는 겹침 청크로 나눠 순차 "
        "추론하고 emission을 CPU에서 스티칭해 피크 VRAM을 청크 길이로 제한한다. 기본 360(6분)은 "
        "기존 통과 사례(5분)보다 커서 짧은 곡은 단일 청크=통짜 경로로 정렬 결과가 완전히 동일하다. "
        "0이면 청킹 비활성(항상 통짜).",
    )
    align_chunk_overlap_sec: float = Field(
        default=5.0,
        description="CTC 정렬 청크 간 겹침(초). 청크 경계의 수용영역 오염을 겹침 절반씩 버려 "
        "제거한다(중앙 채택). wav2vec2 수용영역(~0.4s)보다 넉넉해야 경계 프레임 emission이 "
        "통짜와 일치한다.",
    )

    align_on_vocals: bool = Field(
        default=True,
        description="Run CTC alignment on the demucs-separated vocal stem instead of the full "
        "mix — the original CLI design (--separate swapped the audio before alignment). CTC "
        "emissions are far cleaner without instrumentals, which matters most on dense mixes. "
        "Separation output is reused for VAD clamping and melody f0 either way, so enabling "
        "this adds no extra compute. Falls back to the mix when demucs is unavailable.",
    )

    star_guard_splice: bool = Field(
        default=True,
        description="When the star-swallow guard confirms the ko alignment compressed the "
        "post-interlude block forward, splice instead of discarding the whole ko alignment: "
        "keep ko (syllable-accurate) timings for lines before the interlude and take the "
        "original-text alignment's timings for lines it places after the interlude. Falls back "
        "to the full original-text alignment when the splice boundary is degenerate.",
    )

    star_tokens: bool = Field(
        default=True,
        description="Insert wildcard <star> tokens between lyric lines during CTC alignment "
        "so ad-libs/repeats not present in the lyrics are absorbed instead of "
        "stretching neighboring lines",
    )

    use_pronunciation: bool = Field(
        default=True,
        description="When line-level Korean pronunciation (독음, e.g. from the Vocaloid lyrics "
        "wiki) covers enough of the lyrics, run CTC forced alignment on the pronunciation "
        "text with the MMS 'kor' adapter and map the resulting syllable timings back onto the "
        "original lines. On synthesized/Vocaloid vocals this lifts alignment confidence and "
        "fixes gross post-interlude misplacement that local clamps cannot repair, and yields "
        "syllable-level spans that split multi-mora kanji into separate karaoke notes. "
        "Falls back to original-text alignment when coverage is insufficient or it fails.",
    )

    star_vocal_fallback_sec: float = Field(
        default=8.0,
        description="Cost gate for the pronunciation (ko) alignment guard. When a single wildcard "
        "<star> span absorbs at least this many seconds of real VAD vocal activity, the ko "
        "alignment may have compressed genuine lyric lines out of that region (kor adapter "
        "failing on a heavy-effect section). Swallow magnitude alone cannot tell 'compressed real "
        "lyrics' from 'a genuine lyric-free bridge' (熱異常 swallows ~21s benignly), so exceeding "
        "this only triggers the definitive cross-check (post_interlude_fill_margin_sec) rather "
        "than a fallback. Its purpose is to avoid running a second alignment on songs that "
        "clearly do not need it. Set to 0 to disable the guard entirely.",
    )

    interlude_min_gap_sec: float = Field(
        default=5.0,
        description="Minimum silence gap (seconds) between consecutive VAD vocal regions to count "
        "as a structural interlude. The largest such gap anchors the post-interlude window used by "
        "the ko-alignment fallback cross-check. Songs without a gap this long skip the check.",
    )

    post_interlude_fill_margin_sec: float = Field(
        default=15.0,
        description="Decision threshold for the ko-alignment star-swallow guard. Once the swallow "
        "gate trips and an interlude exists, the original-text (ja) alignment is run and both "
        "alignments are measured by how many seconds of lyric lines they place in the "
        "post-interlude vocal window. If ja fills at least this many seconds MORE than ko, the ko "
        "path compressed the post-interlude block forward (out of the window) → fall back to "
        "original-text alignment for the whole song. Anchoring on the interlude (fixed by the "
        "audio) rather than the star span (which moves between alignments) makes this robust: "
        "初音ミクの消失 shows ja−ko = +46.7 to +79.4s across runs (falls back), 熱異常 shows "
        "−1.3 to +5.5s (keeps ko) — VAD boundaries drift between runs but the separation stays "
        "enormous.",
    )

    post_interlude_leak_lead_sec: float = Field(
        default=3.0,
        description="Reverse-leak detector lead. The whole-window fill margin above only fires when "
        "an ENTIRE post-interlude block is compressed forward; it misses the milder (and now more "
        "common, after vocals-first CTC lifted bulk accuracy) failure where only the LEADING "
        "narration line of a spoken section leaks back across the interlude while the rest of the "
        "block lands correctly (初音ミクの消失 today: idx17-18 ~19s early, idx46 ~24s early, while "
        "idx19+/47+ are ~in place, so ko fills 103/116s of the largest window and the fill margin "
        "never trips). The reverse-leak check compares ja vs ko per interlude: a line is 'leaked' "
        "when ja places it at/after the interlude gap_end but ko places it at least this many "
        "seconds BEFORE gap_end. Catches single leading-line leaks the fill margin cannot see.",
    )

    post_interlude_leak_min_sec: float = Field(
        default=8.0,
        description="Minimum backward displacement (seconds) of a leaked run before the reverse-leak "
        "guard rewrites its timing. A leaked run (a maximal block of ja-vs-ko displaced lines "
        "containing at least one leaked seed line) is spliced to ja timing only when its worst line "
        "is displaced at least this far — keeping benign sub-second/fast-rap wobble untouched while "
        "catching the ~19-24s narration leaks. Conservative by design: the guard moves lines only "
        "when ja and ko disagree grossly.",
    )

    mass_leak_min_gap_sec: float = Field(
        default=12.0,
        description="Interlude length (seconds of VAD silence) that arms the ja-free mass-leak "
        "re-spacing snap. On synthetic vocals (e.g. 足立レイ) the CTC posterior is a uniform floor "
        "(熱異常: 92.5% of lines conf<0.001) so BOTH ko and ja alignments lose all acoustic anchors "
        "and a whole reprise block skips the interlude, collapsing forward (熱異常: reprise leads "
        "idx51-52 crammed at 129.9/130.7s before the 32.7s silence 132.8->165.5, rest compressed, "
        "median -14.75s). The ja cross-check cannot help (ja is equally collapsed), so the snap "
        "anchors on the interlude silence — a hard acoustic fact independent of the posterior — and "
        "only for gaps this long (a short break cannot hide a reprise). Set to 0 to disable.",
    )

    mass_leak_min_char_rate: float = Field(
        default=11.0,
        description="Impossible-cram gate for the mass-leak snap (characters per second). To tell a "
        "genuinely leaked reprise crammed before an interlude from a legitimately fast pre-break "
        "section, the snap requires at least one line in the pre-interlude cluster to sing faster "
        "than this — a rate no human sung/rapped line reaches (熱異常 idx51: 11 chars in 0.8s = "
        "~13.8/s), which only happens when forced alignment squeezed a full lyric line into a "
        "near-zero slot. Below this the cluster is treated as real fast singing and left untouched.",
    )

    mass_leak_max_coverage: float = Field(
        default=0.3,
        description="PRIMARY gate for the mass-leak snap: a line only counts as a leaked cram when "
        "its own VAD vocal coverage (fraction of the line span overlapping detected vocal) is below "
        "this — i.e. the line is floating on silence, the hard acoustic signature of a genuinely "
        "misplaced reprise line. Lines sitting on real vocal are NEVER moved. This protects "
        "ultra-fast songs (消失: sung lines are <1.5s apart and dense, so the spacing/char-rate "
        "signals alone false-positived normal lines placed correctly on 35-42s vocal and shoved them "
        "+24-38s — the vocal-coverage gate is what tells a correct dense line from a leaked one). "
        "char-rate stays a secondary confirmation.",
    )

    fuse_original_chars: bool = Field(
        default=True,
        description="Fuse measured original-text (ja) character timing into the pronunciation (ko) "
        "alignment. On the ko path the ORIGINAL characters never touch the audio: their spans are a "
        "three-stage back-mapping (aligned Korean syllable -> mora -> original char, "
        "text/reading.py::map_pron_alignment_to_line), so even a perfectly placed line has a "
        "synthesized INTRA-line distribution. Measured: ko-aligned songs light 38-59% of original "
        "chars in 3+ char simultaneous clumps, ja-aligned songs only 2%. With this on, a second "
        "alignment on the original text is always run in the ko path and each line's ko boundaries "
        "and pron_segments are KEPT while the intra-line original-char spans are replaced by ja's "
        "measured distribution, linearly mapped onto the line's MEASURED PRON-SYLLABLE WINDOW (first "
        "syllable start .. last syllable end), NOT the line bounds: a ko line usually ends later than "
        "the actual singing (tail extension, gap to the next line), so mapping onto the bounds "
        "stretched the original chars into that padding while the pron syllables kept their measured "
        "times — measured on 6 regenerated songs, the original chars ran 0.09-0.44s LATE at the 75% "
        "quantile with the gap widening from 0 at the first char (worker.py::_measured_vocal_window). "
        "Lines with no usable pron window fall back to mapping onto the line bounds. Lines where ja "
        "itself collapsed (_impossible_word_distribution) or yields fewer distinct timing anchors than the "
        "back-mapping are left untouched. Cost is one extra CTC pass (~9s on a 4.7min song); the ko "
        "and ja adapters share the mms-1b-all base so the language switch is an adapter swap "
        "(0.23s, was 5.33s before adapter caching). The same ja pass is reused by the dual-align "
        "safety net and the reverse-leak guard, so those no longer pay for their own. Set to false "
        "to restore the pure back-mapping behaviour.",
    )

    dual_align_conf: float = Field(
        default=0.002,
        description="Confidence floor for the dual-alignment safety net (0 disables). Once the "
        "pronunciation (ko) path is chosen it is kept even at very low quality, so a synthetic "
        "vocal whose CTC posterior is a uniform floor never gets a second opinion. When the ko "
        "alignment's song-level average line confidence falls below this, the original-text (ja) "
        "alignment is also run and whichever scores higher is adopted. Measured: 熱異常 original ko "
        "quality 0.0005 vs its cover 0.0076 — 0.002 sits between, so only genuinely floor-confidence "
        "songs pay for the extra alignment. This threshold is NOT contaminated by the adapter-scale "
        "problem below: the ko path always loads the same 'kor' adapter (align(language='ko') is "
        "hardcoded), so the scale this number lives on is fixed.",
    )

    dual_align_min_ratio: float = Field(
        default=1.5,
        description="Margin the original-text (ja) alignment must beat the ko alignment by, in "
        "average line confidence, before the dual-align safety net switches to it (ja_conf >= "
        "ko_conf * this). A plain higher-is-better comparison would flip on noise when both scores "
        "are near the floor (熱異常: ja is equally collapsed, so it must NOT win); requiring a clear "
        "margin preserves the pronunciation alignment's syllable value unless ja is decisively better. "
        "The two sides may be measured with DIFFERENT MMS adapters (ko is always 'kor'; the original "
        "pass is jpn/kor/cmn by detected language) and CTC confidence scales with adapter vocab size, "
        "so worker.py rescales ja onto ko's adapter scale before applying this ratio — the number "
        "therefore always means the same thing. When both adapters match (English songs: both 'kor' "
        "since the eng adapter was dropped) the rescale is the identity.",
    )

    pron_referee: bool = Field(
        default=False,
        description="OFF BY DEFAULT — measured harmful on real audio, see the verdict below. "
        "Let the AUDIO decide which reading a line is actually sung with. The "
        "deterministic pronunciation path (text/pron_style.py) reaches 82.1% exact agreement with "
        "the 2,207 human-written wiki pronunciation lines, and virtually all of the remaining "
        "mismatch collapses onto a single question — WHICH READING (私 와타시 vs 와타쿠시, 三日月 "
        "미카즈키 vs 밋카츠키, 数え事 카조에 고토 vs 코토 (連濁), 何も 나니모 vs 난모, and ateji "
        "furigana like 涙（シル） that no dictionary knows but the singer sings). A dictionary cannot "
        "settle those; the audio can, and it is FREE: the [1, T, V] log-softmax emission is computed "
        "once per song and already resident, so scoring an alternative token sequence is just another "
        "F.forced_align DP over the line's frame window — NO model forward. After the first pass the "
        "engine slices each line's window, force-aligns every candidate reading from "
        "pron_style.pronunciation_candidates, and adopts the winner only if it beats the current "
        "reading by pron_referee_margin in per-token average log-probability. Lines with a single "
        "candidate are never scored (cost 0), so songs whose readings are unambiguous pay nothing. "
        "Set false to keep the pre-referee behaviour exactly (the candidate argument to "
        "CTCEngine.align is optional and the code path is identical when unused). "
        "REAL-AUDIO VERDICT (s5Rkv_5Sbbo, 134 lines, 2026-07-25): the mechanism works but its "
        "judgement does not. 53 of 134 lines switched, and the switches are wrong three ways. "
        "(a) DELETION IS FREE: 11 switches simply dropped characters (私 와타쿠시오 → 시오, "
        "彼らを 카레라오 → 카라오, 苦しみ 쿠루시미 → 쿠 시미) because scoring the merged token "
        "spans lets a shorter candidate hand its unwanted frames to blank at no cost. The score "
        "must be normalised over the WHOLE window (blank frames included), not over tokens. "
        "(b) THE KOR ADAPTER CANNOT RESOLVE THESE AXES: 21 of 53 switches only changed 오오→오우, "
        "i.e. Korean long-vowel ORTHOGRAPHY for the same Japanese [oː] — a convention we already "
        "settled at 82.1%, silently undone. Worse, unmotivated 連濁 won repeatedly (호시가→보시가, "
        "카네가→가네가, 후네와→부네와): the adapter is not separating h from b in sung audio. "
        "(c) NO AUDIO-CONFIDENCE GATE: this song's greedy transcript reads '9집민', '6와', 'k' and "
        "heard/reading length ratio is 0.57 — there was no signal to arbitrate on, yet every line "
        "was scored. Direct proof the audio did not drive the outcome: of 52 switches with a "
        "greedy transcript, 40 left the distance to what was heard UNCHANGED. Do not turn this on "
        "again without (a) window normalisation, (b) dropping candidate axes the adapter cannot "
        "hear, (c) a per-song/per-line confidence gate, and a re-measurement that shows a net win.",
    )

    pron_referee_margin: float = Field(
        default=0.15,
        description="Margin a challenger reading must beat the current reading by, in PER-TOKEN "
        "AVERAGE LOG-PROBABILITY (nats), before the audio referee replaces a line's pronunciation. "
        "Normalising by token count is mandatory — comparing sums of (negative) frame log-probs "
        "would hand victory to whichever candidate has the fewest tokens. Why 0.15: on the kor "
        "adapter a typical aligned character scores exp(score)≈0.05, i.e. about -3.0 nats per token, "
        "and a genuinely wrong reading is wrong in 1-2 syllables out of ~10 in a line, so the true "
        "signal is on the order of (2 x 3.0)/10 = 0.6 nats/token. 0.15 sits ~4x below that expected "
        "signal yet far above numeric jitter between two near-identical token sequences, which is "
        "the conservative side: failing to fix a reading only leaves today's 82.1%, while flipping a "
        "correct reading on noise makes the karaoke syllables WRONG. This is the same philosophy as "
        "dual_align_min_ratio (require a clear margin or keep the incumbent), expressed as an "
        "absolute log-prob difference instead of a ratio because the two sides here are measured on "
        "the SAME emission with the same adapter, so no scale correction is needed. NOT calibrated "
        "on real audio yet — re-measure per deployment before loosening.",
    )

    pron_referee_human_margin: float = Field(
        default=0.4,
        description="Larger margin required when the line's incumbent pronunciation was written by "
        "a HUMAN (merged from the vocaloid wiki) rather than produced deterministically. Human "
        "readings are included as candidates rather than exempted from arbitration, because humans "
        "do miss furigana/ateji occasionally, but they are right far more often than the 82.1% "
        "deterministic path, so the audio must be decisively clearer before overriding one. Detected "
        "without extra plumbing: the incumbent is human exactly when it differs from "
        "pron_style.pronunciation_candidates()[0] (the deterministic default). ~2.7x "
        "pron_referee_margin, i.e. still below the ~0.6 nats/token a genuinely wrong reading costs.",
    )

    pron_referee_max_candidates: int = Field(
        default=8,
        description="Upper bound on candidate readings scored per line (including the incumbent). "
        "Generation cost is negligible (60 lines of candidates in 0.064s measured) and each extra "
        "candidate is one small DP over the line window, but every extra comparison raises the "
        "chance that the maximum over challengers exceeds the incumbent by luck, so the count is "
        "capped. 8 is the depth at which the human reading of 三日月の夜 (미카즈키) enters the "
        "MeCab n-best candidate list (measured: it is the 7th distinct rendering).",
    )

    synth_all_lines_conf: float = Field(
        default=0.0,
        description="OPT-IN whole-song synth floor (default 0 = disabled). The DEFAULT intra-line "
        "re-synthesis is PER-LINE and structural: only lines the guard moved (a 'leak' fix label) or "
        "lines whose word distribution is physically impossible (chars crammed faster than "
        "mass_leak_min_char_rate within their span, or word spans falling outside the line bounds) "
        "get uniformly re-synthesized — CTC's good lines are preserved. Song-level confidence is NOT "
        "used for selection (熱異常: corr(line conf, |residual|) = -0.19 — conf has no discriminative "
        "power on collapsed songs). This field only enables an additional whole-song fallback: when "
        ">0 and the song's avg confidence is below it, EVERY line is re-synthesized regardless of the "
        "structural signals. Left at 0 by default because it discards accurate CTC timing on songs "
        "like 消失 (quality 0.00106) whose sung lines are ±0.5s correct. WARNING if you enable it: "
        "the value is compared against RAW song-level confidence, which scales with the MMS adapter's "
        "vocab size (measured: same English song, eng adapter 0.1289 vs kor 0.0492 — 2.6x apart with "
        "IDENTICAL residuals), so one number cannot mean the same thing for a jpn-aligned and a "
        "kor-aligned song. Calibrate per deployment language, or use debug.quality_norm (scale-free) "
        "when comparing songs across adapters.",
    )

    exclude_gloss_lines: bool = Field(
        default=True,
        description="Drop translation/pronunciation gloss lines from the ALIGNMENT INPUT when the "
        "pasted lyrics are a bilingual sheet rather than the sung text. Measured on a 73-song corpus: "
        "FxOfDVyITak pastes a perfectly regular (kana, hangul, hangul) 3-line block 74/74 times — two "
        "thirds of the input is not sung — and ba7YbGO2aq4 has 8 hangul lines that are each a "
        "translation of the immediately preceding Japanese line (8/8). Forcing timings onto non-sung "
        "lines makes CTC fit a token sequence several times longer than the vocal, wrecking the sung "
        "lines' timing too; both songs sit at the corpus confidence floor (0.0072/0.0135). Detection "
        "is deliberately conservative (worker.detect_gloss_lines): it needs either a >=3-cycle "
        "(non-hangul, hangul, hangul) period covering >=90% of the input at >=90% conformance, or a "
        "hangul minority (>=4 lines, <=45% of the song) where EVERY hangul line directly follows a "
        "non-hangul line of one uniform script — a genuinely sung Korean passage has consecutive "
        "hangul lines and fails that. Excluded lines are NOT discarded: they are re-attached to their "
        "source line as pronunciation/translation for display. A fully alternating (original, hangul) "
        "sheet is intentionally NOT detected — it is indistinguishable from a bilingual duet. Set "
        "false to feed the pasted text to the aligner verbatim.",
    )


class TranslationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVERYRIC_TRANSLATE_")

    engine: Literal["gemini", "openai", "local", "nvidia"] = Field(
        default="gemini", description="Translation engine"
    )
    model: str = Field(default="gemini-2.0-flash", description="Model name")
    nvidia_model: str = Field(
        default="openai/gpt-oss-120b",
        description="Model name for the NVIDIA NIM engine (separate from `model` so the "
        "gemini default doesn't leak into NIM requests). 2026-07 실측: gpt-oss-120b가 "
        "ja→ko 30줄 기준 오역 0·22s로 최선 (qwen3.5-122b는 2026-07-20 EOL, "
        "qwen3-next-80b는 君→쿤 오독·정반대 오역, deepseek-v4-pro는 장문 120s 타임아웃)",
    )
    api_url: str | None = Field(default=None, description="Custom API URL for local LLM")
    api_key: str | None = Field(default=None, description="API key (env var takes precedence)")
    tone: Literal["literal", "natural", "poetic", "casual", "formal"] = Field(
        default="natural", description="Translation tone/style"
    )
    temperature: float = Field(default=0.3, description="Generation temperature")
    include_pronunciation: bool = Field(
        default=False, description="Include pronunciation transcription"
    )
    pronunciation_format: Literal["parentheses", "brackets", "newline"] = Field(
        default="parentheses", description="Pronunciation display format"
    )
    target_language: str = Field(default="ko", description="Target language for translation")
    timeout: int = Field(default=120, description="API timeout in seconds")
    batch_concurrency: int = Field(
        default=4,
        description="긴 가사를 나눈 번역 배치를 동시에 요청하는 개수. 배치는 서로 의존이 "
        "없어(각자 자기 구간만 번역하고 결과를 인덱스 순서로 잇는다) 병렬로 돌려도 결과가 "
        "같다. 순차 루프일 때 번역 시간이 배치 수에 선형 비례했다 — 실측(2026-07): NIM "
        "자체는 30줄 1배치 8.3s(140 tok/s)인데 실사용 translate 평균 20.9s·중앙 8.9s·최대 "
        "118.5s로, 중앙값이 1배치 시간과 거의 같고 긴 곡만 길어졌다(독음 포함이면 배치가 "
        "60→30줄로 잘게 나뉘어 배치 수가 2배). NIM 동시 요청 실측은 4건·8건 모두 200(429 "
        "없음)이고 벽시계가 단건과 같아 실제로 병렬 처리된다 — 다만 분당 지속 한도(RPM)는 "
        "확인되지 않아 기본값을 4로 잡는다. 배치 하나의 소요는 동시성을 올려도 줄지 않으므로 "
        "그 이상은 이득 대비 429 위험만 커진다. 1이면 스레드를 만들지 않고 기존 순차 루프와 "
        "동일하게 동작한다(즉시 되돌릴 수 있는 스위치). 1 미만은 1로 취급.",
    )
    rate_limit_retries: int = Field(
        default=3,
        description="429(rate limit) 응답에 대한 재시도 횟수 상한. 동시 요청이 늘면 미확인 "
        "RPM 한도에 걸릴 수 있는데, 429는 '조금 뒤엔 되는' 실패라 상한 안에서 기다렸다 다시 "
        "던진다. 상한을 넘으면 응답을 그대로 돌려줘 기존 실패 경로(API error 예외 → 배치 "
        "부분 실패 처리)를 탄다. 0이면 재시도 없음.",
    )
    rate_limit_backoff_sec: float = Field(
        default=2.0,
        description="429 재시도 첫 대기(초). 재시도마다 2배로 늘고(2→4→8), 응답의 Retry-After가 "
        "더 길면 그 값을 따르며, 어느 쪽이든 회당 30초 상한에서 멈춘다(게이트웨이 타임아웃 "
        "600s 안에서 끝나야 한다). 대기하는 배치는 다른 배치의 진행을 막지 않는다.",
    )
    max_tokens: int = Field(
        default=8192,
        description="Max completion tokens for OpenAI-compatible chat endpoints (openai/local/"
        "nvidia). Without this, some NIM-hosted models default to a small completion budget "
        "and truncate the pronunciation JSON array mid-response for multi-line lyrics. "
        "reasoning 모델(gpt-oss 등)은 사고 토큰이 이 예산을 같이 쓰므로 4096이면 "
        "30줄 곡에서 JSON이 잘렸다 — 8192로 상향.",
    )


class SegmentationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVERYRIC_SEGMENT_")

    mode: Literal["line", "word", "character"] = Field(
        default="line", description="Segmentation mode"
    )
    min_duration: float = Field(default=0.2, description="Minimum segment duration in seconds")
    max_chars_per_segment: int = Field(
        default=50, description="Maximum characters per segment (for auto-split in line mode)"
    )
    min_silence_gap: float = Field(
        default=0.3, description="Minimum silence gap between segments. Shorter gaps are merged"
    )
    silence_merge_mode: Literal["midpoint", "extend_prev", "extend_next"] = Field(
        default="midpoint", description="How to merge short silence gaps"
    )
    interlude_gap: float = Field(
        default=5.0, description="Gaps longer than this are treated as interludes (no subtitle)"
    )
    use_mecab: bool = Field(default=True, description="Use MeCab for Japanese word segmentation")


class MelodySettings(BaseSettings):
    """Vocal melody extraction (karaoke pitch bar) configuration."""

    model_config = SettingsConfigDict(env_prefix="EVERYRIC_MELODY_")

    enabled: bool = Field(
        default=True, description="Annotate word timestamps with MIDI notes"
    )
    separate_vocals: bool = Field(
        default=True,
        description="Run demucs vocal separation before f0 extraction "
        "(mix tracks bleed accompaniment pitch into notes; falls back to mix if unavailable)",
    )
    device: str = Field(default="auto", description="Inference device: auto, cpu, cuda")
    f0_model: Literal["fcpe", "rmvpe"] = Field(
        default="rmvpe",
        description="f0 estimation backend. rmvpe (DeepUnet+BiGRU, singing-pitch SOTA) "
        "measured lower subharmonic lock-on (-12 semitone mass ratio 0.44 vs FCPE's 0.69) "
        "and fewer large frame-to-frame jumps on a real karaoke track A/B; falls back to "
        "FCPE automatically if the rmvpe.pt weights are missing or fail to load.",
    )
    rmvpe_model_path: Path = Field(
        default=Path(__file__).resolve().parents[2] / "models" / "rmvpe" / "rmvpe.pt",
        description="Path to RMVPE weights (rmvpe.pt, ~180MB, MIT-licensed inference code "
        "ported from RVC-Project, weights from HuggingFace lj1995/VoiceConversionWebUI). "
        "Not bundled with the repo; download separately.",
    )
    rmvpe_threshold: float = Field(
        default=0.01,
        description="RMVPE unvoiced salience cutoff (0-1 sigmoid). Lower than the RVC "
        "default of 0.03 — measured to raise line-span voiced coverage to ~FCPE parity "
        "(0.90 vs 0.905 mean) without degrading octave-lock-on or jump-rate metrics.",
    )
    threshold: float = Field(
        default=0.006, description="FCPE unvoiced detection threshold"
    )
    f0_min: float = Field(default=65.0, description="Minimum f0 in Hz (~C2)")
    f0_max: float = Field(default=1100.0, description="Maximum f0 in Hz (~C6)")
    octave_snap: bool = Field(
        default=True,
        description="Fold octave/harmonic jumps (>7 semitones vs previous voiced frame) "
        "back toward the melodic trajectory before note quantization "
        "(fixes FCPE octave lock-on; measured 37%→5% large-jump rate)",
    )
    key_detect: bool = Field(
        default=True,
        description="Estimate the song key (Krumhansl-Schmuckler pitch-class correlation) "
        "and store it with the sync for karaoke display",
    )
    key_snap: bool = Field(
        default=True,
        description="Snap out-of-scale notes whose span f0 median sits near the semitone "
        "rounding boundary to the in-scale neighbor (skipped when key confidence is low; "
        "clear chromatic passing notes are preserved)",
    )
    anchor_to_words: bool = Field(
        default=True,
        description="Cut notes at aligned character (syllable) boundaries instead of free "
        "f0-stability runs, so note timing locks to the lyric alignment the user sees",
    )
    min_note_sec: float = Field(
        default=0.08, description="Minimum stable duration for a note segment"
    )
    max_gap_sec: float = Field(
        default=0.12, description="Unvoiced gap shorter than this stays in the same note"
    )
    min_voiced_ratio: float = Field(
        default=0.15, description="Skip spans whose voiced frame ratio is below this"
    )
    chunk_sec: float = Field(
        default=360.0,
        description="f0 추출(RMVPE/FCPE) 시 모델에 한 번에 넣는 오디오 최대 길이(초, 겹침 포함). "
        "f0 추론은 CTC 정렬과 병렬로 돌아(WS2-B) 두 forward의 활성 피크가 합쳐지므로, 정렬과 "
        "동일하게 청크 처리해 멜로디 쪽 피크 VRAM도 길이 무관 상한을 둔다. 이 값을 넘는 오디오는 "
        "겹침 청크로 나눠 f0 배열을 시간축으로 스티칭한다. 기본 360(6분)은 짧은 곡을 단일 청크="
        "통짜 경로로 유지해 노트 결과가 동일하다. 0이면 청킹 비활성.",
    )
    chunk_overlap_sec: float = Field(
        default=5.0,
        description="f0 추출 청크 간 겹침(초). 경계 프레임의 피치 오염을 겹침 절반씩 버려 제거한다.",
    )


class OutputSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVERYRIC_OUTPUT_")

    default_format: Literal["srt", "ass", "lrc", "json"] = Field(
        default="srt", description="Default output format"
    )
    generate_all_variants: bool = Field(
        default=False, description="Generate all output variants (original, translated, etc.)"
    )


class ServerSettings(BaseSettings):
    """API server configuration."""

    model_config = SettingsConfigDict(env_prefix="EVERYRIC_SERVER_")

    host: str = Field(
        default="127.0.0.1",
        description="Server bind host. Default loopback-only; set 0.0.0.0 explicitly "
        "to expose on the LAN (combine with api_key).",
    )
    port: int = Field(default=8000, description="Server port")
    reload: bool = Field(default=False, description="Enable auto-reload for development")
    workers: int = Field(default=1, description="Number of worker processes")
    api_key: str = Field(
        default="",
        description="When set, every /api request must present this value (or the admin "
        "key) in X-API-Key. Empty = no auth (local single-user default).",
    )
    max_job_audio_sec: int = Field(
        default=1800,
        description="Maximum audio duration (seconds) accepted for sync generation. Longer "
        "videos (podcasts, live archives, hours-long loops) would monopolize the single GPU "
        "slot for hours with no way to cancel mid-alignment; the job now fails fast with a "
        "friendly message right after download instead. 0 disables the cap.",
    )

    max_concurrent_jobs: int = Field(
        default=1,
        description="Max sync-generation jobs processed at once. Alignment+separation+"
        "melody hold significant GPU/RAM; excess jobs wait in a queue (status=queued).",
    )
    admin_api_key: str = Field(
        default="",
        description="Admin API key (X-API-Key). When set, destructive actions "
        "(force regenerate, sync reset) from other callers are rate-limited; "
        "requests presenting this key bypass the limit. Empty = no limits (local use).",
    )
    daily_destructive_limit: int = Field(
        default=2,
        description="Max force-regenerations/resets per video per 24h for non-admin "
        "callers (only enforced when admin_api_key is set). 0 disables the limit.",
    )
    worker_key: str = Field(
        default="",
        description="원격 GPU 워커 풀 인증 키 (X-Worker-Key). 설정하면 /api/worker/* "
        "엔드포인트가 켜져 원격 워커가 잡을 클레임·처리한다. 빈 값이면 워커 API 전체가 "
        "403 (기능 비활성). 개인 풀 모델이라 워커들이 이 키 하나를 공유하고 worker_id로 "
        "머신을 구분한다.",
    )
    local_worker: bool = Field(
        default=True,
        description="서버 프로세스가 직접 생성 파이프라인을 돌릴지 여부. True면 기존처럼 "
        "인프로세스로 처리한다. False면 GPU 없는 API 전용 서버로 보고, 생성 잡을 add_task "
        "없이 status=queued로만 마킹해 원격 워커가 클레임하도록 둔다 (queue_position 표시 유지).",
    )
    worker_lease_sec: int = Field(
        default=120,
        description="원격 워커가 클레임한 잡의 리스 만료(초). 진행률 보고(≤2s 간격)가 "
        "하트비트를 겸해 리스를 갱신한다. 만료되면(워커 하트비트 끊김) 다음 claim 처리 "
        "시 잡을 queued로 되돌려 다른 워커가 다시 가져가게 한다.",
    )
    # ── 중립 연동 (외부 곡 인덱스 / 외부 미디어 캐시) ──────────────────────────
    song_index_url: str = Field(
        default="",
        description="외부 곡 인덱스(songindex/1)의 베이스 URL. 설정하면 /api/vocaro/match를 "
        "업스트림 GET {url}/match?title=... 프록시로 전환한다(확장 응답 형태 무변경). 빈 값이면 "
        "기존 로컬 인덱스 경로 그대로.",
    )
    song_index_key: str = Field(
        default="",
        description="외부 곡 인덱스 인증 키 — 프록시 요청에 Authorization: Bearer <key>로 실린다.",
    )
    media_cache_url: str = Field(
        default="",
        description="외부 미디어 캐시(mediacache/1)의 베이스 URL. 설정하면 잡이 처리 주체에게 "
        "넘어가는 순간 GET {url}/lookup?platform=youtube&id=<video_id>로 조회해, 히트 시 "
        "재다운로드 없이 로컬 원본에서 오디오만 추출해 쓴다. 빈 값이면 항상 yt-dlp 경로.",
    )
    media_cache_key: str = Field(
        default="",
        description="외부 미디어 캐시 인증 키 — 조회 요청에 Authorization: Bearer <key>로 실린다.",
    )
    link_match_threshold: float = Field(
        default=0.55,
        description="반주 상관 링크 검증(link-jobs)에서 match로 판정하는 confidence(정규화 "
        "상관 최고 피크 절대높이) 하한. 실측 캘리브레이션(2026-07-24): 동일 인스트 커버 "
        "0.93, 무관 곡 쌍 0.02 — 0.55는 그 사이의 보수적 경계다.",
    )
    worker_vram_guard_gb: float = Field(
        default=8.0,
        description="잡 경계 VRAM 회수(empty_cache) 후에도 예약이 이 값(GiB)을 넘으면 참조 "
        "누수 회귀로 보고 웜 모델 캐시를 버리고 재적재한다. 동거 호스트 실측(2026-07-24): "
        "모델 실중량 3~6GiB, 앨로케이터 사재기 방치 시 18.4GiB까지 부풂. 0 = 가드 비활성.",
    )
    link_min_offset_margin: float = Field(
        default=0.08,
        description="링크 검증에서 오프셋 유일성 게이트 — (최고 피크 − 이차 피크)가 이 값 "
        "미만이면 confidence가 높아도 자동 링크를 보류한다. 루프 구조 곡은 마디 간격의 "
        "이차 피크가 최고 피크에 근접하는데, 그 간극이 너무 작으면 이웃 박자 오프셋을 "
        "잘못 고를 위험이 있다 (틀린 오프셋 링크는 no-link보다 해롭다).",
    )
    # ── 커버 온디맨드 자동 연결 (제목으로 후보만 찾고, 판정은 반주 상관이 한다) ────
    auto_link_candidates: bool = Field(
        default=True,
        description="사용자가 처음 보는 영상에서 코퍼스의 같은 곡 후보를 제목으로 찾아 링크 "
        "검증 잡을 자동 제출할지. False면 후보 목록만 돌려주고 잡은 만들지 않는다(킬 스위치). "
        "제목 매칭은 '후보 발견'에만 쓰이고 같은 곡인지의 최종 판정은 반주 상관 게이트"
        "(link_match_threshold·link_min_offset_margin)가 그대로 담당한다 — 제목이 맞았다는 "
        "이유만으로 링크가 만들어지는 경로는 없다.",
    )
    link_candidate_min_title_score: float = Field(
        default=0.6,
        description="링크 후보로 인정하는 제목 유사도 하한 (1.0=정규화 정확 일치, 그 미만은 "
        "상호 포함 시 길이비). 오탐의 대가는 검증 잡 한 번(GPU 수십 초)이고 틀린 링크는 "
        "만들어지지 않으므로 헐거워도 안전하다. 0.6은 «【初音ミク】…» 류에서 가수명 조각이 "
        "다른 곡 제목에 우연히 포함되며 나오는 0.5~0.57대 오탐을 걸러내는 선이다.",
    )
    link_candidate_scan_limit: int = Field(
        default=500,
        description="후보 탐색이 훑는 코퍼스 최대 곡 수(영상별 최신 싱크 1건 기준). 코퍼스가 "
        "작아 전수 스캔으로 충분하다 — 이 값은 코퍼스가 커졌을 때의 안전 상한일 뿐이다.",
    )
    link_retry_cooldown_days: int = Field(
        default=14,
        description="같은 (영상, 후보) 쌍의 검증 잡을 다시 제출하기까지의 쿨다운(일). "
        "get_active_pair는 진행 중(queued/processing) 중복만 막아서, 완료·실패한 쌍은 "
        "사용자가 그 영상을 열 때마다 GPU를 다시 태울 수 있다. 최근 이 기간 안에 끝난 "
        "(done/failed) 이력이 있으면 자동 제출을 건너뛴다. 0이면 쿨다운 비활성.",
    )
    manual_link_requires_admin: bool = Field(
        default=False,
        description="수동 링크 생성(POST /api/sync/link)에 어드민 키를 요구할지. 이 API는 "
        "반주 검증 없이 임의 오프셋(0 포함)으로 SyncLink를 박을 수 있어 코퍼스에 틀린 링크가 "
        "남은 전례가 있다. True + admin_api_key 설정 시 X-API-Key가 어드민 키인 요청만 허용한다. "
        "기본 False는 기존 동작 유지(로컬 단일 사용자) — 켜지 않아도 수동 링크는 항상 "
        "verified=false로 기록돼 조회 응답에서 자동 검증 링크와 구분된다.",
    )
    warm_models: bool = Field(
        default=True,
        description="생성 파이프라인의 무거운 모델(demucs 분리기·CTC 정렬 엔진·멜로디 f0 "
        "백엔드)을 프로세스 수명 동안 지연 싱글턴으로 상주시켜 두 번째 잡부터 재로드 0회로 "
        "만든다. 상주 주체는 원격 워커(CLI)와 인프로세스 서버뿐 — API 전용 모드(local_worker="
        "false)는 생성을 돌리지 않으므로 어떤 모델도 로드되지 않는다(torch 지연 임포트 불변). "
        "false면 기존처럼 잡마다 인스턴스를 새로 만든다.",
    )


class Settings(BaseSettings):
    """Main settings container."""

    model_config = SettingsConfigDict(
        env_prefix="EVERYRIC_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model: ModelSettings = Field(default_factory=ModelSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    alignment: AlignmentSettings = Field(default_factory=AlignmentSettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    segmentation: SegmentationSettings = Field(default_factory=SegmentationSettings)
    melody: MelodySettings = Field(default_factory=MelodySettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)

    # Debug mode
    debug: bool = Field(default=False, description="Enable debug mode")


# Global settings instance (lazy loaded)
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset settings (useful for testing)."""
    global _settings
    _settings = None
