/**
 * PARSeq text recognizer — onnxruntime-web wrappers for all three variants.
 *
 * Feed contract (Phase 1 confirmed):
 *   Key "input"  Float32Array  shape (1, 3, H, W)
 *   Input sizes per variant (W × H):
 *     rec30  → 256 × 16
 *     rec50  → 384 × 16
 *     rec100 → 768 × 16
 *
 * Output:
 *   Logit tensor, shape (1, seqLen, numChars) — caller decodes to text.
 *   Python PARSeqRecognizer.postprocess() applies argmax + charset lookup.
 *
 * All three PARSeq sessions are lazy-loaded on first use (T2-3e).
 */

import * as ort from "onnxruntime-web";
import type {
  RecognizerFeeds,
  RecognizerOutputs,
  ProgressCallback,
} from "../types/ortTypes.js";
import { PARSEQ_INPUT_SIZES } from "../types/ortTypes.js";
import { getSession } from "./ortSession.js";

type ParseqVariant = "30" | "50" | "100";

const VARIANT_TO_MODEL_ID = {
  "30":  "rec30",
  "50":  "rec50",
  "100": "rec100",
} as const satisfies Record<ParseqVariant, "rec30" | "rec50" | "rec100">;

/**
 * Run PARSeq inference for the given width variant.
 *
 * The session is lazily initialized on first call per variant.
 *
 * @param variant  '30' | '50' | '100' — corresponds to rec30 / rec50 / rec100.
 * @param feeds    Input tensor (Float32Array, shape 1×3×H×W).
 * @param onProgress  Download progress callback (fired only on first call).
 * @returns  Raw logit tensor and its shape.
 * @throws   RangeError on OOM.
 */
export async function runParseq(
  variant: ParseqVariant,
  feeds: RecognizerFeeds,
  onProgress?: ProgressCallback,
): Promise<RecognizerOutputs> {
  const modelId = VARIANT_TO_MODEL_ID[variant];
  const session = await getSession(modelId, onProgress);

  const expectedSize = PARSEQ_INPUT_SIZES[modelId];
  const [, , h, w] = feeds.shape;
  if (h !== expectedSize.h || w !== expectedSize.w) {
    throw new Error(
      `[recognizer] rec${variant} expects input (H=${expectedSize.h}, W=${expectedSize.w}), ` +
        `got (H=${h}, W=${w}).`,
    );
  }

  const inputTensors: Record<string, ort.Tensor> = {
    input: new ort.Tensor("float32", feeds.input, feeds.shape),
  };

  let results: ort.InferenceSession.OnnxValueMapType;
  try {
    results = await session.run(inputTensors);
  } catch (err) {
    if (err instanceof RangeError) {
      throw new RangeError(
        `[recognizer] Out of memory during PARSeq rec${variant} inference.`,
        { cause: err },
      );
    }
    throw err;
  }

  const outputName = session.outputNames[0];
  if (outputName === undefined) {
    throw new Error(`[recognizer] PARSeq rec${variant} returned no output tensors.`);
  }
  const tensor = results[outputName];
  if (tensor === undefined) {
    throw new Error(`[recognizer] Output tensor "${outputName}" missing from PARSeq results.`);
  }
  if (!(tensor.data instanceof Float32Array)) {
    throw new Error(`[recognizer] Expected Float32Array output from PARSeq, got ${tensor.type}.`);
  }

  const [b, seqLen, numChars] = tensor.dims as [number, number, number];
  return {
    logits: tensor.data,
    logitsShape: [b, seqLen, numChars],
  };
}
