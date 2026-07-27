// vocaloidlyrics.miraheze.org (VocaloidLyrics Wiki) 클라이언트.
// - MediaWiki API로 검색 → 최상위 후보 문서를 파싱한다. 발음은 로마자, 번역은 영어다
//   (vocaro.wikidot.com의 한글 발음/한국어 번역과 대칭축 — SourceResult.pronLang/translationLang
//   로 구분해 소비처가 script를 고른다).
// - service worker에는 DOMParser가 없어 vocaro.ts와 동일하게 정규식으로 파싱한다.
// - 라이선스: 위키 편집 콘텐츠는 CC BY-SA 4.0(출처 표기 필요) — UI에서 출처 페이지 링크를
//   항상 노출한다.
//
// 실곡 조사(2026-07, 「ロキ (Roki)」「ヴァンパイア (Vampire)/DECO*27」「!mperfection」
// 「フラジール (Fragile)/nulut」 4곡 실제 HTML 대조):
// - 가사 표는 `<table class="lyrics-table" id="lyrics-N">`. **한 페이지에 언어가 다른
//   표가 여러 개 있을 수 있다** — Vampire 페이지의 lyrics-2는 만다린 커버(중국어/병음/영어)
//   였다. 그래서 헤더에 `<th class="lyrics-jp">`가 있는 표만 고른다.
// - 헤더 행 `<tr class="lyrics-table-header">`의 `<th>` 개수(2 또는 3)가 실제 열 수다.
//   Japanese+Romaji만 있으면(2열) 번역이 없다(하꼬곡·romaji-only, 실측: "!mperfection").
//   Japanese+Romaji+English(3열)가 일반적이다.
// - 데이터 행은 `<tr>`(가수별 `style="color:..."`는 무시) 안 `<td>` N개가 열 순서대로
//   원문/로마자/[번역]이다.
// - 연 사이 빈 줄은 `<tr><td><br /></td></tr>` — **칸 1개, 내용 없음** 단독행으로 나타나며
//   건너뛴다.
// - 영어만 있는 삽입구(간투사 등)는 `<td colspan="N" class="merged ...">text</td>` **칸
//   1개, 내용 있음** 단독행 — 원문/로마자 구분이 없으므로 text 하나로만 싣는다(발음·번역
//   없음). 실측(4곡)으로는 한 칸 안에서 `<br>`가 텍스트 두 줄을 가르는 경우(칸 하나에
//   여러 물리 줄)는 없었다 — `cellText`가 `<br>`를 공백으로 접어 한 줄로 합치므로, 그런
//   칸을 만나도 깨지지 않고 한 줄로 근사한다(조용한 실패보다 안전한 폴백).
// - `action=parse&page=<제목>`은 제목에 공백+괄호가 섞이면 "missingtitle"로 실패하는
//   경우가 실측됐다(원인 미상 — 인코딩 정규화 이슈로 추정). search가 이미 pageid를 주므로
//   `action=parse&pageid=<id>`로 우회한다(제목 인코딩 문제 자체가 사라진다).

import type { SourceLine, SourceResult } from './sources';

const BASE = 'https://vocaloidlyrics.miraheze.org';
const API = `${BASE}/w/api.php`;
const FETCH_TIMEOUT_MS = 4000;
const LICENSE = 'CC BY-SA 4.0';

interface SearchHit {
  pageid: number;
  title: string;
}

/** 제목으로 곡 페이지를 찾아 가사(원문+로마자+[영어 번역])를 반환. 못 찾으면 null */
export async function mirahezeLookup(title: string): Promise<SourceResult | null> {
  const trimmed = title.trim();
  if (!trimmed) return null;

  const hit = await searchTopHit(trimmed);
  if (!hit) return null;

  const html = await fetchParsedHtml(hit.pageid);
  if (!html) return null;

  const parsed = parseLyricsTable(html);
  if (!parsed || parsed.lines.length === 0) return null;

  return {
    sourceId: 'miraheze',
    // MediaWiki 문서 URL은 공백→'_'만 치환하고 나머지(괄호·'*'·'/' 등)는 그대로 남긴다
    // (encodeURI가 그 규칙과 일치 — encodeURIComponent를 쓰면 '/'까지 %2F로 깨진다).
    pageUrl: `${BASE}/wiki/${encodeURI(hit.title.replace(/ /g, '_'))}`,
    pageTitle: hit.title,
    lines: parsed.lines,
    pronLang: 'romaji',
    translationLang: parsed.hasTranslation ? 'en' : undefined,
    license: LICENSE,
  };
}

// ── 검색 ──────────────────────────────────────────────────────

