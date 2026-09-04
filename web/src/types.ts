/** Mirrors `Session.public_state` in services/pipeline.py. */

export type Tier = "LOW" | "MODERATE" | "HIGH" | "CRITICAL";

export interface SVI {
  score: number;
  computed_tier: Tier;
  tier: Tier;
  channel_a: number;
  channel_b: number;
  channel_c_delta: number;
  base: number;
  abstained: boolean;
  abstention_reasons: string[];
  coarse_domains: string[];
  contributions: Record<string, number>;
  rules_triggered: string[];
  rule_bases: string[];
  model_bypassed: boolean;
}

export interface Action {
  action_id: string;
  label: string;
  type: string;
  owner: string;
  owner_label: string;
  statutory_basis: string;
  sla_minutes: number;
  due_at: string | null;
  immediate: boolean;
  triggered_by: string[];
}

export interface NextAction {
  kind: string;
  prompt: string;
  language: string;
  slot_key: string | null;
  instrument: string | null;
  item_index: number | null;
  scale: string | null;
  scope: string | null;
  rationale: string;
}

export interface TranscriptEntry {
  speaker: string;
  text: string;
  redactions: Record<string, number>;
  at: string;
}

export interface Coverage {
  phase: string;
  context_coverage: number;
  screening_coverage: number;
  cssrs_administered: boolean;
  slots_asked: number;
  slots_total: number;
  pending_confirmations: string[];
  crisis_flag: boolean;
}

export interface InteractionState {
  interaction_id: string;
  channel: string;
  language: string;
  district: string | null;
  passive_mode: boolean;
  transcript: TranscriptEntry[];
  svi: SVI | null;
  coverage: Coverage;
  signal: { confidence: string; reasons: string[] };
  actions: Action[];
  next_action: NextAction | null;
  closed: boolean;
}

export interface DashboardSummary {
  tier_distribution: Partial<Record<Tier, number>>;
  live_interactions: number;
  overdue_actions: {
    interaction_id: string; action_id: string; label: string;
    owner: string; due_at: string; basis: string;
  }[];
  overdue_count: number;
}

export interface Readiness {
  production_ready: boolean;
  blockers: string[];
  lexicons: Record<string, {
    name: string; version: string; terms: number;
    reviewed: boolean; warning: string | null;
  }>;
}

export interface Dictation {
  /** What the recogniser heard. Empty when no backend is loaded, or on silence. */
  text: string;
  recognised: boolean;
  asr_configured: boolean;
  signal_confidence: string;
  quality_reasons: string[];
  state: InteractionState;
}
