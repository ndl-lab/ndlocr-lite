/**
 * DEIM layout detector — onnxruntime-web wrapper.
 *
 * Feed contract (Phase 1 confirmed):
 *   Key "images"              Float32Array  shape (1, 3, H, W)   — H=W=1024
 *   Key "orig_target_sizes"   BigInt64Array shape (1, 2)         — [[origH, origW]]
 *
 * Output contract:
 *   [classIds, bboxes, scores]         or
 *   [classIds, bboxes, scores, charCounts]
 *   classIds  : Float32Array  (1, N)
 *   bboxes    : Float32Array  (1, N, 4)
 *   scores    : Float32Array  (1, N)
 *   charCounts: Float32Array  (1, N)  — optional 4th output
 *
 * Python DEIMDetector.postprocess() calls np.squeeze on each, so the JS side
 * returns the raw (unsqueezed) tensors for symmetry; Python handles squeezing.
 */

import * as ort from "onnxruntime-web";
import type { DetectorFeeds, DetectorOutputs, ProgressCallback } from "../types/ortTypes.js";
import { getSession } from "./ortSession.js";

/**
 * Run DEIM inference.
 *
 * The DEIM session is lazily initialized on the first call; subsequent calls
 * return instantly from the singleton registry.
 *
 * @throws RangeError on OOM → caller should retry after freeing resources.
 */
export async function runDeim(
  feeds: DetectorFeeds,
  onProgress?: ProgressCallback,
): Promise<DetectorOutputs> {
  const session = await getSession("deim", onProgress);

  const inputTensors: Record<string, ort.Tensor> = {
    images: new ort.Tensor("float32", feeds.images, feeds.imagesShape),
    orig_target_sizes: new ort.Tensor("int64", feeds.orig_target_sizes, [1, 2]),
  };

  let results: ort.InferenceSession.OnnxValueMapType;
  try {
    results = await session.run(inputTensors);
  } catch (err) {
    if (err instanceof RangeError) {
      // OOM: propagate clearly so the caller can free memory and retry.
      throw new RangeError(
        `[detector] Out of memory during DEIM inference. ` +
          `Free resources or switch to WASM execution provider.`,
        { cause: err },
      );
    }
    throw err;
  }

  const outputNames = session.outputNames;
  const classIds  = getFloat32Output(results, outputNames, 0, "classIds");
  const bboxes    = getFloat32Output(results, outputNames, 1, "bboxes");
  const scores    = getFloat32Output(results, outputNames, 2, "scores");

  const out: DetectorOutputs = { classIds, bboxes, scores };

  if (outputNames.length >= 4) {
    out.charCounts = getFloat32Output(results, outputNames, 3, "charCounts");
  }

  return out;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getFloat32Output(
  results: ort.InferenceSession.OnnxValueMapType,
  names: readonly string[],
  index: number,
  label: string,
): Float32Array {
  const name = names[index];
  if (name === undefined) {
    throw new Error(`[detector] Expected output at index ${index} (${label}) but only ${names.length} outputs found.`);
  }
  const tensor = results[name];
  if (tensor === undefined) {
    throw new Error(`[detector] Output tensor "${name}" missing from DEIM results.`);
  }
  if (!(tensor.data instanceof Float32Array)) {
    throw new Error(`[detector] Expected Float32Array for "${name}", got ${tensor.type}.`);
  }
  return tensor.data;
}
