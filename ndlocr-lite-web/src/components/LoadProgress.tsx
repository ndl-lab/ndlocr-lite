import { useOcrStore } from "../state/useOcrStore.js";
import { t } from "../lib/i18n.js";

const STAGES = ["pyodide", "packages", "wheel", "models", "init"] as const;
type Stage = (typeof STAGES)[number];

function stageLabelKey(stage: Stage) {
  return `stageLabel_${stage}` as const;
}

export function LoadProgress() {
  const lang = useOcrStore((s) => s.lang);
  const progress = useOcrStore((s) => s.progress);

  const activeIndex = STAGES.reduce<number>((last, stage, i) => {
    const p = progress[stage] ?? -1;
    return p >= 0 ? i : last;
  }, -1);

  return (
    <div className="max-w-lg mx-auto bg-white rounded-xl shadow p-8 space-y-5">
      <h2 className="text-lg font-semibold text-slate-700 text-center">{t(lang, "appTitle")}</h2>
      <div className="space-y-3">
        {STAGES.map((stage, i) => {
          const percent = progress[stage] ?? 0;
          const done = percent >= 100;
          const active = i === activeIndex && !done;
          const waiting = i > activeIndex;

          return (
            <div key={stage} className={waiting ? "opacity-40" : ""}>
              <div className="flex justify-between text-sm mb-1">
                <span className={done ? "text-green-600 font-medium" : "text-slate-600"}>
                  {done && (
                    <span className="mr-1" aria-hidden>
                      ✓
                    </span>
                  )}
                  {t(lang, stageLabelKey(stage))}
                </span>
                {!waiting && <span className="text-slate-400">{percent}%</span>}
              </div>
              <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden">
                <div
                  className={[
                    "h-full rounded-full transition-all duration-300",
                    done ? "bg-green-500" : active ? "bg-blue-500" : "bg-slate-200",
                  ].join(" ")}
                  style={{ width: `${percent}%` }}
                  role="progressbar"
                  aria-valuenow={percent}
                  aria-valuemin={0}
                  aria-valuemax={100}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
