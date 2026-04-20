/**
 * InferenceSession factory and lazy-singleton registry.
 *
 * Responsibilities:
 *  - Point onnxruntime-web at the WASM files in public/ort/.
 *  - Select execution providers: WebGPU → WASM (SIMD + threads).
 *  - Cap thread count at min(hardwareConcurrency, 4).
 *  - Expose createSession() for one-off creation.
 *  - Expose getSession() for lazy-singleton access per ModelId.
 *
 * Session lifecycle (T2-3e):
 *  - DEIM:            eager – loaded as soon as loadAllSessions() is called.
 *  - PARSeq 30/50/100: lazy – loaded on first runParseq() call for that variant.
 */

import * as ort from "onnxruntime-web";
import type { ModelId, ProgressCallback } from "../types/ortTypes.js";
import type { ModelManifest } from "../types/ortTypes.js";
import { ensureModel } from "./modelCache.js";

// ---------------------------------------------------------------------------
// ORT environment setup (runs once at module load)
// ---------------------------------------------------------------------------

function configureOrtEnv(): void {
  // wasmPaths must end with "/" — Vite copies ORT WASM files to public/ort/.
  ort.env.wasm.wasmPaths = `${import.meta.env.BASE_URL}ort/`;

  // T5-2a: Enable SIMD explicitly (requires COOP/COEP headers already set).
  ort.env.wasm.simd = true;

  const concurrency = typeof navigator !== "undefined"
    ? navigator.hardwareConcurrency ?? 1
    : 1;
  // T5-2a: Cap at 4 threads; more threads show diminishing returns for these
  // model sizes and can increase memory pressure on constrained devices.
  ort.env.wasm.numThreads = Math.min(concurrency, 4);
}

configureOrtEnv();

// ---------------------------------------------------------------------------
// Session creation
// ---------------------------------------------------------------------------

/**
 * Create an InferenceSession from raw model bytes.
 * Tries WebGPU first; falls back to WASM on any error.
 */
export async function createSession(
  modelBytes: ArrayBuffer,
  options?: ort.InferenceSession.SessionOptions,
): Promise<ort.InferenceSession> {
  const baseOptions: ort.InferenceSession.SessionOptions = {
    executionProviders: ["webgpu", "wasm"],
    ...options,
  };
  try {
    return await ort.InferenceSession.create(modelBytes, baseOptions);
  } catch (err) {
    // WebGPU may have failed (unsupported GPU, context lost, etc.)
    console.warn("[ortSession] WebGPU failed, retrying with WASM:", err);
    return await ort.InferenceSession.create(modelBytes, {
      ...baseOptions,
      executionProviders: ["wasm"],
    });
  }
}

// ---------------------------------------------------------------------------
// Lazy-singleton registry
// ---------------------------------------------------------------------------

type SessionEntry = {
  session: ort.InferenceSession | null;
  promise: Promise<ort.InferenceSession> | null;
};

const registry = new Map<ModelId, SessionEntry>([
  ["deim",   { session: null, promise: null }],
  ["rec30",  { session: null, promise: null }],
  ["rec50",  { session: null, promise: null }],
  ["rec100", { session: null, promise: null }],
]);

let _manifest: ModelManifest | null = null;

/**
 * Must be called before any getSession() / loadAllSessions() call.
 */
export function setManifest(manifest: ModelManifest): void {
  _manifest = manifest;
}

function requireManifest(): ModelManifest {
  if (_manifest === null) {
    throw new Error("[ortSession] Call setManifest() before loading sessions.");
  }
  return _manifest;
}

function modelUrl(manifest: ModelManifest, id: ModelId): string {
  const entry = manifest.models.find((m) => m.id === id);
  if (!entry) throw new Error(`[ortSession] Model "${id}" not found in manifest.`);
  return manifest.baseUrl + entry.file;
}

function modelSha256(manifest: ModelManifest, id: ModelId): string {
  const entry = manifest.models.find((m) => m.id === id);
  if (!entry) throw new Error(`[ortSession] Model "${id}" not found in manifest.`);
  return entry.sha256;
}

/**
 * Return the InferenceSession for the given model, initializing it lazily on
 * first call. Concurrent callers for the same id share one pending Promise.
 */
export async function getSession(
  id: ModelId,
  onProgress?: ProgressCallback,
): Promise<ort.InferenceSession> {
  const entry = registry.get(id)!;

  if (entry.session !== null) return entry.session;
  if (entry.promise !== null) return entry.promise;

  const manifest = requireManifest();
  const url = modelUrl(manifest, id);
  const sha256 = modelSha256(manifest, id);

  const p = ensureModel(id, url, sha256, onProgress)
    .then((buf) => createSession(buf))
    .then((sess) => {
      entry.session = sess;
      entry.promise = null;
      return sess;
    });

  entry.promise = p;
  return p;
}

/**
 * Eagerly load the DEIM session. PARSeq sessions remain lazy.
 * Call this after setManifest() to warm up the detector in the background.
 */
export async function loadAllSessions(
  onProgress?: (id: ModelId, loaded: number, total: number) => void,
): Promise<void> {
  await getSession("deim", onProgress ? (l, t) => onProgress("deim", l, t) : undefined);
}

/**
 * T5-7b: Release a single ORT InferenceSession to free GPU/WASM memory.
 * The session will be re-created on next getSession() call.
 */
export async function releaseSession(id: ModelId): Promise<void> {
  const entry = registry.get(id);
  if (!entry) return;
  if (entry.session !== null) {
    try {
      await entry.session.release?.();
    } catch {
      // release() may not be implemented on all EPs; ignore errors.
    }
    entry.session = null;
  }
  entry.promise = null;
}

/**
 * T5-7b: Release all ORT sessions (low-memory mode after OCR completes).
 * Frees WASM linear memory held by all four model sessions.
 */
export async function releaseAllSessions(): Promise<void> {
  await Promise.all(Array.from(registry.keys()).map(releaseSession));
}
