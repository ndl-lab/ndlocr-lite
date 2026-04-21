import JSZip from "jszip";
import { useOcrStore } from "../state/useOcrStore.js";
import { t } from "../lib/i18n.js";
import { downloadBlob, baseName } from "../lib/fileUtils.js";

export function DownloadButtons() {
  const lang = useOcrStore((s) => s.lang);
  const result = useOcrStore((s) => s.result);
  const currentImage = useOcrStore((s) => s.currentImage);

  if (!result || !currentImage) return null;

  const { xml, text, json, vizPng } = result;
  const base = baseName(currentImage.fileName);
  const jsonStr = JSON.stringify(json, null, 2);

  const downloadTxt = () => downloadBlob(text, `${base}.txt`, "text/plain;charset=utf-8");
  const downloadXml = () => downloadBlob(xml, `${base}.xml`, "application/xml;charset=utf-8");
  const downloadJson = () => downloadBlob(jsonStr, `${base}.json`, "application/json;charset=utf-8");
  const downloadViz = () => {
    if (!vizPng) return;
    downloadBlob(vizPng as Uint8Array<ArrayBuffer>, `${base}_viz.png`, "image/png");
  };

  const downloadAll = async () => {
    const zip = new JSZip();
    zip.file(`${base}.txt`, text);
    zip.file(`${base}.xml`, xml);
    zip.file(`${base}.json`, jsonStr);
    if (vizPng) zip.file(`${base}_viz.png`, vizPng as Uint8Array<ArrayBuffer>);
    const blob = await zip.generateAsync({ type: "blob" });
    downloadBlob(blob, `${base}.zip`, "application/zip");
  };

  const btnClass =
    "px-3 py-1.5 text-sm rounded border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 transition-colors font-medium";

  return (
    <div className="bg-white rounded-xl shadow p-3 shrink-0">
      <div className="flex flex-wrap gap-2">
        <button className={btnClass} onClick={downloadTxt}>
          {t(lang, "downloadTxt")}
        </button>
        <button className={btnClass} onClick={downloadXml}>
          {t(lang, "downloadXml")}
        </button>
        <button className={btnClass} onClick={downloadJson}>
          {t(lang, "downloadJson")}
        </button>
        {vizPng && (
          <button className={btnClass} onClick={downloadViz}>
            {t(lang, "downloadViz")}
          </button>
        )}
        <button
          className="px-3 py-1.5 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white transition-colors font-medium"
          onClick={downloadAll}
        >
          {t(lang, "downloadZip")}
        </button>
      </div>
    </div>
  );
}
