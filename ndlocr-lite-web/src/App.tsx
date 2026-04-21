import { useCallback, useEffect, useRef, useState } from "react";
import { OcrClient } from "./lib/OcrClient.js";
import { useOcrStore } from "./state/useOcrStore.js";
import { t } from "./lib/i18n.js";
import { Header } from "./components/Header.js";
import { Footer } from "./components/Footer.js";
import { DropZone } from "./components/DropZone.js";
import { LoadProgress } from "./components/LoadProgress.js";
import { ImageViewer } from "./components/ImageViewer.js";
import { ResultTabs } from "./components/ResultTabs.js";
import { DownloadButtons } from "./components/DownloadButtons.js";
import { ErrorBoundary } from "./components/ErrorBoundary.js";

// First-visit info modal
function InitModal({ onClose }: { onClose: () => void }) {
  const lang = useOcrStore((s) => s.lang);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-2xl shadow-xl max-w-md w-full p-6 space-y-4">
        <h2 className="text-lg font-semibold text-slate-800">{t(lang, "initialModalTitle")}</h2>
        <p className="text-sm text-slate-600 leading-relaxed">{t(lang, "initialModalBody")}</p>
        <p className="text-xs text-slate-500">{t(lang, "privacyNote")}</p>
        <button
          onClick={onClose}
          className="w-full py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors"
        >
          {t(lang, "initialModalOk")}
        </button>
      </div>
    </div>
  );
}

// OCR spinner shown while processing
function OcrSpinner() {
  const lang = useOcrStore((s) => s.lang);
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-20 text-slate-500">
      <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      <p className="text-base font-medium">{t(lang, "processingOcr")}</p>
    </div>
  );
}

// Error display with retry
function ErrorDisplay({ onRetry }: { onRetry: () => void }) {
  const lang = useOcrStore((s) => s.lang);
  const error = useOcrStore((s) => s.error);
  return (
    <div className="max-w-lg mx-auto mt-8 p-6 bg-red-50 border border-red-200 rounded-xl space-y-3 text-center">
      <p className="text-red-700 font-semibold">{t(lang, "errorTitle")}</p>
      {error && (
        <pre className="text-xs text-red-600 overflow-auto whitespace-pre-wrap text-left">{error}</pre>
      )}
      <button
        onClick={onRetry}
        className="px-5 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg font-medium text-sm transition-colors"
      >
        {t(lang, "errorRetry")}
      </button>
    </div>
  );
}

// T5-7c: Warn users on devices with < 4 GB RAM (navigator.deviceMemory API).
function LowMemoryWarning({ onDismiss }: { onDismiss: () => void }) {
  const lang = useOcrStore((s) => s.lang);
  return (
    <div className="fixed bottom-4 left-4 right-4 z-40 max-w-lg mx-auto">
      <div className="bg-amber-50 border border-amber-300 rounded-xl shadow-lg p-4 flex gap-3 items-start">
        <span className="text-amber-500 text-lg shrink-0" aria-hidden="true">⚠</span>
        <p className="text-sm text-amber-800 flex-1">{t(lang, "lowMemoryWarning")}</p>
        <button
          onClick={onDismiss}
          className="text-xs px-2 py-1 rounded bg-amber-200 hover:bg-amber-300 text-amber-900 shrink-0 font-medium transition-colors"
        >
          {t(lang, "lowMemoryDismiss")}
        </button>
      </div>
    </div>
  );
}

const FIRST_VISIT_KEY = "ndlocr_lite_web_visited";
const LOW_MEMORY_DISMISSED_KEY = "ndlocr_lite_low_mem_dismissed";

/** Returns true when navigator.deviceMemory < 4 GB (Chrome/Edge only). */
function isLowMemoryDevice(): boolean {
  const dm = (navigator as Navigator & { deviceMemory?: number }).deviceMemory;
  return dm !== undefined && dm < 4;
}

