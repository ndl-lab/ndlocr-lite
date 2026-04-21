/**
 * Shared TypeScript types for the Python <-> JS inference boundary.
 *
 * Feed/output shapes are fixed by Phase 1 analysis:
 *   DEIMDetector.preprocess()  → {"images": (1,3,H,W), "orig_target_sizes": int64(1,2)}
 *   PARSeqRecognizer.preprocess() → {"input": (1,3,H,W)}
 *
 * These types are consumed by detector.ts / recognizer.ts (Phase 2) and will
 * be re-exported to the Pyodide bridge in Phase 3.
 */

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------

export interface ModelEntry {
  id: string;
  file: string;
  sha256: string;
  size: number;
}

export interface ModelManifest {
  version: string;
  baseUrl: string;
  models: ModelEntry[];
}

// ---------------------------------------------------------------------------
// Model IDs
// ---------------------------------------------------------------------------

export type ModelId = "deim" | "rec30" | "rec50" | "rec100";

/** PARSeq variant mapped to its fixed input width in pixels. */
export const PARSEQ_INPUT_SIZES: Record<"rec30" | "rec50" | "rec100", { w: number; h: number }> = {
  rec30:  { w: 256, h: 16 },
  rec50:  { w: 384, h: 16 },
  rec100: { w: 768, h: 16 },
};

// ---------------------------------------------------------------------------
// Progress callback
// ---------------------------------------------------------------------------

export type ProgressCallback = (loaded: number, total: number) => void;

// ---------------------------------------------------------------------------
// DEIM (layout detector)
// Phase 1 confirmed: feed key is "images" (not "image")
// ---------------------------------------------------------------------------

export interface DetectorFeeds {
  /** Float32 pixel data, layout (1, 3, H, W). H = W = 1024 for DEIM. */
  images: Float32Array;
  imagesShape: [1, 3, number, number];
  /** int64 original image dimensions [[origH, origW]], encoded as BigInt64Array. */
  orig_target_sizes: BigInt64Array;
}

export interface DetectorOutputs {
  /** shape (1, N) — model outputs float; Python postprocess casts to int. */
  classIds: Float32Array;
  /** shape (1, N, 4) */
  bboxes: Float32Array;
  /** shape (1, N) */
  scores: Float32Array;
  /** shape (1, N) — optional fourth output from DEIM. */
  charCounts?: Float32Array;
}

// ---------------------------------------------------------------------------
// PARSeq (text recognizer)
// Phase 1 confirmed: feed key is "input"
// ---------------------------------------------------------------------------

export interface RecognizerFeeds {
  /** Float32 pixel data, layout (1, 3, H, W). W depends on variant. */
  input: Float32Array;
  shape: [1, 3, number, number];
}

export interface RecognizerOutputs {
  /** Raw logit tensor, shape (1, seqLen, numChars). */
  logits: Float32Array;
  logitsShape: [number, number, number];
}
