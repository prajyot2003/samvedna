import { useEffect, useRef, useState } from "react";
import { startRecording, type Recorder } from "../lib/wav";

type Status =
  | { kind: "idle" }
  | { kind: "recording"; seconds: number }
  | { kind: "transcribing" }
  | { kind: "note"; text: string }
  | { kind: "error"; text: string };

/**
 * Where the counsellor writes down what the caller said — by typing, or by
 * speaking and then correcting what came back.
 *
 * ALWAYS PRESENT, not only while the agent is asking for a narrative. A caller
 * describing an atrocity does not deliver it in the one turn the dialog policy
 * has budgeted for it; they say the thing that matters while being asked
 * something else entirely. A console where the only way to record that is to
 * wait for the right prompt loses it.
 *
 * DICTATION PRODUCES A DRAFT, NEVER A RECORD. Recognition error is worst on
 * exactly the dialects spoken by the callers this system exists for, so the
 * words land in the box for the counsellor to fix and submit deliberately.
 * Nothing is scored until they press Record. The recording itself is analysed
 * either way — the signal quality gate and the prosody features are measured
 * from it, which is the only route by which a typed console ever produces a
 * Channel C signal at all.
 */
export function Composer({
  onSubmit, onDictate, busy, disabled, language,
}: {
  onSubmit: (text: string) => void;
  onDictate: (blob: Blob) => Promise<{ text: string; recognised: boolean;
                                       asr_configured: boolean }>;
  busy: boolean;
  disabled: boolean;
  language: string;
}) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const recorder = useRef<Recorder | null>(null);
  const timer = useRef<number | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  // Release the microphone if this unmounts mid-recording. A console that can
  // leave a hot mic behind on a victim helpline is not shippable.
  useEffect(() => () => {
    recorder.current?.cancel();
    if (timer.current) window.clearInterval(timer.current);
  }, []);

  const clearTimer = () => {
    if (timer.current) { window.clearInterval(timer.current); timer.current = null; }
  };

  const beginRecording = async () => {
    try {
      recorder.current = await startRecording();
      setStatus({ kind: "recording", seconds: 0 });
      timer.current = window.setInterval(
        () => setStatus((s) => (s.kind === "recording"
          ? { kind: "recording", seconds: s.seconds + 1 } : s)), 1000);
    } catch {
      setStatus({ kind: "error",
                  text: "Microphone unavailable — check the browser's permission prompt." });
    }
  };

  const finishRecording = async () => {
    const active = recorder.current;
    if (!active) return;
    recorder.current = null;
    clearTimer();
    setStatus({ kind: "transcribing" });
    try {
      const blob = await active.stop();
      const result = await onDictate(blob);
      if (result.text) {
        // Appended, never replaced: a second dictation must not silently erase
        // a correction the counsellor already made to the first.
        setText((current) => (current ? `${current} ${result.text}` : result.text));
        setStatus({ kind: "idle" });
        box.current?.focus();
      } else if (!result.asr_configured) {
        setStatus({ kind: "note",
                    text: "No speech recogniser is loaded, so there are no words to insert. "
                        + "The recording was still analysed for signal quality and prosody. "
                        + "Run `make fetch-models` to enable recognition." });
      } else {
        setStatus({ kind: "note",
                    text: "Nothing was recognised in that recording. Type it instead." });
      }
    } catch (exc) {
      setStatus({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    }
  };

  const cancelRecording = () => {
    recorder.current?.cancel();
    recorder.current = null;
    clearTimer();
    setStatus({ kind: "idle" });
  };

  const submit = () => {
    const value = text.trim();
    if (!value) return;
    setText("");
    setStatus({ kind: "idle" });
    onSubmit(value);
  };

  const recording = status.kind === "recording";
  const transcribing = status.kind === "transcribing";
  const locked = busy || disabled || transcribing;

  return (
    <section className="card composer">
      <div className="composer-head">
        <span className="lbl">Record what the caller says</span>
        {recording && (
          <span className="rec" role="status">
            <span className="rec-dot" aria-hidden="true" />
            {String(Math.floor(status.seconds / 60)).padStart(2, "0")}:
            {String(status.seconds % 60).padStart(2, "0")}
          </span>
        )}
      </div>

      <textarea
        ref={box}
        className="deva"
        rows={3}
        value={text}
        disabled={locked}
        lang={language === "bho" ? "bho" : "hi"}
        placeholder={recording
          ? "Listening… speak, then press Stop to insert the words here."
          : "Type what the caller says, or use the microphone."}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
        }}
      />

      <div className="composer-actions">
        {recording ? (
          <>
            <button className="recording" onClick={finishRecording}>
              Stop and transcribe
            </button>
            <button className="ghost" onClick={cancelRecording}>Discard</button>
          </>
        ) : (
          <button disabled={locked} onClick={beginRecording} title="Record from the microphone">
            <span className="mic" aria-hidden="true" />
            {transcribing ? "Transcribing…" : "Dictate"}
          </button>
        )}
        <button className="primary" disabled={locked || recording || !text.trim()}
                onClick={submit}>
          Record
        </button>
        <span className="hint">⌘↵ to submit</span>
      </div>

      {status.kind === "note" && <p className="hint note">{status.text}</p>}
      {status.kind === "error" && <p className="error">{status.text}</p>}
      {!recording && status.kind === "idle" && (
        <p className="hint">
          Dictation fills the box for you to correct. Nothing is assessed until
          you press Record.
        </p>
      )}
    </section>
  );
}
