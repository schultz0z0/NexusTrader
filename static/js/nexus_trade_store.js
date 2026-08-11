const NEXUS_BOT_ID = "nexus-trade";
const EVENT_TYPES = new Set([
  "nexus.runtime",
  "nexus.decision",
  "nexus.trade",
  "nexus.campaign",
  "nexus.report",
  "nexus.trial_changed",
  "nexus.proposal",
  "nexus.version_changed",
]);
const SENSITIVE_KEY = /(authorization|credential|otp|password|secret|ticket|token|api[_-]?key|(?:^|_)path$)/i;

const clone = (value) => structuredClone(value);

function emptyState() {
  return {
    schemaVersion: 1,
    snapshotVersion: 0,
    runtime: {},
    emergencyStop: false,
    lanes: [],
    activeCampaigns: [],
    campaign: { current: null, progress: { completed: 0, target: 300 } },
    decisions: [],
    trades: [],
    reports: [],
    proposals: [],
    trialChange: null,
    versionChange: null,
    auditEvents: [],
    connection: { status: "idle", lastUpdated: null },
  };
}

function hasSensitiveValue(value) {
  if (Array.isArray(value)) return value.some(hasSensitiveValue);
  if (value && typeof value === "object") {
    return Object.entries(value).some(([key, item]) => SENSITIVE_KEY.test(key) || hasSensitiveValue(item));
  }
  if (typeof value !== "string") return false;
  const normalized = value.trim().toLowerCase();
  return normalized.startsWith("bearer ")
    || normalized.includes("file://")
    || /[a-z]:[\\/]/i.test(value)
    || /(?:^|[?&])(token|ticket|secret|api[_-]?key)=/i.test(value);
}

function identityOf(payload, keys) {
  for (const key of keys) {
    if (payload?.[key] !== undefined && payload?.[key] !== null) return String(payload[key]);
  }
  return "";
}

function upsert(items, payload, keys) {
  const identity = identityOf(payload, keys);
  if (!identity) return [...items, clone(payload)];
  return [clone(payload), ...items.filter((item) => identityOf(item, keys) !== identity)];
}

function campaignView(campaigns) {
  const current = campaigns.find((item) => item?.status === "ACTIVE") || campaigns[0] || null;
  const source = current?.progress && typeof current.progress === "object" ? current.progress : current || {};
  const completed = Number(source.completed ?? source.operations ?? source.n_total ?? 0);
  const target = Number(source.target ?? 300);
  return {
    current: current ? clone(current) : null,
    progress: {
      completed: Number.isFinite(completed) && completed >= 0 ? completed : 0,
      target: Number.isFinite(target) && target > 0 ? target : 300,
    },
  };
}

function validSnapshot(snapshot) {
  return Boolean(snapshot)
    && typeof snapshot === "object"
    && snapshot.schema_version === 1
    && Number.isInteger(snapshot.snapshot_version)
    && snapshot.snapshot_version >= 1
    && !hasSensitiveValue(snapshot);
}

function validEvent(event) {
  return Boolean(event)
    && typeof event === "object"
    && EVENT_TYPES.has(event.type)
    && event.bot_id === NEXUS_BOT_ID
    && typeof event.event_id === "string"
    && Boolean(event.event_id.trim())
    && event.schema_version === 1
    && Number.isInteger(event.snapshot_version)
    && event.snapshot_version >= 1
    && event.payload
    && typeof event.payload === "object"
    && !Array.isArray(event.payload)
    && !hasSensitiveValue(event);
}

export function createNexusTradeStore(initial = {}) {
  let state = emptyState();
  const listeners = new Set();
  const seenEventIds = new Set();

  const notify = () => {
    const snapshot = clone(state);
    listeners.forEach((listener) => listener(snapshot));
  };

  const hydrate = (snapshot, emit = true) => {
    if (!validSnapshot(snapshot) || snapshot.snapshot_version < state.snapshotVersion) return false;
    const activeCampaigns = clone(snapshot.active_campaigns || []);
    state = {
      ...state,
      schemaVersion: snapshot.schema_version,
      snapshotVersion: snapshot.snapshot_version,
      runtime: clone(snapshot.runtime || {}),
      emergencyStop: Boolean(snapshot.emergency_stop ?? snapshot.runtime?.emergency_stop),
      lanes: clone(snapshot.lanes || []),
      activeCampaigns,
      campaign: campaignView(activeCampaigns),
      decisions: clone(snapshot.decisions || []),
      trades: clone(snapshot.trades || []),
      reports: clone(snapshot.reports || []),
      proposals: clone(snapshot.proposals || []),
      auditEvents: clone(snapshot.nexus_events || state.auditEvents),
      connection: { status: "live", lastUpdated: Date.now() },
    };
    for (const item of state.auditEvents) {
      if (typeof item?.event_id === "string") seenEventIds.add(item.event_id);
    }
    if (emit) notify();
    return true;
  };

  const apply = (event) => {
    if (!validEvent(event)
      || seenEventIds.has(event.event_id)
      || event.snapshot_version < state.snapshotVersion) return false;
    const payload = clone(event.payload);
    const next = { ...state, snapshotVersion: Math.max(state.snapshotVersion, event.snapshot_version) };

    if (event.type === "nexus.runtime") {
      const runtime = payload.runtime && typeof payload.runtime === "object" ? payload.runtime : payload;
      next.runtime = { ...state.runtime, ...runtime };
      next.emergencyStop = Boolean(payload.emergency_stop ?? next.runtime.emergency_stop ?? state.emergencyStop);
    } else if (event.type === "nexus.decision") {
      next.decisions = upsert(state.decisions, payload, ["id", "decision_id"]);
    } else if (event.type === "nexus.trade") {
      next.trades = upsert(state.trades, payload, ["id", "contract_id"]);
    } else if (event.type === "nexus.campaign") {
      const active = payload.status === "ACTIVE"
        ? upsert(state.activeCampaigns, payload, ["id"])
        : state.activeCampaigns.filter((item) => item.id !== payload.id);
      next.activeCampaigns = active;
      next.campaign = campaignView(active);
    } else if (event.type === "nexus.report") {
      next.reports = upsert(state.reports, payload, ["id"]);
    } else if (event.type === "nexus.proposal") {
      next.proposals = upsert(state.proposals, payload, ["id"]);
    } else if (event.type === "nexus.trial_changed") {
      next.trialChange = payload;
      if (payload.campaign) {
        next.activeCampaigns = upsert(state.activeCampaigns, payload.campaign, ["id"]);
        next.campaign = campaignView(next.activeCampaigns);
      }
      if (payload.version) {
        next.lanes = upsert(state.lanes, { lane: "challenger_trial", version: payload.version }, ["lane"]);
      }
    } else if (event.type === "nexus.version_changed") {
      next.versionChange = payload;
      if (Array.isArray(payload.lanes)) next.lanes = clone(payload.lanes);
      else if (payload.lane && payload.version) next.lanes = upsert(state.lanes, payload, ["lane"]);
    }

    seenEventIds.add(event.event_id);
    next.auditEvents = [...state.auditEvents, clone(event)].slice(-200);
    next.connection = { status: "live", lastUpdated: Date.now() };
    state = next;
    notify();
    return true;
  };

  const setConnection = (status) => {
    if (!["idle", "connecting", "live", "stale", "offline"].includes(status)) return false;
    state = { ...state, connection: { ...state.connection, status } };
    notify();
    return true;
  };

  if (validSnapshot(initial)) hydrate(initial, false);

  return {
    get: () => clone(state),
    hydrate,
    apply,
    setConnection,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export { NEXUS_BOT_ID };
