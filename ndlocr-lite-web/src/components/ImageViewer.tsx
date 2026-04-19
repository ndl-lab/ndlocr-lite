import { useRef, useState } from "react";
import { useOcrStore } from "../state/useOcrStore.js";

// Colors from deim.py colorlist (RGB → CSS)
const CLASS_COLORS: readonly string[] = [
  "rgb(0,0,0)",        // 0: text_block
  "rgb(255,0,0)",      // 1: line_main
  "rgb(0,0,142)",      // 2: line_caption
  "rgb(0,0,230)",      // 3: line_ad
  "rgb(106,0,228)",    // 4: line_note
  "rgb(0,60,100)",     // 5: line_note_tochu
  "rgb(0,80,100)",     // 6: block_fig
  "rgb(0,0,70)",       // 7: block_ad
  "rgb(0,0,192)",      // 8: block_pillar
  "rgb(250,170,30)",   // 9: block_folio
  "rgb(100,170,30)",   // 10: block_rubi
  "rgb(220,220,0)",    // 11: block_chart
  "rgb(175,116,175)",  // 12: block_eqn
  "rgb(250,0,30)",     // 13: block_cfm
  "rgb(165,42,42)",    // 14: block_eng
  "rgb(255,77,255)",   // 15: block_table
  "rgb(255,0,0)",      // 16: line_title
];

interface LineBox {
  id: number;
  x: number;
  y: number;
  w: number;
  h: number;
  text: string;
}

function extractLines(json: unknown): { lines: LineBox[]; imgW: number; imgH: number } {
  const j = json as {
    contents?: Array<
      Array<{
        boundingBox: [[number, number], [number, number], [number, number], [number, number]];
        id: number;
        text: string;
        confidence: number;
      }>
    >;
    imginfo?: { img_width: number; img_height: number };
  };

  const raw = j.contents?.[0] ?? [];
  const imgW = j.imginfo?.img_width ?? 0;
  const imgH = j.imginfo?.img_height ?? 0;

  const lines: LineBox[] = raw.map((item) => {
    const bb = item.boundingBox;
    const x = bb[0][0];
    const y = bb[0][1];
    const w = bb[2][0] - bb[0][0];
    const h = bb[1][1] - bb[0][1];
    return { id: item.id, x, y, w, h, text: item.text };
  });

  return { lines, imgW, imgH };
}

export function ImageViewer() {
  const result = useOcrStore((s) => s.result);
  const currentImage = useOcrStore((s) => s.currentImage);
  const highlightedLineId = useOcrStore((s) => s.highlightedLineId);
  const setHighlightedLineId = useOcrStore((s) => s.setHighlightedLineId);

  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  if (!currentImage || !result) return null;

  const { objectUrl, width: imgW, height: imgH } = currentImage;
  const { lines } = extractLines(result.json);

  const handleMouseMove = (e: React.MouseEvent, text: string) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setTooltip({ x: e.clientX - rect.left + 8, y: e.clientY - rect.top + 8, text });
  };

  return (
    <div className="bg-white rounded-xl shadow overflow-hidden h-full flex flex-col">
      <div className="px-4 py-2 border-b border-slate-100 text-sm text-slate-500 font-medium shrink-0">
        {currentImage.fileName}
      </div>
      <div
        ref={containerRef}
        className="relative overflow-auto flex-1 flex items-start justify-center bg-slate-100 p-2"
      >
        <div className="relative inline-block">
          <img
            src={objectUrl}
            alt="Input image"
            className="block max-w-full"
            draggable={false}
          />
          {/* SVG overlay — viewBox matches original pixel dimensions */}
          <svg
            viewBox={`0 0 ${imgW} ${imgH}`}
            xmlns="http://www.w3.org/2000/svg"
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              pointerEvents: "none",
            }}
            preserveAspectRatio="none"
          >
            {lines.map((line, i) => {
              const color = CLASS_COLORS[i % CLASS_COLORS.length] ?? "rgb(0,0,255)";
              const isHL = highlightedLineId === line.id;
              return (
                <rect
                  key={line.id}
                  x={line.x}
                  y={line.y}
                  width={line.w}
                  height={line.h}
                  fill={isHL ? "rgba(59,130,246,0.25)" : "none"}
                  stroke={isHL ? "rgb(59,130,246)" : color}
                  strokeWidth={isHL ? 3 : 1.5}
                  style={{ pointerEvents: "all", cursor: "crosshair" }}
                  onMouseEnter={(e) => {
                    setHighlightedLineId(line.id);
                    handleMouseMove(e as unknown as React.MouseEvent, line.text);
                  }}
                  onMouseMove={(e) => handleMouseMove(e as unknown as React.MouseEvent, line.text)}
                  onMouseLeave={() => {
                    setHighlightedLineId(null);
                    setTooltip(null);
                  }}
                />
              );
            })}
          </svg>
          {/* Tooltip */}
          {tooltip && (
            <div
              className="absolute z-10 max-w-xs px-2 py-1 rounded bg-slate-800 text-white text-xs shadow pointer-events-none whitespace-pre-wrap break-all"
              style={{ left: tooltip.x, top: tooltip.y }}
            >
              {tooltip.text}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
