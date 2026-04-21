import { useCallback, useRef, useState } from "react";
import { useOcrStore } from "../state/useOcrStore.js";
import { t } from "../lib/i18n.js";

interface Props {
  onFile: (file: File | Blob, fileName?: string) => void;
}

export function DropZone({ onFile }: Props) {
  const lang = useOcrStore((s) => s.lang);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const handleFiles = useCallback(
    (files: FileList | null) => {
      const file = files?.[0];
      if (!file) return;
      onFile(file, file.name);
    },
    [onFile],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles],
  );

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  };

  const onDragLeave = () => setDragging(false);

  const onInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    handleFiles(e.target.files);
    e.target.value = "";
  };

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={t(lang, "dropHint")}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      className={[
        "border-2 border-dashed rounded-xl p-16 text-center cursor-pointer select-none transition-colors",
        dragging
          ? "border-blue-500 bg-blue-50"
          : "border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50",
      ].join(" ")}
    >
      <div className="flex flex-col items-center gap-3 text-slate-500 pointer-events-none">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="w-14 h-14 text-slate-300"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909M3 20.25h18M3.75 3h16.5M3.75 3v13.5M20.25 3v13.5"
          />
        </svg>
        <p className="text-base font-medium">{t(lang, "dropHint")}</p>
        <p className="text-sm">{t(lang, "supportedFormats")}</p>
        <p className="text-xs text-slate-400">{t(lang, "pasteShortcut")}</p>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/bmp,image/tiff,image/webp"
        className="hidden"
        onChange={onInputChange}
      />
    </div>
  );
}
