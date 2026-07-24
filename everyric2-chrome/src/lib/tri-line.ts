/**
 * 검색 결과 가사처럼 「일어 원문 / 한글 독음 / 한국어 번역」 3줄이 반복되는
 * 붙여넣기 텍스트를 감지해 분해한다.
 * 지원 형태: 빈 줄로 구분된 3줄 블록들, 또는 빈 줄 없이 3줄 주기로 이어지는 텍스트.
 * 확신이 없으면 null — 호출부는 원문 그대로 생성 경로를 탄다.
 */

export interface TriLine {
  text: string;
  pronunciation?: string;
  translation?: string;
}

const RE_KANA = /[぀-ヿ]/;
const RE_CJK = /[一-鿿]/;
const RE_HANGUL = /[가-힣]/g;

function hangulRatio(s: string): number {
  const total = s.replace(/\s/g, '').length;
  if (total === 0) return 0;
  return (s.match(RE_HANGUL)?.length ?? 0) / total;
}

/** 일어 원문 줄: 가나나 한자를 포함하고 한글은 거의 없음 */
function isJa(s: string): boolean {
  return (RE_KANA.test(s) || RE_CJK.test(s)) && hangulRatio(s) < 0.15;
}

/** 한글 줄(독음·번역): 한글이 절반 이상이고 가나 없음 */
function isKo(s: string): boolean {
  return hangulRatio(s) >= 0.5 && !RE_KANA.test(s);
}

export function parseTriLineLyrics(raw: string): TriLine[] | null {
  const rawLines = raw.split('\n').map(s => s.trim());
  const blocks: string[][] = [];
  let cur: string[] = [];
  for (const ln of rawLines) {
    if (ln) {
      cur.push(ln);
      continue;
    }
    if (cur.length) blocks.push(cur);
    cur = [];
  }
  if (cur.length) blocks.push(cur);
  const flat = blocks.flat();
  if (flat.length < 6) return null; // 두 세트는 있어야 패턴으로 인정

  let triples: string[][] | null = null;
  if (blocks.length >= 2 && blocks.every(b => b.length === 3)) {
    triples = blocks;
  } else if (flat.length % 3 === 0) {
    triples = [];
    for (let i = 0; i < flat.length; i += 3) triples.push(flat.slice(i, i + 3));
  }
  if (!triples) return null;

  // 각 세트가 (일어, 한글, 한글) 꼴인지 — 80% 이상이면 혼합 가사로 판정.
  // 간주 표기 등 예외 줄이 섞여도 통과하도록 완전 일치를 요구하지 않는다.
  let ok = 0;
  for (const [a, b, c] of triples) {
    if (isJa(a) && isKo(b) && isKo(c)) ok++;
  }
  if (ok / triples.length < 0.8) return null;

  return triples.map(([a, b, c]) => ({ text: a, pronunciation: b, translation: c }));
}
