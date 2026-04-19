/**
 * OcrClient — high-level API for the Pyodide OCR worker.
 *
 * Usage:
 *   const client = new OcrClient();
 *   await client.init((stage, percent) => console.log(stage, percent));
 *   const result = await client.ocr(file, { viz: true });
 *   console.log(result.xml, result.text);
 *   client.terminate();
 */

import * as Comlink from "comlink";
import type { OcrResult, ProgressFn } from "../workers/pyodide.worker.js";
import type { ModelManifest } from "../types/ortTypes.js";

// Vite ?worker import — bundled as a separate ES-module chunk.
import PyodideWorker from "../workers/pyodide.worker?worker";

export type { OcrResult, ProgressFn };

export interface OcrOptions {
  /** Filename embedded in output XML. Defaults to the File.name or 'image.jpg'. */
  imgName?: string;
  /** Include a PNG visualization in the result. Default: false. */
  viz?: boolean;
}

// ---------------------------------------------------------------------------
// OcrClient
// ---------------------------------------------------------------------------

export class OcrClient {
  private readonly worker: Worker;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private readonly api: any;  // Comlink.Remote<typeof workerApi>
  private ready = false;

  constructor() {
    this.worker = new PyodideWorker();
    this.api = Comlink.wrap(this.worker);
  }

  /**
   * Initialize the worker: load Pyodide, packages, ndlocr_web wheel, and ORT
   * sessions.  Progress is reported via the onProgress callback (stage, 0-100).
   *
   * Stages: "pyodide" | "packages" | "wheel" | "models" | "init"
   */
  async init(onProgress?: ProgressFn): Promise<void> {
    const manifest = await this.fetchManifest();
    const progressProxy = onProgress
      ? Comlink.proxy(onProgress)
      : Comlink.proxy((_stage: string, _percent: number) => {});

    await this.api.init(progressProxy, manifest);
    this.ready = true;
  }

  /**
   * Run OCR on a File or Blob.  Returns XML, plain-text, JSON, and optionally
   * a PNG visualization.
   */
  async ocr(source: File | Blob, opts: OcrOptions = {}): Promise<OcrResult> {
    if (!this.ready) {
      throw new Error("[OcrClient] Call init() before ocr().");
    }

    const imgName = opts.imgName ?? (source instanceof File ? source.name : "image.jpg");
    const viz = opts.viz ?? false;

    const bitmap = await createImageBitmap(source);
    // Transfer the ImageBitmap into the worker (zero-copy move).
    const result = await this.api.ocr(Comlink.transfer(bitmap, [bitmap]), imgName, viz);
    return result as OcrResult;
  }

  /** Terminate the underlying worker. The client is unusable after this call. */
  terminate(): void {
    this.api[Comlink.releaseProxy]?.();
    this.worker.terminate();
    this.ready = false;
  }

  // ---------------------------------------------------------------------------
  // Private helpers
  // ---------------------------------------------------------------------------

  private async fetchManifest(): Promise<ModelManifest> {
    const url = `${import.meta.env.BASE_URL}manifest.json`;
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`[OcrClient] Failed to fetch manifest: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<ModelManifest>;
  }
}
