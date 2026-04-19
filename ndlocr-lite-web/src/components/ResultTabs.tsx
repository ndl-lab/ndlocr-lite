import { useState, useRef } from "react";
import { useOcrStore } from "../state/useOcrStore.js";
import { t } from "../lib/i18n.js";

type Tab = "text" | "xml" | "json";

interface LineItem {
  boundingBox: [[number, number], [number, number], [number, number], [number, number]];
  id: number;
  isVertical: string;
  text: string;
  confidence: number;
}

function isVerticalPage(json: unknown): boolean {
  const j = json as { contents?: Array<LineItem[]> };
  const lines = j.contents?.[0] ?? [];
  const tateCnt = lines.filter((l) => {
    const bb = l.boundingBox;
    const w = bb[2][0] - bb[0][0];
    const h = bb[1][1] - bb[0][1];
    return h > w;
  }).length;
  return lines.length > 0 && tateCnt / lines.length > 0.5;
}

function useCopy(text: string, lang: "en" | "ja") {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return { copy, label: copied ? t(lang, "copied") : t(lang, "copyText") };
}

export function ResultTabs() {
  const lang = useOcrStore((s) => s.lang);
  const result = useOcrStore((s) => s.result);
  const highlightedLineId = useOcrStore((s) => s.highlightedLineId);
  const setHighlightedLineId = useOcrStore((s) => s.setHighlightedLineId);
  const [activeTab, setActiveTab] = useState<Tab>("text");
  const lineRefs = useRef<Map<number, HTMLElement>>(new Map());

  if (!result) return null;

  const { text, xml, json } = result;
  const isVert = isVerticalPage(json);

  const tabs: { id: Tab; label: string }[] = [
    { id: "text", label: t(lang, "tabText") },
    { id: "xml", label: t(lang, "tabXml") },
    { id: "json", label: t(lang, "tabJson") },
  ];

  const textCopy = useCopy(text, lang);
  const xmlCopy = useCopy(xml, lang);
  const jsonCopy = useCopy(JSON.stringify(json, null, 2), lang);

  const activeCopy = activeTab === "text" ? textCopy : activeTab === "xml" ? xmlCopy : jsonCopy;

  // Parse text lines for interactive line list
  const textLines = text.split("\n");

  // Map line text → id from json for cross-highlighting
  const j = json as { contents?: Array<LineItem[]> };
  const lineItems = j.contents?.[0] ?? [];

  return (
    <div className="bg-white rounded-xl shadow flex flex-col h-full overflow-hidden">
      {/* Tab bar */}
      <div className="flex border-b border-slate-200 shrink-0">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={[
              "px-4 py-2 text-sm font-medium transition-colors",
              activeTab === tab.id
                ? "border-b-2 border-blue-500 text-blue-600"
                : "text-slate-500 hover:text-slate-700",
            ].join(" ")}
          >
            {tab.label}
          </button>
        ))}
        <div className="ml-auto flex items-center pr-2">
          <button
            onClick={activeCopy.copy}
            className="text-xs px-2 py-1 rounded text-slate-500 hover:bg-slate-100 transition-colors"
          >
            {activeCopy.label}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-3 text-sm">
        {activeTab === "text" && (
          <div
            className={[
              "min-h-full",
              isVert ? "writing-mode-vertical" : "",
            ].join(" ")}
            style={isVert ? { writingMode: "vertical-rl" } : {}}
          >
            {lineItems.length > 0 ? (
              lineItems.map((item) => {
                const isHL = highlightedLineId === item.id;
                return (
                  <div
                    key={item.id}
                    ref={(el) => {
                      if (el) lineRefs.current.set(item.id, el);
                      else lineRefs.current.delete(item.id);
                    }}
                    className={[
                      "cursor-pointer rounded px-1 transition-colors",
                      isHL ? "bg-blue-100 text-blue-900" : "hover:bg-slate-50",
                    ].join(" ")}
                    onMouseEnter={() => setHighlightedLineId(item.id)}
                    onMouseLeave={() => setHighlightedLineId(null)}
                  >
                    {item.text || "\u00A0"}
                  </div>
                );
              })
            ) : (
              <pre className="whitespace-pre-wrap font-mono text-xs text-slate-700">{text}</pre>
            )}
          </div>
        )}

        {activeTab === "xml" && (
          <pre className="whitespace-pre-wrap font-mono text-xs text-slate-700 break-all">
            {xml}
          </pre>
        )}

        {activeTab === "json" && (
          <pre className="whitespace-pre-wrap font-mono text-xs text-slate-700 break-all">
            {JSON.stringify(json, null, 2)}
          </pre>
        )}
      </div>

      {/* Line count info */}
      {activeTab === "text" && (
        <div className="px-3 py-1.5 border-t border-slate-100 text-xs text-slate-400 shrink-0">
          {lineItems.length > 0
            ? `${lineItems.length} lines`
            : `${textLines.length} lines`}
          {isVert && " · vertical"}
        </div>
      )}
    </div>
  );
}
