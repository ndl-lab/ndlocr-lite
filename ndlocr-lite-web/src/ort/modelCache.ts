/**
 * Cache Storage API wrapper for ONNX model files.
 *
 * Primary storage: Cache Storage API (`caches.open`).
 * Fallback storage: IndexedDB via idb-keyval (Private Mode / Safari quirk mode).
 *
 * Flow:
 *   1. Check Cache Storage for the model URL.
 *   2. If found, stream bytes out and return ArrayBuffer.
 *   3. If not found, fetch with ReadableStream progress tracking.
 *   4. Verify SHA-256 of downloaded bytes.
 *   5. Store in Cache Storage (or IndexedDB fallback).
 *   6. Return ArrayBuffer (zero-copy transfer to caller).
 */

import { get as idbGet, set as idbSet } from "idb-keyval";
import type { ProgressCallback } from "../types/ortTypes.js";

const CACHE_NAME = "ndlocr-models-v1";

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Ensure a model is available locally, downloading and caching it if needed.
 *
 * @param id       Human-readable model identifier (e.g. "deim").
 * @param url      Full URL to the .onnx file.
 * @param sha256   Expected hex-encoded SHA-256 digest.
 * @param onProgress Optional progress callback (loaded bytes, total bytes).
 * @returns ArrayBuffer containing the raw ONNX model bytes.
 */
export async function ensureModel(
  id: string,
  url: string,
  sha256: string,
  onProgress?: ProgressCallback,
): Promise<ArrayBuffer> {
  // 1. Try Cache Storage
  if (typeof caches !== "undefined") {
    const buf = await tryReadFromCacheStorage(url);
    if (buf !== null) {
      return buf;
    }
    // Not cached — download, verify, store
    const downloaded = await fetchWithProgress(url, onProgress);
    await verifyHash(downloaded, sha256, id);
    await writeToCacheStorage(url, downloaded);
    return downloaded;
  }

  // 2. Fallback: IndexedDB
  const idbKey = `ndlocr-model:${id}`;
  const cached: ArrayBuffer | undefined = await idbGet(idbKey);
  if (cached !== undefined) {
    return cached;
  }
  const downloaded = await fetchWithProgress(url, onProgress);
  await verifyHash(downloaded, sha256, id);
  await idbSet(idbKey, downloaded);
  return downloaded;
}

/**
 * Delete the named cache (call when upgrading model versions).
 */
export async function evictModelCache(): Promise<void> {
  if (typeof caches !== "undefined") {
    await caches.delete(CACHE_NAME);
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

async function tryReadFromCacheStorage(url: string): Promise<ArrayBuffer | null> {
  try {
    const cache = await caches.open(CACHE_NAME);
    const match = await cache.match(url);
    if (match) {
      return match.arrayBuffer();
    }
  } catch {
    // Cache Storage unavailable (Private Mode / permissions policy)
  }
  return null;
}

async function writeToCacheStorage(url: string, buf: ArrayBuffer): Promise<void> {
  try {
    const cache = await caches.open(CACHE_NAME);
    const response = new Response(buf, {
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Length": String(buf.byteLength),
      },
    });
    await cache.put(url, response);
  } catch {
    // Best-effort; caller already has the data in memory.
  }
}

async function fetchWithProgress(url: string, onProgress?: ProgressCallback): Promise<ArrayBuffer> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch model: ${response.status} ${response.statusText} (${url})`);
  }

  const contentLength = Number(response.headers.get("Content-Length") ?? "0");

  if (!onProgress || !response.body) {
    return response.arrayBuffer();
  }

  // Stream with progress tracking
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.byteLength;
    onProgress(loaded, contentLength);
  }

  // Concatenate chunks into a single ArrayBuffer
  const total = chunks.reduce((sum, c) => sum + c.byteLength, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out.buffer;
}

async function verifyHash(buf: ArrayBuffer, expectedHex: string, id: string): Promise<void> {
  if (!globalThis.crypto?.subtle) {
    console.warn(`[modelCache] SubtleCrypto unavailable — skipping hash check for ${id}`);
    return;
  }
  const hashBuf = await globalThis.crypto.subtle.digest("SHA-256", buf);
  const actual = Array.from(new Uint8Array(hashBuf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  if (actual !== expectedHex) {
    throw new Error(
      `SHA-256 mismatch for model "${id}": expected ${expectedHex}, got ${actual}`,
    );
  }
}
