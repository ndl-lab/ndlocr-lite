import { useOcrStore } from "../state/useOcrStore.js";
import { t } from "../lib/i18n.js";

export function Header() {
  const lang = useOcrStore((s) => s.lang);
  const setLang = useOcrStore((s) => s.setLang);

  return (
    <header className="bg-slate-800 text-white shadow-md">
      <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t(lang, "appTitle")}</h1>
          <p className="text-slate-400 text-xs mt-0.5">{t(lang, "appSubtitle")}</p>
        </div>
        <button
          onClick={() => setLang(lang === "en" ? "ja" : "en")}
          className="text-sm px-3 py-1 rounded border border-slate-500 hover:bg-slate-700 transition-colors"
          aria-label="Toggle language"
        >
          {t(lang, "langToggle")}
        </button>
      </div>
    </header>
  );
}
