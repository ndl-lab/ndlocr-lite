import { useOcrStore } from "../state/useOcrStore.js";
import { t } from "../lib/i18n.js";

interface LicenseEntry {
  name: string;
  version?: string;
  license: string;
  url: string;
}

const LICENSES: LicenseEntry[] = [
  {
    name: "NDLOCR-Lite",
    license: "CC BY 4.0",
    url: "https://creativecommons.org/licenses/by/4.0/",
  },
  {
    name: "Pyodide",
    version: "0.27.x",
    license: "MPL 2.0",
    url: "https://github.com/pyodide/pyodide/blob/main/LICENSE",
  },
  {
    name: "onnxruntime-web",
    version: "1.22.x",
    license: "MIT",
    url: "https://github.com/microsoft/onnxruntime/blob/main/LICENSE",
  },
  {
    name: "React",
    version: "19.x",
    license: "MIT",
    url: "https://github.com/facebook/react/blob/main/LICENSE",
  },
  {
    name: "Vite",
    version: "6.x",
    license: "MIT",
    url: "https://github.com/vitejs/vite/blob/main/LICENSE",
  },
  {
    name: "Tailwind CSS",
    version: "4.x",
    license: "MIT",
    url: "https://github.com/tailwindlabs/tailwindcss/blob/main/LICENSE",
  },
  {
    name: "Comlink",
    version: "4.x",
    license: "Apache 2.0",
    url: "https://github.com/GoogleChromeLabs/comlink/blob/main/LICENSE",
  },
  {
    name: "Zustand",
    version: "5.x",
    license: "MIT",
    url: "https://github.com/pmndrs/zustand/blob/main/LICENSE",
  },
  {
    name: "JSZip",
    version: "3.x",
    license: "MIT",
    url: "https://github.com/Stuk/jszip/blob/main/LICENSE.markdown",
  },
  {
    name: "idb-keyval",
    version: "6.x",
    license: "Apache 2.0",
    url: "https://github.com/jakearchibald/idb-keyval/blob/main/licence",
  },
  {
    name: "vite-plugin-pwa / Workbox",
    license: "MIT",
    url: "https://github.com/vite-pwa/vite-plugin-pwa/blob/main/LICENSE",
  },
  {
    name: "numpy",
    license: "BSD 3-Clause",
    url: "https://github.com/numpy/numpy/blob/main/LICENSE.txt",
  },
  {
    name: "Pillow",
    license: "HPND",
    url: "https://github.com/python-pillow/Pillow/blob/main/LICENSE",
  },
  {
    name: "lxml",
    license: "BSD 3-Clause",
    url: "https://github.com/lxml/lxml/blob/master/LICENSES.txt",
  },
  {
    name: "networkx",
    license: "BSD 3-Clause",
    url: "https://github.com/networkx/networkx/blob/main/LICENSE.txt",
  },
  {
    name: "PyYAML",
    license: "MIT",
    url: "https://github.com/yaml/pyyaml/blob/master/LICENSE",
  },
];

export function LicenseModal({ onClose }: { onClose: () => void }) {
  const lang = useOcrStore((s) => s.lang);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-2xl shadow-xl max-w-lg w-full max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between p-5 border-b border-slate-200">
          <h2 className="text-base font-semibold text-slate-800">
            {t(lang, "licensesTitle")}
          </h2>
          <button
            onClick={onClose}
            aria-label={t(lang, "licensesClose")}
            className="text-slate-400 hover:text-slate-600 text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="overflow-y-auto p-5 space-y-3 text-sm">
          {LICENSES.map((entry) => (
            <div key={entry.name} className="flex items-baseline justify-between gap-2">
              <span className="text-slate-700 font-medium">
                {entry.name}
                {entry.version && (
                  <span className="text-slate-400 font-normal ml-1">{entry.version}</span>
                )}
              </span>
              <a
                href={entry.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline shrink-0"
              >
                {entry.license}
              </a>
            </div>
          ))}
        </div>

        <div className="p-4 border-t border-slate-200 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-medium transition-colors"
          >
            {t(lang, "licensesClose")}
          </button>
        </div>
      </div>
    </div>
  );
}
