import { useOcrStore } from "../state/useOcrStore.js";
import { t } from "../lib/i18n.js";

export function Footer() {
  const lang = useOcrStore((s) => s.lang);

  return (
    <footer className="bg-slate-800 text-slate-400 text-xs mt-auto">
      <div className="max-w-7xl mx-auto px-4 py-4 space-y-1">
        <p>
          <span className="font-medium text-slate-300">{t(lang, "privacyNote")}</span>
        </p>
        <p>
          {t(lang, "footerDeps")}
        </p>
        <p className="flex gap-4">
          <a
            href="https://github.com/ndl-lab/ndlocr-lite"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-white underline"
          >
            {t(lang, "footerRepo")}
          </a>
          <span>
            {t(lang, "footerLicense")}
            {" — "}
            <a
              href="https://creativecommons.org/licenses/by/4.0/"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white underline"
            >
              CC BY 4.0
            </a>
            {" · National Diet Library of Japan"}
          </span>
        </p>
      </div>
    </footer>
  );
}
