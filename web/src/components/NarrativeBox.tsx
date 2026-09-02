import { useRef, useState } from "react";

const SAMPLES: { label: string; language: string; text: string }[] = [
  {
    label: "Social boycott",
    language: "hi",
    text: "गाँव वालों ने बहिष्कार कर दिया, हुक्का पानी बंद है, पुलिस ने रिपोर्ट लिखने से मना कर दिया",
  },
  {
    label: "Homicide",
    language: "hi",
    text: "मेरे पति की हत्या कर दी, अब कमाने वाला कोई नहीं, आरोपी अभी भी गाँव में ही है",
  },
  {
    label: "Crisis disclosure",
    language: "bho",
    text: "केहू बात ना करे, बहिष्कार बा। अब ना सहल जाला, जिये के मन नइखे",
  },
];

/**
 * Recording what the caller actually said — available at every point in the
 * call, not only at the opening question.
 *
 * This was the console's worst defect. A call is continuous speech: people
 * disclose the thing that matters twenty minutes in, halfway through an
 * unrelated question. The old layout offered a text box during the opening
 * prompt and then took it away, so anything said afterwards could not be
 * recorded at all — no extraction, no crisis detection, no trace in the case
 * record.
 *
 * The sample phrases are labelled as samples and typed into the same field a
 * counsellor uses. They exist so a demonstration does not depend on the person
 * driving it being able to type Devanagari.
 */
export function NarrativeBox({
  onSubmit, busy, language,
}: {
  onSubmit: (text: string) => void;
  busy: boolean;
  language: string;
}) {
  const [text, setText] = useState("");
  const [showSamples, setShowSamples] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const value = text.trim();
    if (!value || busy) return;
    setText("");
    onSubmit(value);
  };

  return (
    <section className="card narrative-box">
      <div className="narrative-head">
        <span className="lbl">What the caller said</span>
        <button type="button" className="link-button"
                aria-expanded={showSamples}
                onClick={() => setShowSamples((v) => !v)}>
          {showSamples ? "Hide samples" : "Sample phrases"}
        </button>
      </div>

      {showSamples && (
        <div className="samples">
          <p className="muted">
            Sample text for demonstration. It is typed into the same box a
            counsellor would use — nothing about it is special-cased.
          </p>
          {SAMPLES.map((sample) => (
            <button key={sample.label} type="button" disabled={busy}
                    className={sample.language === language ? "" : "other-lang"}
                    onClick={() => { setText(sample.text); box.current?.focus(); }}>
              {sample.label}
              {sample.language !== language && (
                <span className="lang-tag">{sample.language}</span>
              )}
            </button>
          ))}
        </div>
      )}

      <textarea
        ref={box}
        className="deva"
        rows={3}
        value={text}
        placeholder="Type what the caller says, at any point in the call…"
        disabled={busy}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            submit();
          }
        }}
      />

      <div className="narrative-actions">
        <button className="primary" disabled={busy || !text.trim()}
                onClick={submit}>Record</button>
        <span className="hint">
          <kbd>Ctrl</kbd> + <kbd>Enter</kbd> to record. Redaction runs before
          anything is stored.
        </span>
      </div>
    </section>
  );
}
