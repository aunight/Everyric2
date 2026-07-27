"""가사 위키 소스 어댑터 — 확장(everyric2-chrome/src/lib)의 파서를 서버로 포트한 것.

두 위키가 한 곡의 3개 언어를 채운다: ``vocaro``(원문 일본어 + 한글 독음 + 한국어 번역),
``miraheze``(원문 일본어 + 로마자 + 영어 번역). 둘 다 :class:`SourceLine` 한 모양으로
나오므로 소비처는 소스별 분기 없이 다룰 수 있다.
"""

from everyric2.sources.base import SourceLine, WikiFetcher, cell_text

__all__ = ["SourceLine", "WikiFetcher", "cell_text"]
