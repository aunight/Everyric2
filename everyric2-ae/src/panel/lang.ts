import type { PronScript, PronVariants, SyncLine, TranslationLanguage } from "./types";

/**
 * 발음 표기와 번역 언어의 공유 계약.
 *
 * 크롬 확장의 `src/lib/lang.ts`와 **같은 규칙**이어야 한다. 서버는 한 세그에 표기별
 * 발음(hangul/romaji/kana)을 함께 실어 보내고, 어느 것을 보여줄지는 클라이언트가 고른다.
 */

/** 'auto'면 번역 언어를 따른다: en→romaji, ja→kana, 그 밖(ko·zh)→hangul. */
export function resolveScript(settings: {
  pronunciationScript: PronScript | "auto";
  translationLanguage: TranslationLanguage;
}): PronScript {
  if (settings.pronunciationScript !== "auto") return settings.pronunciationScript;
  if (settings.translationLanguage === "en") return "romaji";
  if (settings.translationLanguage === "ja") return "kana";
  return "hangul";
}

/**
 * 표시용 발음 문자열. 표시 지점은 반드시 이 함수를 거친다 — `line.pronunciation`을 직접
 * 읽으면 안 된다.
 *
 * **레거시 폴백은 script가 'hangul'일 때만.** `line.pronunciation`은 항상 한글 값이라,
 * romaji·kana를 보는 사용자에게 그대로 주면 자기 표기가 아닌 한글 독음이 뜬다. 표기가
 * 없으면 undefined를 돌려줘 발음 줄 자체를 생략하게 한다.
 */
export function resolvedPronunciation(line: SyncLine, script: PronScript): string | undefined {
  const fromDict = line.pron?.[script];
  if (fromDict) return fromDict;
  return script === "hangul" ? line.pronunciation : undefined;
}

/** 응답의 표기 dict를 안전하게 걷어낸다. 값이 문자열인 키만 남긴다. */
export function normalizePronVariants(value: unknown): PronVariants | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const source = value as Record<string, unknown>;
  const variants: PronVariants = {};
  for (const script of ["hangul", "romaji", "kana"] as const) {
    const entry = source[script];
    if (typeof entry === "string" && entry.trim() !== "") variants[script] = entry;
  }
  return Object.keys(variants).length > 0 ? variants : undefined;
}

/**
 * 이 문서에서 실제로 고를 수 있는 번역 언어.
 *
 * 서버가 알려준 `availableLangs`가 있으면 그것을 쓰고, 없으면(구버전 응답·로컬 JSON)
 * 세그에 번역이 하나라도 있을 때 한국어만 있다고 본다 — 레거시 번역 슬롯은 늘 한국어였다.
 */
export function selectableLanguages(document: {
  availableLangs?: string[];
  lines: SyncLine[];
}): TranslationLanguage[] {
  const known: TranslationLanguage[] = ["ko", "en", "ja"];
  const fromServer = (document.availableLangs ?? []).filter((lang): lang is TranslationLanguage =>
    known.indexOf(lang as TranslationLanguage) >= 0,
  );
  if (fromServer.length > 0) return fromServer;
  return document.lines.some((line) => line.translation) ? ["ko"] : [];
}

/**
 * 번역 언어를 바꾼 문서를 만든다. 서버가 `translationsByLang`을 함께 줬으면 재조회 없이
 * 갈아끼운다 — 확장이 언어 칩으로 즉시 전환하는 것과 같은 경로다.
 *
 * 해당 언어 배열이 없으면 문서를 그대로 돌려준다(호출부가 재조회를 결정한다).
 */
export function withTranslationLanguage<T extends { lines: SyncLine[]; translationsByLang?: Record<string, Array<string | null>>; translationLang?: string }>(
  document: T,
  lang: TranslationLanguage,
): T {
  const table = document.translationsByLang?.[lang];
  if (!table) return document;
  return {
    ...document,
    translationLang: lang,
    lines: document.lines.map((line, index) => {
      const translation = table[index];
      if (typeof translation === "string" && translation.trim() !== "") {
        return { ...line, translation };
      }
      // 그 언어에 이 줄의 번역이 없으면 남의 언어 번역을 남겨 두지 않는다.
      const { translation: _dropped, ...rest } = line;
      return rest as SyncLine;
    }),
  };
}
