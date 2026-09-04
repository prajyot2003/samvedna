import type { DashboardSummary, Dictation, InteractionState, Languages, Readiness, Tier } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

/** Set by the gateway in deployment; a local default keeps `make dev` usable. */
const OPERATOR = import.meta.env.VITE_OPERATOR_ID ?? "counsellor-local";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Operator-Id": OPERATOR,
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch { /* keep the status text */ }
    throw new Error(`${response.status} — ${detail}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  start: (language: string, channel = "ivrs", district = "Gaya") =>
    request<InteractionState>("/interactions", {
      method: "POST",
      body: JSON.stringify({ language, channel, district }),
    }),

  read: (id: string) => request<InteractionState>(`/interactions/${id}`),

  consent: (id: string, scope: string, decision: string) =>
    request<InteractionState>(`/interactions/${id}/consent`, {
      method: "POST",
      body: JSON.stringify({ scope, decision, method: "spoken" }),
    }),

  utterance: (id: string, text: string, speaker = "caller") =>
    request<InteractionState>(`/interactions/${id}/utterance`, {
      method: "POST",
      body: JSON.stringify({ text, speaker }),
    }),

  slot: (id: string, key: string, present: boolean) =>
    request<InteractionState>(`/interactions/${id}/slot`, {
      method: "POST",
      body: JSON.stringify({ key, present }),
    }),

  screener: (id: string, instrument: string, itemIndex: number, value: number) =>
    request<InteractionState>(`/interactions/${id}/screener`, {
      method: "POST",
      body: JSON.stringify({ instrument, item_index: itemIndex, value }),
    }),

  override: (id: string, toTier: Tier, counsellorId: string, reason: string) =>
    request<InteractionState>(`/interactions/${id}/override`, {
      method: "POST",
      body: JSON.stringify({ to_tier: toTier, counsellor_id: counsellorId, reason }),
    }),

  close: (id: string) =>
    request<InteractionState>(`/interactions/${id}/close`, { method: "POST" }),

  /**
   * Speech to text for review. Nothing is added to the record here — the
   * counsellor edits what comes back and submits it through `utterance`.
   */
  dictate: async (id: string, blob: Blob) => {
    const form = new FormData();
    form.append("file", blob, "dictation.wav");
    const response = await fetch(`${BASE}/interactions/${id}/dictate`, {
      method: "POST",
      headers: { "X-Operator-Id": OPERATOR },   // no Content-Type: the browser
      body: form,                               // must set the multipart boundary
    });
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail ?? detail; } catch { /* keep */ }
      throw new Error(`${response.status} — ${detail}`);
    }
    return response.json() as Promise<Dictation>;
  },

  dashboard: () => request<DashboardSummary>("/dashboard/summary"),
  readiness: () => request<Readiness>("/readiness"),
  languages: () => request<Languages>("/languages"),
  verifyAudit: () => request<{
    ok: boolean; records: number; head: string; summary: string;
    failures: { seq: number; reason: string }[];
  }>("/audit/verify"),

  feedUrl: (id: string) =>
    `${BASE.replace(/^http/, "ws")}/ws/interactions/${id}`,
};
