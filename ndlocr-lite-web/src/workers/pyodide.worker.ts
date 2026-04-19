/**
 * Pyodide Web Worker — Phase 3
 *
 * Architecture:
 *   Main Thread ──Comlink──► this Worker
 *                              ├─ Pyodide runtime (CDN)
 *                              ├─ ndlocr_web Python package (wheel)
 *                              └─ onnxruntime-web sessions (same worker)
 *
 * The worker runs ndlocr_web.pipeline.run_ocr_on_image() inside Pyodide.
 * ONNX inference is dispatched to JS helpers (jsDeimInfer / jsParseqInfer)
 * defined in this file, which call the Phase-2 ORT session wrappers directly.
 *
 * Progress is reported via a Comlink-proxied callback in five stages:
 *   pyodide → packages → wheel → models → init
 */

import * as Comlink from "comlink";
import { setManifest, getSession } from "../ort/ortSession.js";
import { runDeim } from "../ort/detector.js";
import { runParseq } from "../ort/recognizer.js";
import type {
  ModelManifest,
  DetectorFeeds,
  RecognizerFeeds,
} from "../types/ortTypes.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ProgressFn = (stage: string, percent: number) => void;

export interface OcrResult {
  xml: string;
  text: string;
  /** Parsed JSON from ndlocr_web (contains contents + imginfo). */
  json: unknown;
  /** PNG bytes when viz=true, otherwise null. */
  vizPng: Uint8Array | null;
}

// ---------------------------------------------------------------------------
// Pyodide CDN URL — bump version here when upgrading
// ---------------------------------------------------------------------------

const PYODIDE_VERSION = "0.27.2";
const PYODIDE_CDN = `https://cdn.jsdelivr.net/npm/pyodide@${PYODIDE_VERSION}/`;

// ---------------------------------------------------------------------------
// Module-level state (persists across ocr() calls)
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let pyodide: any = null;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let runOcrPy: any = null;   // Python async function: (imageData, w, h, name, viz) → OcrResult
let isReady = false;

// ---------------------------------------------------------------------------
// JS inference helpers exposed to Python via pyodide.globals
// ---------------------------------------------------------------------------

/**
 * Called by Python JSDeimInfer.__call__():
 *   imagesJs   – Float32Array  (1×3×H×W, from pyodide.ffi.to_js)
 *   shapeJs    – JS Array      [1, 3, H, W]
 *   origSizesJs – BigInt64Array (1×2)
 */
async function jsDeimInfer(
  imagesJs: Float32Array,
  shapeJs: ArrayLike<number>,
  origSizesJs: BigInt64Array,
): Promise<{
  classIds: Float32Array;
  bboxes: Float32Array;
  scores: Float32Array;
  charCounts: Float32Array | null;
  N: number;
}> {
  const imagesShape = Array.from(shapeJs) as [1, 3, number, number];

  const feeds: DetectorFeeds = {
    images: imagesJs,
    imagesShape,
    orig_target_sizes: origSizesJs,
  };

  const out = await runDeim(feeds);
  const N = out.scores.length;

  return {
    classIds: out.classIds,
    bboxes: out.bboxes,
    scores: out.scores,
    charCounts: out.charCounts ?? null,
    N,
  };
}

/**
 * Called by Python JSParseqInfer.__call__():
 *   variant  – '30' | '50' | '100'
 *   inputJs  – Float32Array (1×3×H×W)
 *   shapeJs  – JS Array     [1, 3, H, W]
 */
async function jsParseqInfer(
  variant: string,
  inputJs: Float32Array,
  shapeJs: ArrayLike<number>,
): Promise<{ logits: Float32Array; logitsShape: number[] }> {
  const shape = Array.from(shapeJs) as [1, 3, number, number];
  const feeds: RecognizerFeeds = { input: inputJs, shape };
  const out = await runParseq(variant as "30" | "50" | "100", feeds);
  return { logits: out.logits, logitsShape: out.logitsShape };
}

// ---------------------------------------------------------------------------
// Python setup code (runs once during init)
// ---------------------------------------------------------------------------

const PYTHON_SETUP = `
import sys
import numpy as np
import js
from ndlocr_web.detector import DEIMDetector
from ndlocr_web.recognizer import PARSeqRecognizer
from ndlocr_web.bridge import JSDeimInfer, JSParseqInfer
from ndlocr_web.config import load_class_names, load_charlist
from ndlocr_web.pipeline import run_ocr_on_image

_classes = load_class_names()
_charlist = load_charlist()

_detector = DEIMDetector(
    infer=JSDeimInfer(js.jsDeimInfer),
    classes=_classes,
)
_recognizer30 = PARSeqRecognizer(
    infer=JSParseqInfer(js.jsParseqInfer, "30"),
    charlist=_charlist,
    input_size=(256, 16),
)
_recognizer50 = PARSeqRecognizer(
    infer=JSParseqInfer(js.jsParseqInfer, "50"),
    charlist=_charlist,
    input_size=(384, 16),
)
_recognizer100 = PARSeqRecognizer(
    infer=JSParseqInfer(js.jsParseqInfer, "100"),
    charlist=_charlist,
    input_size=(768, 16),
)

async def _run_ocr(image_data_js, width_js, height_js, img_name_js, viz_js):
    width = int(width_js)
    height = int(height_js)
    img_name = str(img_name_js)
    viz = bool(viz_js)
    rgba = np.asarray(image_data_js, dtype=np.uint8).reshape(height, width, 4)
    rgb = np.ascontiguousarray(rgba[:, :, :3])
    return await run_ocr_on_image(
        rgb, _detector, _recognizer30, _recognizer50, _recognizer100,
        img_name=img_name, viz=viz,
    )
`;

