import { create } from "zustand";
import type { OcrResult } from "../lib/OcrClient.js";

export type Phase = "idle" | "loading" | "ready" | "ocr" | "done" | "error";

export interface ProgressEntry {
  stage: string;
  percent: number;
}

export interface CurrentImage {
  objectUrl: string;
  width: number;
  height: number;
  fileName: string;
}

interface OcrStore {
  phase: Phase;
  progress: Record<string, number>;
  currentImage: CurrentImage | null;
  result: OcrResult | null;
  error: string | null;
  lang: "en" | "ja";
  highlightedLineId: number | null;

  setPhase: (phase: Phase) => void;
  updateProgress: (stage: string, percent: number) => void;
  setImage: (url: string, width: number, height: number, fileName: string) => void;
  setResult: (result: OcrResult) => void;
  setError: (error: string) => void;
  setLang: (lang: "en" | "ja") => void;
  setHighlightedLineId: (id: number | null) => void;
  reset: () => void;
}

export const useOcrStore = create<OcrStore>((set, get) => ({
  phase: "idle",
  progress: {},
  currentImage: null,
  result: null,
  error: null,
  lang: navigator.language.startsWith("ja") ? "ja" : "en",
  highlightedLineId: null,

  setPhase: (phase) => set({ phase }),

  updateProgress: (stage, percent) =>
    set((s) => ({ progress: { ...s.progress, [stage]: percent } })),

  setImage: (url, width, height, fileName) => {
    const prev = get().currentImage?.objectUrl;
    if (prev) URL.revokeObjectURL(prev);
    set({ currentImage: { objectUrl: url, width, height, fileName } });
  },

  setResult: (result) => set({ result }),

  setError: (error) => set({ phase: "error", error }),

  setLang: (lang) => set({ lang }),

  setHighlightedLineId: (id) => set({ highlightedLineId: id }),

  reset: () => {
    const prev = get().currentImage?.objectUrl;
    if (prev) URL.revokeObjectURL(prev);
    set({
      phase: "ready",
      result: null,
      currentImage: null,
      error: null,
      highlightedLineId: null,
    });
  },
}));
