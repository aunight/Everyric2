import type { MicDisplayMode, Settings } from '../types';

/** 舊版 line/dots 都是獨立麥克風軌跡；其餘未知值回到新的日 K 預設。 */
export function normalizeMicDisplayMode(value: unknown): MicDisplayMode {
  if (value === 'trace' || value === 'line' || value === 'dots') return 'trace';
  return 'notes';
}

/**
 * 採點是單一功能開關：打開後必須同時有麥克風輸入。
 *
 * 關閉採點時不反向關閉 micPitch，因為使用者可能只是想看自己的音高而不計分。
 */
export function normalizeScoringSettingsPatch(
  patch: Partial<Settings>,
): Partial<Settings> {
  return patch.karaokeScoring === true ? { ...patch, micPitch: true } : patch;
}

/** 舊版可能已存下「採點開、麥克風關」；讀取時也維持同一個單開關不變式。 */
export function normalizeScoringSettings<
  T extends {
    karaokeScoring: boolean;
    micPitch: boolean;
    micDisplayMode?: unknown;
  },
>(settings: T): Omit<T, 'micDisplayMode'> & { micDisplayMode: MicDisplayMode } {
  return {
    ...settings,
    micPitch: settings.karaokeScoring ? true : settings.micPitch,
    micDisplayMode: normalizeMicDisplayMode(settings.micDisplayMode),
  };
}
