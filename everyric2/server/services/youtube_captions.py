"""유튜브 자막 조달 서비스 — 라우터와 무관하게 재사용되는 IO/변환 로직.

워치 페이지에서 긁은 timedtext URL은 POT(proof-of-origin) 강제로 브라우저 플레이어
밖에서는 200 + 빈 본문을 돌려준다 (2026-07 실측). yt-dlp는 클라이언트 선택/POT을
내부에서 처리하고 계속 업데이트되므로, 자막은 **반드시 서버가 yt-dlp로 받아야 한다**.
확장이 timedtext를 직접 치는 경로로 대체할 수 없다.

소비자는 둘이다:
  ① `/api/captions` GET 라우트 — 사용자가 트랙을 고르던 옛 경로 (폐기 예정)
  ② `POST /api/sync/generate-from-caption` — video_id만으로 원어 트랙을 자동 판정해
     싱크 생성 잡을 만드는 현 경로

여기 함수는 전부 블로킹이다(yt-dlp). async 라우트에서 부를 때는 threadpool로 넘긴다.
"""

import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 경로 파라미터를 URL·파일 glob에 그대로 쓰므로 형식을 강제한다 (쿼리 인젝션 차단)
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
LANG_RE = re.compile(r"^[A-Za-z0-9-]{1,16}$")

# 자동 생성 자막은 번역 대상 언어 ~150개가 전부 나열된다 — 원어 트랙('-orig')과
# 사용자가 실제로 쓸 번역만 노출한다 (공개 트랙 목록 전용)
AUTO_LANG_ALLOW = {"ja", "ko", "en"}

# CTC 엔진이 전용 모델/어댑터를 가진 언어 (alignment.ctc_engine.LANG_MODEL_MAP과 동일).
# 자막에서 판정한 언어가 여기 없으면 job.language를 비워 엔진의 텍스트 기반 자동 판정에
# 맡긴다 — 'gl'/'fil' 같은 코드를 그대로 넘기면 엉뚱한 어댑터가 잡힌다.
ALIGNABLE_LANGS = {"ja", "ko", "zh", "en"}

# 정리 후 이 줄 수를 못 넘기면 가사로 쓰지 않는다. 자막이 «[음악]» 같은 효과음 표기나
# 안내 문구뿐인 영상에서 GPU를 태우고 쓰레기 싱크를 남기는 것을 막는 하한선이다.
MIN_LYRIC_LINES = 3


# 실패 코드 → HTTP 상태. 5xx는 조달 자체가 실패한 일시적 사건(재시도 가치 있음),
# 4xx는 "이 영상은 자막으로 안 된다"는 확정 판정이다 — 클라이언트는 4xx를 받으면
# 가사 직접 붙여넣기로 안내한다.
HTTP_STATUS_BY_CODE = {
    "listing_failed": 502,
    "download_failed": 502,
    "no_captions": 404,
    "language_undetermined": 404,
    "empty_caption": 404,
    "too_short": 404,
}


