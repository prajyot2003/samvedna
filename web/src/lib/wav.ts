/**
 * Microphone capture that produces a WAV the server can actually read.
 *
 * WHY NOT MediaRecorder. It is the obvious API and it is the wrong one here.
 * Chrome gives you `audio/webm;codecs=opus` and Safari gives you MP4/AAC;
 * libsndfile — which `soundfile` wraps, and which the /dictate endpoint uses —
 * can open neither. The upload would fail with a decoder error that reads like
 * a broken microphone, on the one screen where a confusing failure is least
 * affordable.
 *
 * So this taps the audio graph directly and writes the WAV header itself.
 * There is no codec involved and nothing to negotiate.
 *
 * The context is opened at 16 kHz because that is Whisper's working rate and
 * comfortably above the 3400 Hz ceiling of the telephony band the rest of the
 * pipeline models. The browser resamples properly on the way in, which is
 * better than decimating the buffer ourselves and cheaper than shipping 48 kHz
 * over the wire.
 */

const TARGET_RATE = 16000;

export type Recorder = {
  /** Stops capture, releases the microphone, and returns the recording. */
  stop: () => Promise<Blob>;
  /** Releases the microphone and discards whatever was captured. */
  cancel: () => void;
};

export async function startRecording(): Promise<Recorder> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });

  const context = new AudioContext({ sampleRate: TARGET_RATE });
  const source = context.createMediaStreamSource(stream);

  // ScriptProcessorNode is deprecated in favour of AudioWorklet, which needs a
  // separately-served module file. For a single mono tap at 16 kHz the cost of
  // that indirection is not worth paying, and every browser this console
  // targets still supports this node.
  const processor = context.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  let length = 0;

  processor.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    chunks.push(new Float32Array(input));
    length += input.length;
  };

  source.connect(processor);
  // Required for the processor to run in Chrome. A zero gain keeps the
  // counsellor from hearing their own microphone echoed back mid-call.
  const mute = context.createGain();
  mute.gain.value = 0;
  processor.connect(mute);
  mute.connect(context.destination);

  const release = () => {
    processor.disconnect();
    source.disconnect();
    mute.disconnect();
    stream.getTracks().forEach((track) => track.stop());
    void context.close();
  };

  return {
    stop: async () => {
      processor.onaudioprocess = null;
      const rate = context.sampleRate;
      release();

      const samples = new Float32Array(length);
      let offset = 0;
      for (const chunk of chunks) {
        samples.set(chunk, offset);
        offset += chunk.length;
      }
      return encodeWav(samples, rate);
    },
    cancel: () => {
      processor.onaudioprocess = null;
      release();
    },
  };
}

/** Mono 16-bit PCM WAV. Written by hand so no codec sits in the path. */
export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };

  ascii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);          // PCM header size
  view.setUint16(20, 1, true);           // format: PCM
  view.setUint16(22, 1, true);           // channels: mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);   // byte rate
  view.setUint16(32, 2, true);           // block align
  view.setUint16(34, 16, true);          // bits per sample
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);

  // Float [-1, 1] to signed 16-bit, clamped. Values outside the range are the
  // normal result of a loud speaker close to the microphone; wrapping them
  // instead of clamping turns a loud voice into a burst of noise, which the
  // signal quality gate would then correctly and uselessly reject.
  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }

  return new Blob([view], { type: "audio/wav" });
}
