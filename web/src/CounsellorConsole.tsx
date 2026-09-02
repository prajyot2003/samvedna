import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type { AnsweredItem, HistoryPoint, InteractionState, Tier } from "./types";
import { ActionList } from "./components/ActionList";
import { AnsweredList } from "./components/AnsweredList";
import { AudioInput } from "./components/AudioInput";
import { CorrectionDialog } from "./components/CorrectionDialog";
import { NarrativeBox } from "./components/NarrativeBox";
import { NextQuestion } from "./components/NextQuestion";
import { OverrideDialog } from "./components/OverrideDialog";
import { Progress } from "./components/Progress";
import { ShortcutHelp } from "./components/ShortcutHelp";
import { StatusStrip } from "./components/StatusStrip";
import { SVIPanel } from "./components/SVIPanel";
import { Trajectory } from "./components/Trajectory";
import { Transcript } from "./components/Transcript";
import { StartScreen } from "./StartScreen";
import { useShortcuts } from "./hooks/useShortcuts";

const SCALE_LABELS: Record<string, string[]> = {
  "yes-no": ["No", "Yes"],
  "0-3": ["Not at all", "Several days", "More than half", "Nearly every day"],
  "0-4": ["None", "Mild", "Moderate", "Severe", "Extreme"],
};

/**
 * The live call screen.
 *
 * A counsellor using this is on the phone with someone describing the worst
 * thing that has happened to them. Three things must be readable without
 * hunting: what was said, what the assessment is, and what to ask next.
 * Everything else is placed where it does not compete.
 *
 * State comes from the REST response after each action; the WebSocket exists to
 * pick up changes this console did not cause and triggers a re-read rather than
 * merging events client-side. Two sources of truth would let the screen drift
 * out of step with the record, and the record is what the ledger holds.
 */