export function App() {
  const phase = useOcrStore((s) => s.phase);
  const lang = useOcrStore((s) => s.lang);
  const clientRef = useRef<OcrClient | null>(null);
  const [showModal, setShowModal] = useState(() => !localStorage.getItem(FIRST_VISIT_KEY));
  const [showLowMemWarning, setShowLowMemWarning] = useState(
    () => isLowMemoryDevice() && !localStorage.getItem(LOW_MEMORY_DISMISSED_KEY),
  );

  const initClient = useCallback(() => {
    const client = new OcrClient();
    clientRef.current = client;
    useOcrStore.getState().setPhase("loading");

    client
      .init((stage, percent) => {
        useOcrStore.getState().updateProgress(stage, percent);
      })
      .then(() => {
        useOcrStore.getState().setPhase("ready");
      })
      .catch((err: unknown) => {
        useOcrStore.getState().setError(String(err));
      });
  }, []);

  useEffect(() => {
    initClient();
    return () => {
      clientRef.current?.terminate();
    };
  }, [initClient]);

  const handleFile = useCallback(async (file: File | Blob, fileName?: string) => {
    const client = clientRef.current;
    if (!client) return;

    const name = fileName ?? (file instanceof File ? file.name : "image.jpg");
    const url = URL.createObjectURL(file);
    const bmp = await createImageBitmap(file);
    useOcrStore.getState().setImage(url, bmp.width, bmp.height, name);
    bmp.close();

    useOcrStore.getState().setPhase("ocr");

    try {
      const result = await client.ocr(file, { imgName: name, viz: true });
      useOcrStore.getState().setResult(result);
      useOcrStore.getState().setPhase("done");
    } catch (err) {
      useOcrStore.getState().setError(String(err));
    }
  }, []);

  // Global paste handler — placed after handleFile to avoid use-before-declare
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      if (phase !== "ready") return;
      const item = Array.from(e.clipboardData?.items ?? []).find((i) =>
        i.type.startsWith("image/"),
      );
      if (!item) return;
      const blob = item.getAsFile();
      if (blob) handleFile(blob, "pasted-image.png");
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  });

  const handleRetry = useCallback(() => {
    clientRef.current?.terminate();
    useOcrStore.setState({ phase: "idle", progress: {}, error: null });
    initClient();
  }, [initClient]);

  const handleDismissModal = () => {
    localStorage.setItem(FIRST_VISIT_KEY, "1");
    setShowModal(false);
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <Header />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-6">
        <ErrorBoundary>
          {phase === "loading" && (
            <div className="flex justify-center mt-8">
              <LoadProgress />
            </div>
          )}

          {phase === "ready" && (
            <div className="max-w-2xl mx-auto">
              <DropZone onFile={handleFile} />
            </div>
          )}

          {phase === "ocr" && <OcrSpinner />}

          {phase === "done" && (
            <div className="flex flex-col gap-4">
              {/* Action bar */}
              <div className="flex justify-end">
                <button
                  onClick={() => useOcrStore.getState().reset()}
                  className="px-4 py-2 text-sm rounded-lg border border-slate-300 bg-white hover:bg-slate-50 text-slate-700 font-medium transition-colors"
                >
                  {t(lang, "newImage")}
                </button>
              </div>
              {/* Split view */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4" style={{ minHeight: "70vh" }}>
                <ImageViewer />
                <div className="flex flex-col gap-3 h-full">
                  <div className="flex-1 min-h-0">
                    <ResultTabs />
                  </div>
                  <DownloadButtons />
                </div>
              </div>
            </div>
          )}

          {phase === "error" && <ErrorDisplay onRetry={handleRetry} />}
        </ErrorBoundary>
      </main>

      <Footer />

      {showModal && <InitModal onClose={handleDismissModal} />}

      {showLowMemWarning && (
        <LowMemoryWarning
          onDismiss={() => {
            localStorage.setItem(LOW_MEMORY_DISMISSED_KEY, "1");
            setShowLowMemWarning(false);
          }}
        />
      )}
    </div>
  );
}
