import { useRef, useState } from "react";

/**
 * Audio into the pipeline — from the microphone or a file.
 *
 * The API has accepted audio since the pipeline was built and the console had
 * no way to send it, which meant the quality gate, the prosody features and the
 * whole abstention path were unreachable from the interface.
 *
 * Recording is explicit and visibly stateful. A console that could capture
 * audio without an obvious indicator, on a system handling victim disclosures,
 * would be indefensible whatever the intent.
 */
export function AudioInput({
  onAudio, busy,
}: {
  onAudio: (blob: Blob, filename: string) => void;
  busy: boolean;
}) {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<number | null>(null);
  const file = useRef<HTMLInputElement>(null);

  const stop = () => {
    recorder.current?.stop();
    recorder.current?.stream.getTracks().forEach((t) => t.stop());
    recorder.current = null;
    if (timer.current) { window.clearInterval(timer.current); timer.current = null; }
    setRecording(false);
    setSeconds(0);
  };

  const start = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunks.current = [];
      mr.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data); };
      mr.onstop = () => {
        const blob = new Blob(chunks.current, { type: mr.mimeType || "audio/webm" });
        if (blob.size > 0) onAudio(blob, "capture.webm");
      };
      mr.start();
      recorder.current = mr;
      setRecording(true);
      timer.current = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    } catch {
      setError("Microphone unavailable. Check the browser's permission prompt.");
    }
  };

  return (
    <section className="card audio-input">
      <span className="lbl">Audio</span>
      <div className="audio-actions">
        {recording ? (
          <button className="recording" onClick={stop}>
            <span className="rec-dot" aria-hidden="true" />
            Stop · {String(Math.floor(seconds / 60)).padStart(2, "0")}:
            {String(seconds % 60).padStart(2, "0")}
          </button>
        ) : (
          <button disabled={busy} onClick={start}>Record from microphone</button>
        )}
        <button disabled={busy || recording} onClick={() => file.current?.click()}>
          Upload a recording
        </button>
        <input ref={file} type="file" accept="audio/*" hidden
               onChange={(e) => {
                 const chosen = e.target.files?.[0];
                 if (chosen) onAudio(chosen, chosen.name);
                 e.target.value = "";
               }} />
      </div>
      {error && <p className="error">{error}</p>}
      <p className="hint">
        Runs voice activity detection, the signal quality gate and eGeMAPS
        prosody. Recognition needs a configured ASR backend; without one the
        gate and prosody still run.
      </p>
    </section>
  );
}