export function CounsellorConsole() {
  const [state, setState] = useState<InteractionState | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [answered, setAnswered] = useState<AnsweredItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overriding, setOverriding] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [correcting, setCorrecting] = useState<AnsweredItem | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [connected, setConnected] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const socket = useRef<WebSocket | null>(null);
  const lastTier = useRef<Tier | null>(null);

  const run = useCallback(async (work: () => Promise<InteractionState>) => {
    setBusy(true);
    setError(null);
    try {
      const next = await work();
      setState(next);
      if (next.svi) {
        api.history(next.interaction_id)
          .then((h) => setHistory(h.snapshots))
          .catch(() => { /* the trajectory is not load-bearing */ });
      }
      return next;
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      throw exc;
    } finally {
      setBusy(false);
    }
  }, []);

  // A tier change is the thing a counsellor most needs to notice and is the
  // easiest to miss while listening. Announced to assistive technology, and
  // never only by colour.
  useEffect(() => {
    const tier = state?.svi?.tier ?? null;
    if (tier && tier !== lastTier.current) {
      if (lastTier.current !== null) {
        setAnnouncement(
          `Assessment changed to ${tier}${state?.svi?.model_bypassed
            ? ", set by a safety rule rather than the score" : ""}.`);
      }
      lastTier.current = tier;
    }
  }, [state?.svi?.tier, state?.svi?.model_bypassed]);

  useEffect(() => {
    if (!state?.interaction_id || socket.current) return;
    const id = state.interaction_id;
    const ws = new WebSocket(api.feedUrl(id));
    socket.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (["tier_overridden", "actions_raised", "svi_computed"].includes(message.type)) {
        api.read(id).then(setState).catch(() => { /* keep the last good state */ });
      }
    };
    return () => { ws.close(); socket.current = null; };
  }, [state?.interaction_id]);

  const id = state?.interaction_id ?? "";
  const action = state?.next_action ?? null;

  const remember = useCallback((item: AnsweredItem) => {
    setAnswered((prev) => {
      const without = prev.filter((p) => p.id !== item.id);
      return [...without, item];
    });
  }, []);

  const answerSlot = useCallback((key: string, present: boolean, label?: string) => {
    remember({ id: `slot:${key}`, kind: "slot", slotKey: key,
               label: label ?? key.replace(/_/g, " "),
               answer: present ? "Yes" : "No" });
    return run(() => api.slot(id, key, present));
  }, [id, remember, run]);

  const answerScreener = useCallback(
    (instrument: string, index: number, value: number, scale?: string, label?: string) => {
      const names = SCALE_LABELS[scale ?? "0-3"] ?? SCALE_LABELS["0-3"];
      remember({
        id: `screener:${instrument}:${index}`, kind: "screener",
        instrument, itemIndex: index, scale,
        label: label ?? `${instrument.toUpperCase()} item ${index + 1}`,
        answer: names[value] ?? String(value),
      });
      return run(() => api.screener(id, instrument, index, value));
    }, [id, remember, run]);

  // --- keyboard ----------------------------------------------------------

  const shortcuts = useMemo(() => {
    const map: Record<string, () => void> = {
      "?": () => setShowHelp((v) => !v),
      escape: () => { setShowHelp(false); setReviewing(false); setCorrecting(null); },
    };
    if (!state || busy || overriding || correcting) return map;

    map.u = () => setReviewing(true);
    if (!action) return map;

    if (action.kind === "ask_consent") {
      map.y = () => run(() => api.consent(id, action.scope!, "granted"));
      map.n = () => run(() => api.consent(id, action.scope!, "declined"));
    } else if (action.kind === "ask_slot" || action.kind === "confirm_fact") {
      map.y = () => answerSlot(action.slot_key!, true, action.prompt);
      map.n = () => answerSlot(action.slot_key!, false, action.prompt);
    } else if (action.kind === "ask_screener") {
      const names = SCALE_LABELS[action.scale ?? "0-3"] ?? SCALE_LABELS["0-3"];
      names.forEach((_, value) => {
        map[String(value)] = () => answerScreener(
          action.instrument!, action.item_index!, value, action.scale ?? undefined,
          action.prompt);
      });
      if (action.scale === "yes-no") {
        map.n = () => answerScreener(action.instrument!, action.item_index!, 0,
                                     action.scale ?? undefined, action.prompt);
        map.y = () => answerScreener(action.instrument!, action.item_index!, 1,
                                     action.scale ?? undefined, action.prompt);
      }
    }
    return map;
  }, [action, answerScreener, answerSlot, busy, correcting, id, overriding, run, state]);

  useShortcuts(shortcuts, Boolean(state));

  // --- render ------------------------------------------------------------

  if (!state) {
    return (
      <StartScreen busy={busy} error={error}
                   onStart={(language) => {
                     setHistory([]); setAnswered([]); lastTier.current = null;
                     run(() => api.start(language)).catch(() => { /* shown inline */ });
                   }} />
    );
  }

  const tier: Tier = state.svi?.tier ?? "LOW";

  return (
    <div className="console">
      <p aria-live="polite" className="sr-only">{announcement}</p>

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
          <button className="link-button" onClick={() => setShowHelp(true)}>
            <kbd>?</kbd> shortcuts
          </button>
        </div>
      </header>

      <StatusStrip state={state} />
      {error && <p className="error banner" role="alert">{error}</p>}

      <div className="console-grid">
        <div className="col-left">
          <NextQuestion
            action={action}
            busy={busy}
            onSlot={(key, present) => answerSlot(key, present, action?.prompt)}
            onScreener={(instrument, index, value) =>
              answerScreener(instrument, index, value,
                             action?.scale ?? undefined, action?.prompt)}
            onConsent={(scope, decision) =>
              run(() => api.consent(id, scope, decision))}
          />
          <NarrativeBox busy={busy} language={state.language}
                        onSubmit={(text) => run(() => api.utterance(id, text))} />
          <Transcript entries={state.transcript} />
          <AudioInput busy={busy}
                      onAudio={(blob, filename) =>
                        run(() => api.audio(id, blob, filename))} />
        </div>

        <div className="col-right">
          <SVIPanel svi={state.svi} trajectory={<Trajectory points={history} />} />
          <div className="console-controls">
            <button disabled={busy || !state.svi}
                    onClick={() => setOverriding(true)}>
              Override assessment
            </button>
            <button disabled={busy} onClick={() => setReviewing(true)}>
              Review answers <kbd>U</kbd>
            </button>
            <button disabled={busy || state.closed}
                    onClick={() => run(() => api.close(id))}>
              Close
            </button>
          </div>
          <Progress coverage={state.coverage} />
          <ActionList actions={state.actions} />
        </div>
      </div>

      {showHelp && <ShortcutHelp onClose={() => setShowHelp(false)} />}

      {reviewing && (
        <AnsweredList items={answered} onClose={() => setReviewing(false)}
                      onCorrect={(item) => { setReviewing(false); setCorrecting(item); }} />
      )}

      {correcting && (
        <CorrectionDialog item={correcting} busy={busy}
          onCancel={() => setCorrecting(null)}
          onSubmit={(item, value) => {
            setCorrecting(null);
            if (item.kind === "slot") {
              answerSlot(item.slotKey!, Boolean(value), item.label);
            } else {
              answerScreener(item.instrument!, item.itemIndex!, Number(value),
                             item.scale, item.label);
            }
          }} />
      )}

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
