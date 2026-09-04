import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { InteractionState, Tier } from "./types";
import { ActionList } from "./components/ActionList";
import { Composer } from "./components/Composer";
import { NextQuestion } from "./components/NextQuestion";
import { OverrideDialog } from "./components/OverrideDialog";
import { StatusStrip } from "./components/StatusStrip";
import { SVIPanel } from "./components/SVIPanel";
import { Transcript } from "./components/Transcript";

/**
 * The live call screen.
 *
 * A counsellor using this is on the phone with someone describing the worst
 * thing that has happened to them. They can read three things: what was said,
 * what the assessment is, and what to ask next. Everything else is reference
 * material and is placed where it does not compete.
 *
 * State comes from the REST response after each action; the WebSocket exists to
 * pick up changes this console did not cause — another operator's override, a
 * transcript arriving from the IVRS leg. Rendering from a single server-owned
 * payload rather than merging events client-side means the screen cannot drift
 * out of step with the record.
 */
export function CounsellorConsole() {
  const [state, setState] = useState<InteractionState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overriding, setOverriding] = useState(false);
  const [connected, setConnected] = useState(false);
  const socket = useRef<WebSocket | null>(null);

  const run = useCallback(async (work: () => Promise<InteractionState>) => {
    setBusy(true);
    setError(null);
    try {
      setState(await work());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }, []);

  // Subscribe to changes this console did not cause.
  useEffect(() => {
    if (!state?.interaction_id || socket.current) return;
    const ws = new WebSocket(api.feedUrl(state.interaction_id));
    socket.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (["tier_overridden", "actions_raised", "svi_computed"].includes(message.type)) {
        api.read(state.interaction_id).then(setState).catch(() => { /* keep last */ });
      }
    };
    return () => { ws.close(); socket.current = null; };
  }, [state?.interaction_id]);

  if (!state) {
    return (
      <div className="start-screen">
        <div className="card">
          <span className="lbl">Start an interaction</span>
          <p className="muted">
            A new call. Consent is requested before anything is assessed.
          </p>
          {error && <p className="error">{error}</p>}
          <div className="answers">
            <button className="primary" disabled={busy}
                    onClick={() => run(() => api.start("hi"))}>
              Hindi caller
            </button>
            <button disabled={busy}
                    onClick={() => run(() => api.start("bho"))}>
              Bhojpuri caller
            </button>
          </div>
        </div>
      </div>
    );
  }

  const id = state.interaction_id;
  const tier: Tier = state.svi?.tier ?? "LOW";

  return (
    <div className="console">
      <header className="console-header">
        <div>
          <span className="lbl">Interaction</span>
          <b className="mono">{id}</b>
        </div>
        <div className="meta">
          <span>{state.language === "bho" ? "Bhojpuri" : "Hindi"}</span>
          <span>{state.channel.toUpperCase()}</span>
          {state.district && <span>{state.district}</span>}
          <span className={`link${connected ? " live" : ""}`}>
            {connected ? "live" : "reconnecting"}
          </span>
        </div>
      </header>

      <StatusStrip state={state} />
      {error && <p className="error banner">{error}</p>}

      <div className="console-grid">
        <div className="col-left">
          <Transcript entries={state.transcript} />
          <Composer
            busy={busy}
            disabled={state.closed}
            language={state.language}
            onSubmit={(text) => run(() => api.utterance(id, text))}
            onDictate={async (blob) => {
              const result = await api.dictate(id, blob);
              setState(result.state);
              return result;
            }}
          />
          <NextQuestion
            action={state.next_action}
            busy={busy}
            onSlot={(key, present) => run(() => api.slot(id, key, present))}
            onScreener={(instrument, index, value) =>
              run(() => api.screener(id, instrument, index, value))}
            onConsent={(scope, decision) =>
              run(() => api.consent(id, scope, decision))}
          />
        </div>

        <div className="col-right">
          <SVIPanel svi={state.svi} />
          <div className="console-controls">
            <button disabled={busy || !state.svi}
                    onClick={() => setOverriding(true)}>
              Override assessment
            </button>
            <button disabled={busy || state.closed}
                    onClick={() => run(() => api.close(id))}>
              Close interaction
            </button>
          </div>
          <ActionList actions={state.actions} />
        </div>
      </div>

      {overriding && (
        <OverrideDialog
          current={tier}
          busy={busy}
          onCancel={() => setOverriding(false)}
          onSubmit={(toTier, reason) => {
            setOverriding(false);
            run(() => api.override(id, toTier, "counsellor-local", reason));
          }}
        />
      )}
    </div>
  );
}
