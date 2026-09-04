import { useEffect, useState } from "react";
import { api } from "../api";
import type { ASRSupport, LanguageInfo } from "../types";

const SUPPORT_LABEL: Record<ASRSupport, string> = {
  native: "Speech recognised",
  substituted: "Recognised as a related language",
  declared: "Recognition untested here",
  none: "No speech recognition",
};

const SUPPORT_DETAIL: Record<ASRSupport, string> = {
  native: "A recogniser transcribes this language directly.",
  substituted: "No model exists for this language, so it is decoded as the "
    + "closest one. Word errors are higher and every transcript says so.",
  declared: "A backend documents this language but nobody here has run it. "
    + "Treat a transcript as unverified until the pilot confirms the pipeline.",
  none: "Nothing transcribes this language. Type what the caller says — the "
    + "signal quality and prosody measurements still run.",
};

/**
 * Which language the caller is speaking, and what the system can honestly do
 * about it.
 *
 * THE SUPPORT LEVEL IS SHOWN BEFORE THE CALL, not discovered during it. A
 * counsellor who asks an Odia speaker to talk and watches nothing appear will
 * assume the microphone is broken and lose time on a call where time is the
 * thing that matters. Telling them first costs one line of screen.
 *
 * The languages are ordered worst-supported first. That is deliberate and it
 * is uncomfortable on purpose: the callers this helpline exists for are the
 * ones our recognisers serve least well, and a picker that buried Santali at
 * the bottom of an alphabetical list would make that easy to stop noticing.
 */
export function LanguagePicker({
  busy, onStart,
}: {
  busy: boolean;
  onStart: (code: string) => void;
}) {
  const [languages, setLanguages] = useState<LanguageInfo[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<string>("hi");

  useEffect(() => {
    api.languages()
      .then((body) => setLanguages(
        [...body.languages].sort((a, b) => rank(a) - rank(b))))
      .catch(() => setFailed(true));
  }, []);

  if (failed) {
    return (
      <p className="error">
        Could not reach the service. Is the backend running on port 8000?
      </p>
    );
  }
  if (!languages) return <p className="muted">Loading languages…</p>;

  const current = languages.find((l) => l.code === selected) ?? languages[0];

  return (
    <div className="language-picker">
      <label className="lbl" htmlFor="language">Caller's language</label>
      <select id="language" value={selected} disabled={busy}
              onChange={(e) => setSelected(e.target.value)}>
        {languages.map((language) => (
          <option key={language.code} value={language.code}>
            {language.endonym} · {language.english_name}
            {language.asr_support === "none" ? " — no speech recognition" : ""}
          </option>
        ))}
      </select>

      <div className={`support support-${current.asr_support}`}>
        <b>{SUPPORT_LABEL[current.asr_support]}</b>
        <p>{SUPPORT_DETAIL[current.asr_support]}</p>
        {!current.prompts_translated && (
          <p className="warn">
            Prompts and screener items are not translated into{" "}
            {current.english_name} yet and will appear in Hindi. Read them in
            the caller's language yourself; do not read the Hindi aloud.
          </p>
        )}
        {current.warning && <p className="warn">{current.warning}</p>}
        <p className="muted">
          {current.script} · {current.states.slice(0, 4).join(", ")}
        </p>
      </div>

      <button className="primary" disabled={busy}
              onClick={() => onStart(current.code)}>
        Start interaction in {current.english_name}
      </button>
    </div>
  );
}

/** Worst support first. See the component docstring. */
function rank(language: LanguageInfo): number {
  const order: Record<ASRSupport, number> = {
    none: 0, declared: 1, substituted: 2, native: 3,
  };
  return order[language.asr_support] * 100
    + (language.lexicon_authored ? 1 : 0);
}