// MediaWiki 전문검색(list=search)은 제목이 아니라 본문 관련도로 정렬한다 — 짧고 흔한
// 제목일수록 진짜 곡 페이지가 밀려난다(실측: "ロキ" 검색 1위는 그 곡을 수록한 앨범
// 페이지고 진짜 곡 페이지 「ロキ (Roki)」는 2위, "フラジール"는 프로듀서 본인 페이지
// "Nulut"가 1위이고 곡 페이지 「フラジール (Fragile)/nulut」는 5위였다). 곡 페이지 제목은
// 예외 없이 원어 제목으로 **시작한다**(뒤에 "(로마자)"·"/프로듀서"가 붙는 식) — 그래서
// 상위 10개 중 검색어로 시작하는 첫 제목을 우선한다. 없으면(질의가 실제 제목과 다른 등)
// 원래의 최상위 후보로 물러난다 — 아예 못 찾는 것보다는 낫다.
const SEARCH_LIMIT = 10;

async function searchTopHit(title: string): Promise<SearchHit | null> {
  const params = new URLSearchParams({
    action: 'query',
    list: 'search',
    srsearch: title,
    srlimit: String(SEARCH_LIMIT),
    format: 'json',
    origin: '*',
  });
  const data = await getJSON<{ query?: { search?: SearchHit[] } }>(`${API}?${params}`);
  const hits = data?.query?.search ?? [];
  if (hits.length === 0) return null;
  const titleMatch = hits.find(h => h.title.startsWith(title));
  const hit = titleMatch ?? hits[0];
  return { pageid: hit.pageid, title: hit.title };
}

async function fetchParsedHtml(pageid: number): Promise<string | null> {
  const params = new URLSearchParams({
    action: 'parse',
    pageid: String(pageid),
    prop: 'text',
    format: 'json',
    origin: '*',
  });
  const data = await getJSON<{ parse?: { text?: { '*': string } } }>(`${API}?${params}`);
  return data?.parse?.text?.['*'] ?? null;
}

async function getJSON<T>(url: string): Promise<T | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// ── 가사 표 파싱 ─────────────────────────────────────────────

/** `<table class="lyrics-table" ...>`들 중 헤더에 lyrics-jp가 있는 표(원문이 일본어인 표) */
function findJapaneseLyricsTable(html: string): string | null {
  const re = /<table class="lyrics-table"[^>]*>([\s\S]*?)<\/table>/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    if (m[1].includes('class="lyrics-jp"')) return m[1];
  }
  return null;
}

function parseLyricsTable(html: string): { lines: SourceLine[]; hasTranslation: boolean } | null {
  const table = findJapaneseLyricsTable(html);
  if (!table) return null;

  const headerMatch = /<tr class="lyrics-table-header">([\s\S]*?)<\/tr>/.exec(table);
  if (!headerMatch) return null;
  const headerCount = (headerMatch[1].match(/<th/g) ?? []).length;
  if (headerCount < 2) return null; // 최소 원문+로마자

  const body = table.slice(headerMatch.index + headerMatch[0].length);
  const lines: SourceLine[] = [];
  const rowRe = /<tr(?:\s[^>]*)?>([\s\S]*?)<\/tr>/g;
  let m: RegExpExecArray | null;
  while ((m = rowRe.exec(body)) !== null) {
    for (const line of rowToLines(extractCells(m[1]))) lines.push(line);
  }
  return { lines, hasTranslation: headerCount >= 3 };
}

/** 행 하나(칸 목록)를 SourceLine 0~n개로. 빈 연 구분·삽입구·정상 행을 여기서 가른다 */
function rowToLines(cells: string[]): SourceLine[] {
  if (cells.length === 0) return [];
  if (cells.length === 1) {
    // <td><br /></td> 단독(내용 없음) — 연 사이 공백 줄, 가사가 아니다
    if (!cells[0]) return [];
    // <td colspan="N" class="merged ...">text</td> — 원문/로마자 구분 없는 삽입구
    return [{ text: cells[0] }];
  }
  const [text, pronunciation, translation] = cells;
  if (!text) return [];
  return [{ text, pronunciation: pronunciation || undefined, translation: translation || undefined }];
}

function extractCells(rowHtml: string): string[] {
  const cells: string[] = [];
  const re = /<td[^>]*>([\s\S]*?)<\/td>/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(rowHtml)) !== null) cells.push(cellText(m[1]));
  return cells;
}

function cellText(cellHtml: string): string {
  return decodeEntities(
    cellHtml
      .replace(/<br\s*\/?>/g, ' ')
      .replace(/<[^>]+>/g, ''),
  ).replace(/\s+/g, ' ').trim();
}

function decodeEntities(s: string): string {
  return s
    .replace(/&#(\d+);/g, (_, code: string) => String.fromCodePoint(Number(code)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, code: string) => String.fromCodePoint(parseInt(code, 16)))
    .replace(/&nbsp;/g, ' ')
    .replace(/&quot;/g, '"')
    .replace(/&#039;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}