// ---------------------------------------------------------------------------
// Worker API (exposed via Comlink)
// ---------------------------------------------------------------------------

const workerApi = {
  /**
   * Initialize Pyodide, load packages, install ndlocr_web wheel, and warm up
   * the ORT sessions.  Must be awaited before calling ocr().
   *
   * @param onProgress  Comlink-proxied progress callback from the main thread.
   * @param manifest    ModelManifest fetched by the main thread.
   */
  async init(onProgress: ProgressFn, manifest: ModelManifest): Promise<void> {
    // ── Stage 1: Pyodide runtime ──────────────────────────────────────────
    onProgress("pyodide", 0);

    // Dynamic CDN import — @vite-ignore prevents Vite from trying to bundle
    // the remote URL.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const pyodideModule = await import(/* @vite-ignore */ `${PYODIDE_CDN}pyodide.mjs`) as any;
    pyodide = await pyodideModule.loadPyodide({ indexURL: PYODIDE_CDN });

    onProgress("pyodide", 100);

    // ── Stage 2: Scientific packages ──────────────────────────────────────
    onProgress("packages", 0);
    await pyodide.loadPackage(["numpy", "Pillow", "lxml", "networkx", "pyyaml"]);
    onProgress("packages", 100);

    // ── Stage 3: ndlocr_web wheel ─────────────────────────────────────────
    onProgress("wheel", 0);
    await pyodide.runPythonAsync(`
import micropip
await micropip.install("/wheels/ndlocr_web-0.1.0-py3-none-any.whl")
`);
    onProgress("wheel", 100);

    // ── Stage 4: ONNX models ──────────────────────────────────────────────
    onProgress("models", 0);
    setManifest(manifest);

    const modelIds: Array<"deim" | "rec30" | "rec50" | "rec100"> = ["deim", "rec30", "rec50", "rec100"];
    const total = modelIds.length;
    for (let i = 0; i < total; i++) {
      const id = modelIds[i]!;
      await getSession(id, (loaded, bytes) => {
        const base = (i / total) * 100;
        const share = (1 / total) * 100;
        onProgress("models", Math.round(base + (loaded / (bytes || 1)) * share));
      });
    }
    onProgress("models", 100);

    // ── Stage 5: Python pipeline setup ────────────────────────────────────
    onProgress("init", 0);

    // Expose JS inference helpers to the Python 'js' module namespace.
    pyodide.globals.set("jsDeimInfer", jsDeimInfer);
    pyodide.globals.set("jsParseqInfer", jsParseqInfer);

    await pyodide.runPythonAsync(PYTHON_SETUP);
    runOcrPy = pyodide.globals.get("_run_ocr");

    onProgress("init", 100);
    isReady = true;
  },

  /**
   * Run OCR on an ImageBitmap.
   * The bitmap is transferred into the worker (Transferable) and converted
   * to RGBA via OffscreenCanvas before being passed into Pyodide/Python.
   *
   * @param bitmap   ImageBitmap — consumed (closed) after pixel extraction.
   * @param imgName  Filename embedded in the output XML.
   * @param viz      When true, include a PNG visualization in the result.
   */
  async ocr(
    bitmap: ImageBitmap,
    imgName: string = "image.jpg",
    viz: boolean = false,
  ): Promise<OcrResult> {
    if (!isReady || !runOcrPy) {
      throw new Error("[pyodide.worker] Not initialized. Call init() first.");
    }

    // ── Pixel extraction ──────────────────────────────────────────────────
    const { width, height } = bitmap;
    const canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(bitmap, 0, 0);
    bitmap.close();
    const { data: rgbaData } = ctx.getImageData(0, 0, width, height);

    // ── Python OCR call ───────────────────────────────────────────────────
    const pyResult = await runOcrPy(rgbaData, width, height, imgName, viz);

    // ── Result extraction ─────────────────────────────────────────────────
    const xml: string = pyResult.xml;
    const text: string = pyResult.text;

    // pyResult.json is a Python dict → convert to JS object
    const jsonObj = pyResult.json.toJs({ dict_converter: Object.fromEntries });
    // json field in OcrResult should be plain JS; serialise and re-parse to
    // strip any remaining Pyodide proxies.
    const jsonPlain = JSON.parse(JSON.stringify(jsonObj));

    let vizPng: Uint8Array | null = null;
    if (pyResult.viz_png !== null && pyResult.viz_png !== undefined) {
      vizPng = new Uint8Array(pyResult.viz_png.toJs());
    }

    // Release Python-side reference
    pyResult.destroy();

    return { xml, text, json: jsonPlain, vizPng };
  },
};

// Expose the API so the main thread can call it via Comlink.wrap()
Comlink.expose(workerApi);
