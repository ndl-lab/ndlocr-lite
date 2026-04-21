/**
 * Thin Comlink re-export with typed helpers.
 *
 * Phase 3 uses Comlink directly for all RPC between the main thread and the
 * Pyodide Worker.  This module is a convenience re-export so that Phase 4+
 * code can import from a single place, and a low-level SAB/Atomics fallback
 * can be added here in Phase 5 without touching call sites.
 */

export {
  wrap,
  expose,
  proxy,
  transfer,
  releaseProxy,
} from "comlink";

export type { Remote, ProxyMarked } from "comlink";