class CaptionUnavailable(Exception):
    """자막으로 가사를 만들 수 없을 때 — code로 실패 사유를 구분한다.

    code: listing_failed | no_captions | language_undetermined | download_failed |
          empty_caption | too_short
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def http_status(self) -> int:
        return HTTP_STATUS_BY_CODE.get(self.code, 502)

    @property
    def terminal(self) -> bool:
        """재시도해도 결과가 같은가 — True면 사용자에게 다른 경로를 안내해야 한다."""
        return self.http_status < 500


@dataclass(frozen=True)
class TrackChoice:
    """자동 판정으로 고른 트랙. lang은 yt-dlp 트랙 키 그대로다 (예: 'ja', 'ja-orig')."""

    lang: str
    auto: bool
    label: str
    # 판정 근거 (asr_orig | asr_only | video_language | sole_manual) — 응답·로그에 실어
    # 어떤 신호로 이 언어를 골랐는지 사람이 확인할 수 있게 한다
    reason: str
    # 트랙 키에서 벗겨낸 순수 언어 코드 ('ja-orig' → 'ja')
    language: str


@dataclass(frozen=True)
class CaptionLyrics:
    """자막에서 뽑아낸 가사 후보."""

    track: TrackChoice
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def align_language(self) -> str | None:
        """CTC 엔진에 넘길 언어 — 지원 목록 밖이면 None(엔진 자동 판정)."""
        return self.track.language if self.track.language in ALIGNABLE_LANGS else None


# ── yt-dlp 조달 ───────────────────────────────────────────────────


def ydl_opts(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """다운로더와 동일한 쿠키/EJS/회선 옵션을 공유하는 yt-dlp 옵션."""
    from everyric2.audio.downloader import YouTubeDownloader

    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True}
    dl = YouTubeDownloader()
    dl._add_cookie_options(opts)
    dl._add_ejs_options(opts)
    dl._add_network_options(opts)
    if extra:
        opts.update(extra)
    return opts


def extract_caption_info(video_id: str) -> dict[str, Any]:
    """영상 메타(자막 트랙 목록 포함)를 yt-dlp로 가져온다."""
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        logger.exception(f"caption listing failed for {video_id}")
        raise CaptionUnavailable("listing_failed", "자막 목록을 가져오지 못했어요") from e
    if not info:
        raise CaptionUnavailable("listing_failed", "자막 목록을 가져오지 못했어요")
    return info


def download_track_lines(video_id: str, lang: str, auto: bool) -> list[dict[str, Any]]:
    """선택한 트랙을 json3로 받아 [{start, end, text}]로 파싱한다."""
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    tmp = Path(tempfile.mkdtemp(prefix="eycap-"))
    try:
        opts = ydl_opts(
            {
                "writesubtitles": not auto,
                "writeautomaticsub": auto,
                "subtitleslangs": [lang],
                "subtitlesformat": "json3",
                "outtmpl": str(tmp / "cap.%(ext)s"),
            }
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as e:
            logger.exception(f"caption download failed for {video_id}/{lang}")
            raise CaptionUnavailable("download_failed", "자막을 내려받지 못했어요") from e

        files = sorted(tmp.glob("*.json3"))
        if not files:
            raise CaptionUnavailable("empty_caption", f"이 영상에 {lang} 자막이 없어요")
        data = json.loads(files[0].read_text(encoding="utf-8"))
        lines = json3_events_to_lines(data)
        if not lines:
            raise CaptionUnavailable("empty_caption", "자막 트랙이 비어 있어요")
        return lines
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── json3 → 라인 ──────────────────────────────────────────────────


def json3_events_to_lines(data: dict[str, Any]) -> list[dict[str, Any]]:
    """timedtext json3 → [{start, end, text}] — 빈 줄/음표만 있는 줄 제거,
    연속 중복(롤링 자막)은 첫 줄의 end를 늘려 병합한다."""
    lines: list[dict[str, Any]] = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        text = " ".join("".join(s.get("utf8", "") for s in segs).split())
        if not text or all(ch in "♪♫♬ " for ch in text):
            continue
        start = float(ev.get("tStartMs", 0)) / 1000.0
        end = start + float(ev.get("dDurationMs", 0)) / 1000.0
        if lines and lines[-1]["text"] == text:
            lines[-1]["end"] = max(lines[-1]["end"], round(end, 3))
            continue
        lines.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return lines


def track_label(entries: list[dict[str, Any]] | None, lang: str, auto: bool) -> str:
    name = next((e.get("name") for e in (entries or []) if e.get("name")), None) or lang
    return f"{name} (자동 생성)" if auto else str(name)


def list_public_tracks(info: dict[str, Any]) -> list[dict[str, Any]]:
    """확장에 노출하던 트랙 목록 (업로더 자막 전체 + 자동 생성은 원어/ja·ko·en만).

    **폐기 예정** — 사용자가 트랙을 고르는 UI가 사라지면 함께 지운다.
    """
    tracks: list[dict[str, Any]] = []
    for lang, entries in (info.get("subtitles") or {}).items():
        if lang == "live_chat":
            continue  # 라이브 채팅 리플레이 — 자막이 아니다
        tracks.append({"lang": lang, "label": track_label(entries, lang, False), "auto": False})
    for lang, entries in (info.get("automatic_captions") or {}).items():
        if not (lang.endswith("-orig") or lang in AUTO_LANG_ALLOW):
            continue
        tracks.append({"lang": lang, "label": track_label(entries, lang, True), "auto": True})
    # 업로더 자막 우선, 그 다음 자동 원어 — 지나치게 길어지지 않게 상한
    tracks.sort(key=lambda t: (t["auto"], not t["lang"].endswith("-orig")))
    return tracks[:12]


# ── 원어 판정 ─────────────────────────────────────────────────────


def base_lang(lang: str) -> str:
    """트랙 키에서 순수 언어 코드만 남긴다 — 'ja-orig'/'ja-JP'/'zh-Hans' → 'ja'/'ja'/'zh'."""
    return (lang or "").split("-", 1)[0].lower()


def _usable_keys(mapping: dict[str, Any] | None) -> list[str]:
    return [k for k in (mapping or {}) if k != "live_chat"]


def detect_original_language(
    subtitles: dict[str, Any] | None,
    automatic_captions: dict[str, Any] | None,
    video_language: str | None = None,
) -> tuple[str, str] | None:
    """이 영상 노래의 원어를 사용자 개입 없이 판정한다. (언어코드, 판정근거) 또는 None.

    실측 근거: 어떤 곡의 트랙 구성은 수동작성 15종(gl/ru/vi/es/ar/en/oc/id/ja/zh/tr/pl/
    fr/fil/ko) + 자동생성 ja였고 원어는 ja였다. 수동작성 트랙 수는 팬 번역 인기도를
    따를 뿐 원어와 무관하므로 **다수결·순서로는 절대 고를 수 없다**. 반면 유튜브는
    원어 오디오에 대해서만 ASR을 만들므로 자동 생성 트랙의 언어가 곧 원어다.

    우선순위:
      ① asr_orig — yt-dlp가 원어 ASR에 붙이는 '-orig' 접미사 트랙. 자동 생성 목록에는
         번역본 ~150종이 함께 들어오므로 '-orig'만이 원어를 가린다.
      ② asr_only — 번역본이 노출되지 않아 자동 트랙이 하나뿐이면 그게 ASR 원어다.
      ③ video_language — yt-dlp가 기본 오디오 트랙에서 뽑은 info['language'].
         해당 언어의 자막이 실제로 있을 때만 인정한다.
      ④ sole_manual — 업로더 자막이 딱 하나면 그게 가사일 가능성이 가장 높다.
    """
    auto_keys = _usable_keys(automatic_captions)
    manual_keys = _usable_keys(subtitles)

    orig = sorted(k for k in auto_keys if k.endswith("-orig"))
    if orig:
        return base_lang(orig[0]), "asr_orig"

    if len(auto_keys) == 1:
        return base_lang(auto_keys[0]), "asr_only"

    if video_language:
        lang = base_lang(video_language)
        if any(base_lang(k) == lang for k in (*manual_keys, *auto_keys)):
            return lang, "video_language"

    if len(manual_keys) == 1:
        return base_lang(manual_keys[0]), "sole_manual"

    return None


def _match_key(mapping: dict[str, Any] | None, lang: str, prefer_orig: bool = False) -> str | None:
    """해당 언어의 트랙 키를 고른다 — 정확 일치 > ('-orig') > 같은 기저 언어."""
    keys = _usable_keys(mapping)
    if prefer_orig and f"{lang}-orig" in keys:
        return f"{lang}-orig"
    if lang in keys:
        return lang
    same = sorted(k for k in keys if base_lang(k) == lang)
    return same[0] if same else None


def select_original_track(info: dict[str, Any]) -> TrackChoice:
    """원어를 판정하고 그 언어의 트랙 하나를 고른다.

    같은 언어에 업로더 자막과 자동 생성이 모두 있으면 **업로더 자막을 쓴다** — 자동
    생성은 가사 오인식이 많아 정렬 텍스트로서 품질이 떨어진다.
    """
    subtitles = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    if not _usable_keys(subtitles) and not _usable_keys(autos):
        raise CaptionUnavailable("no_captions", "이 영상에는 자막이 없어요")

    detected = detect_original_language(subtitles, autos, info.get("language"))
    if not detected:
        raise CaptionUnavailable(
            "language_undetermined",
            "자막은 있지만 어느 것이 이 노래의 원어인지 판단하지 못했어요",
        )
    lang, reason = detected

    manual_key = _match_key(subtitles, lang)
    if manual_key:
        return TrackChoice(
            lang=manual_key,
            auto=False,
            label=track_label(subtitles.get(manual_key), manual_key, False),
            reason=reason,
            language=lang,
        )
    auto_key = _match_key(autos, lang, prefer_orig=True)
    if auto_key:
        return TrackChoice(
            lang=auto_key,
            auto=True,
            label=track_label(autos.get(auto_key), auto_key, True),
            reason=reason,
            language=lang,
        )
    raise CaptionUnavailable(
        "language_undetermined", f"원어({lang})로 판정했지만 해당 언어 자막이 없어요"
    )


# ── 자막 → 가사 텍스트 정리 ───────────────────────────────────────

# «[음악]» «[Applause]» «【拍手】» — 자동 생성 자막의 효과음/상황 표기. 대괄호류는
# 가사 표기에 거의 쓰이지 않아 줄 안에 있어도 통째로 걷어낸다.
_BRACKET_RE = re.compile(r"[\[［【][^\]］】]*[\]］】]")
# 소괄호는 코러스/애드리브 표기로 실제 가사에 흔하다 — 줄 전체가 괄호이고 내용이 알려진
# 효과음일 때만 버린다
_PAREN_LINE_RE = re.compile(r"^[(（]\s*([^)）]*?)\s*[)）]$")
_EFFECT_WORDS = {
    "음악", "박수", "웃음", "환호", "노래", "연주", "무음",
    "music", "applause", "laughter", "laughs", "cheering", "cheers", "singing",
    "instrumental", "sound", "noise", "silence", "no audio",
    "音楽", "拍手", "笑", "笑い", "歓声", "演奏", "音楽が流れる",
}
# 화자 표기: '>> ', '- ', '이름: ' — 이름은 공백 없는 짧은 토큰만 인정해 가사 안의
# 콜론(«3:00» 등)을 잘라내지 않게 한다
_LEAD_MARKER_RE = re.compile(r"^(?:>>+|♪+|-(?=\s))\s*")
_SPEAKER_RE = re.compile(r"^[^\s:：]{1,12}[:：]\s+")
_TRAIL_NOTE_RE = re.compile(r"[♪♫♬\s]+$")


def _clean_line(raw: str) -> str:
    text = _BRACKET_RE.sub(" ", raw or "")
    text = _LEAD_MARKER_RE.sub("", text.strip())
    text = _SPEAKER_RE.sub("", text)
    text = _TRAIL_NOTE_RE.sub("", text)
    text = " ".join(text.split())
    if not text:
        return ""
    paren = _PAREN_LINE_RE.match(text)
    if paren and paren.group(1).strip().lower() in _EFFECT_WORDS:
        return ""
    return text


def clean_caption_lines(
    lines: list[dict[str, Any]] | list[str], merge_rolling: bool = False
) -> list[str]:
    """자막 라인 → 가사 텍스트 줄. 타임스탬프는 버린다 (정렬은 서버가 새로 한다).

    걸러내는 것: 효과음/상황 표기(`[음악]`), 화자 표기(`>>`, `이름:`), 장식 음표,
    빈 줄, **연속 중복 줄**. 같은 가사가 후렴으로 다시 나오는 것은 진짜 반복이므로
    떨어져 있는 중복은 건드리지 않는다.

    merge_rolling=True(자동 생성 자막)면 롤링 자막이 만드는 «앞부분 → 앞부분+뒷부분»
    누적 이벤트를 마지막 형태 하나로 합친다. 업로더 자막은 롤링하지 않고, 반대로
    앞 줄이 뒷 줄의 접두사인 진짜 가사(«君は» → «君は僕を»)가 존재하므로 기본은 끈다.
    """
    out: list[str] = []
    for item in lines:
        raw = item.get("text", "") if isinstance(item, dict) else str(item)
        text = _clean_line(raw)
        if not text:
            continue
        if out:
            prev = out[-1]
            if text == prev:
                continue
            if merge_rolling and text.startswith(prev):
                out[-1] = text
                continue
            if merge_rolling and prev.startswith(text):
                continue
        out.append(text)
    return out


# ── 한 번에: video_id → 가사 ──────────────────────────────────────


def fetch_lyrics_from_captions(video_id: str) -> CaptionLyrics:
    """video_id만으로 원어 자막을 찾아 가사 텍스트를 만든다 (블로킹).

    실패는 전부 CaptionUnavailable로 나가며, 호출부가 code를 보고 클라이언트에게
    "직접 붙여넣기"를 안내할 수 있다.
    """
    if not VIDEO_ID_RE.match(video_id or ""):
        raise CaptionUnavailable("listing_failed", "invalid video_id")

    info = extract_caption_info(video_id)
    track = select_original_track(info)
    raw_lines = download_track_lines(video_id, track.lang, track.auto)
    lines = clean_caption_lines(raw_lines, merge_rolling=track.auto)
    if len(lines) < MIN_LYRIC_LINES:
        raise CaptionUnavailable(
            "too_short",
            f"자막에서 쓸 만한 가사 줄이 {len(lines)}줄뿐이라 가사로 쓸 수 없어요",
        )
    logger.info(
        f"caption lyrics for {video_id}: lang={track.lang} auto={track.auto} "
        f"reason={track.reason} lines={len(lines)}"
    )
    return CaptionLyrics(track=track, lines=lines)
