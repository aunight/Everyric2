"""비가창 크레딧 줄 제거 — 자막 정리 단계(키워드)와 정렬 후 단계(VAD 근거) 양쪽.

실측 근거: TXzfQ0cP1P0(【初音ミク】恋愛裁判)의 업로더 자막은 첫 세 줄이
«Music & Lyrics : 40mP» / «Movie : たま» / «Vocal : 初音ミク»이고, 이것이 가사로 정렬돼
화면 첫 줄에 「뮤우짓쿠 안도 레릿쿠스」라는 독음이 박혔다.
"""

from everyric2.server.services.youtube_captions import clean_caption_lines
from everyric2.server.worker import _drop_nonvocal_nonlyric_edges, _looks_non_lyric


class TestCaptionCreditFilter:
    def test_drops_credit_lines_from_real_track(self):
        lines = clean_caption_lines([
            "Music & Lyrics : 40mP",
            "Movie : たま",
            "Vocal : 初音ミク",
            "有罪 無罪 答えろ",
            "君の心の中",
        ])
        assert lines == ["有罪 無罪 答えろ", "君の心の中"]

    def test_drops_japanese_and_korean_credit_forms(self):
        lines = clean_caption_lines([
            "作詞：いよわ",
            "作詞・作曲：いよわ",
            "일러스트 : 누군가",
            "本当の気持ちを",
        ])
        assert lines == ["本当の気持ちを"]

    def test_keeps_lyrics_that_merely_contain_a_colon(self):
        # 머리말이 역할어가 아니면 건드리지 않는다 — 시각 표기·대화체 가사
        lines = clean_caption_lines(["3:00 に会おう", "君: 僕はここにいる", "夜が明けるまで"])
        assert lines == ["3:00 に会おう", "僕はここにいる", "夜が明けるまで"]

    def test_drops_url_only_lines(self):
        lines = clean_caption_lines(["https://example.com/song", "www.example.com", "歌が聞こえる"])
        assert lines == ["歌が聞こえる"]

    def test_credit_head_too_long_is_kept(self):
        # 콜론 앞이 길면 크레딧 표기로 보지 않는다 (가사 문장일 가능성이 높다)
        long_head = "この長い一行はとても長くて役割名ではありません"
        assert clean_caption_lines([f"{long_head}：まだ歌詞"]) == [f"{long_head}：まだ歌詞"]


class TestLooksNonLyric:
    def test_credit_forms(self):
        assert _looks_non_lyric("Vocal : 初音ミク")
        assert _looks_non_lyric("作詞：いよわ")
        assert _looks_non_lyric("Mix/Mastering : someone")

    def test_urls_and_handles(self):
        assert _looks_non_lyric("https://example.com")
        assert _looks_non_lyric("@some_account")

    def test_real_lyrics(self):
        assert not _looks_non_lyric("縋って いつも縋って")
        assert not _looks_non_lyric("3:00")  # 머리말에 글자가 없다
        assert not _looks_non_lyric("I don't know")

    def test_empty_counts_as_non_lyric(self):
        assert _looks_non_lyric("")
        assert _looks_non_lyric("   ")


def _seg(text: str, ratio: float | None) -> dict:
    seg: dict = {"text": text, "start": 0.0, "end": 1.0}
    if ratio is not None:
        seg["debug"] = {"active_ratio": ratio}
    return seg


class TestDropNonVocalEdges:
    def test_drops_leading_credits_on_silence(self):
        segs = [
            _seg("Music & Lyrics : 40mP", 0.0),
            _seg("Vocal : 初音ミク", 0.02),
            _seg("有罪 無罪 答えろ", 0.9),
            _seg("君の心の中", 0.8),
            _seg("最後の一行", 0.7),
            _seg("さらに一行", 0.7),
        ]
        dropped = _drop_nonvocal_nonlyric_edges(segs)
        assert dropped == ["Music & Lyrics : 40mP", "Vocal : 初音ミク"]
        assert [s["text"] for s in segs][0] == "有罪 無罪 答えろ"

    def test_keeps_credit_looking_line_that_is_actually_sung(self):
        # 텍스트 근거만으로는 버리지 않는다 — 발성이 있으면 남긴다
        segs = [
            _seg("Vocal : 初音ミク", 0.8),
            _seg("有罪 無罪 答えろ", 0.9),
            _seg("君の心の中", 0.8),
            _seg("三行目", 0.7),
            _seg("四行目", 0.7),
        ]
        assert _drop_nonvocal_nonlyric_edges(segs) == []
        assert len(segs) == 5

    def test_keeps_real_lyric_misplaced_on_silence(self):
        # 발성 근거만으로는 버리지 않는다 — CTC가 무음에 얹은 진짜 첫 줄을 지우면 안 된다
        segs = [
            _seg("縋って いつも縋って", 0.0),
            _seg("有罪 無罪 答えろ", 0.9),
            _seg("君の心の中", 0.8),
            _seg("三行目", 0.7),
            _seg("四行目", 0.7),
        ]
        assert _drop_nonvocal_nonlyric_edges(segs) == []
        assert len(segs) == 5

    def test_drops_trailing_credits(self):
        segs = [
            _seg("有罪 無罪 答えろ", 0.9),
            _seg("君の心の中", 0.8),
            _seg("三行目", 0.7),
            _seg("四行目", 0.7),
            _seg("https://example.com", 0.0),
        ]
        assert _drop_nonvocal_nonlyric_edges(segs) == ["https://example.com"]
        assert len(segs) == 4

    def test_stops_at_first_non_candidate(self):
        # 조건이 깨지는 줄에서 멈춘다 — 그 뒤의 크레딧까지 찾아 지우지 않는다
        segs = [
            _seg("Vocal : 初音ミク", 0.0),
            _seg("有罪 無罪 答えろ", 0.9),
            _seg("Mix : someone", 0.0),
            _seg("君の心の中", 0.8),
            _seg("三행", 0.7),
            _seg("네행", 0.7),
        ]
        dropped = _drop_nonvocal_nonlyric_edges(segs)
        assert dropped == ["Vocal : 初音ミク"]
        assert any(s["text"] == "Mix : someone" for s in segs)

    def test_never_shrinks_below_min_keep(self):
        segs = [_seg("Vocal : A", 0.0), _seg("Mix : B", 0.0), _seg("歌", 0.9)]
        assert _drop_nonvocal_nonlyric_edges(segs) == []
        assert len(segs) == 3

    def test_missing_vad_debug_is_never_dropped(self):
        # VAD 정보가 없으면 근거가 하나뿐이다 — 건드리지 않는다
        segs = [
            _seg("Vocal : 初音ミク", None),
            _seg("有罪 無罪 答えろ", 0.9),
            _seg("君の心の中", 0.8),
            _seg("三行目", 0.7),
            _seg("四行目", 0.7),
        ]
        assert _drop_nonvocal_nonlyric_edges(segs) == []
