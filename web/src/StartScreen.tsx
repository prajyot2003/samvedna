/**
 * Where a call begins.
 *
 * The language choice is first and unavoidable because it determines the
 * consent script, the crisis lexicon and the recogniser — getting it wrong
 * silently degrades every one of them, and Bhojpuri degrades furthest.
 */
export function StartScreen({
  onStart, busy, error,
}: {
  onStart: (language: string) => void;
  busy: boolean;
  error: string | null;
}) {
  return (
    <div className="start-screen">
      <div className="card start-card">
        <span className="lbl">New interaction</span>
        <h1>Which language is the caller speaking?</h1>
        <p className="muted">
          This sets the consent script, the crisis vocabulary and the
          recogniser. Consent is requested before anything is assessed.
        </p>

        {error && <p className="error">{error}</p>}

        <div className="language-choice">
          <button className="primary" disabled={busy}
                  onClick={() => onStart("hi")}>
            <b>Hindi</b>
            <span>हिन्दी</span>
          </button>
          <button disabled={busy} onClick={() => onStart("bho")}>
            <b>Bhojpuri</b>
            <span>भोजपुरी</span>
          </button>
        </div>

        <p className="start-note">
          Bhojpuri is supported deliberately as the harder case. Recognition is
          weaker for it, and where the system is less sure it raises the
          assessment rather than lowering it.
        </p>
      </div>
    </div>
  );
}
