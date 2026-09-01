import { useEffect, useRef } from "react";
import type { TranscriptEntry } from "../types";

/**
 * What was said, already redacted server-side.
 *
 * Redaction counts are shown rather than hidden: a counsellor reading
 * "गाँव [VILLAGE] में" should understand that a place name was removed on
 * purpose, not that the recogniser failed.
 */
export function Transcript({ entries }: { entries: TranscriptEntry[] }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [entries.length]);

  return (
    <section className="card transcript">
      <span className="lbl">Transcript</span>
      {entries.length === 0 && <p className="muted">Nothing said yet.</p>}
      <ol>
        {entries.map((entry, i) => (
          <li key={i} className={entry.speaker}>
            <span className="who lbl">{entry.speaker}</span>
            <p className="deva">{entry.text}</p>
            {Object.keys(entry.redactions).length > 0 && (
              <span className="redacted">
                removed before storage:{" "}
                {Object.entries(entry.redactions)
                  .map(([k, n]) => `${n} ${k.toLowerCase()}`).join(", ")}
              </span>
            )}
          </li>
        ))}
      </ol>
      <div ref={end} />
    </section>
  );
}
