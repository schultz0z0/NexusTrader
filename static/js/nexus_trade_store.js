const NEXUS_BOT_ID = "nexus-trade";
const EVENT_TYPES = new Set([
  "nexus.runtime",
  "nexus.decision",
  "nexus.trade",
  "nexus.position",
  "nexus.campaign",
  "nexus.report",
  "nexus.trial_changed",
  "nexus.proposal",
  "nexus.version_changed",
  "nexus.learning",
]);
const SENSITIVE_KEY = /(authorization|credential|otp|password|secret|ticket|token|api[_-]?key|(?:^|_)path$)/i;

const clone = (value) => structuredClone(value);

function emptyState() {
  return {
    schemaVersion: 1,
    snapshotVersion: 0,
    runtime: {},
    emergencyStop: false,
    championManagement: null,
    lanes: [],
    laneStates: {},
    positions: [],
    market: null,
    lastTick: null,
    activeCampaigns: [],
    campaign: { current: null, progress: { completed: 0, target: 300 } },
    decisions: [],
    trades: [],
    reports: [],
    proposals: [],
    learning: { jobs: [], attempts: [], candidates: [] },
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
  const current = campaigns.find((item) => (
    item?.lane === "challenger_trial" && item?.status === "ACTIVE"
  )) || null;
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

function validMarketEvent(event) {
  if (!event || typeof event !== "object"
    || !["market.history", "market.tick"].includes(event.type)
    || event.bot_id !== NEXUS_BOT_ID
    || event.schema_version !== 1
    || typeof event.event_id !== "string"
    || !event.event_id.trim()
    || event.symbol !== "R_100"
    || Number(event.timeframe_seconds) !== 60
    || hasSensitiveValue(event)) return false;
  if (event.type === "market.history") return Array.isArray(event.points);
  return Number.isFinite(Number(event.epoch)) && Number.isFinite(Number(event.price));
}

function validPosition(payload) {
  return Boolean(payload)
    && ["champion_baseline", "challenger_trial"].includes(payload.lane)
    && Number.isInteger(payload.contract_id) && payload.contract_id > 0
    && typeof payload.owner_decision_id === "string" && Boolean(payload.owner_decision_id.trim())
    && ["OPEN", "UPDATED", "CLOSED"].includes(payload.status)
    && Number.isInteger(payload.update_epoch) && payload.update_epoch >= 0;
}

export function createNexusTradeStore(initial = {}) {
  let state = emptyState();
  const listeners = new Set();
  const seenEventIds = new Set();
  const positionEpochs = new Map();
  const closedPositions = new Set();

  const notify = (change = { kind: "state", type: "state" }) => {
    const snapshot = clone(state);
    const detail = clone(change);
    listeners.forEach((listener) => listener(snapshot, detail));
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
      championManagement: clone(snapshot.champion_management || state.championManagement),
      lanes: clone(snapshot.lanes || []),
      laneStates: clone(snapshot.lane_states || {}),
      positions: clone(snapshot.positions || []),
      market: clone(snapshot.market || state.market),
      lastTick: clone(snapshot.last_tick || state.lastTick),
      activeCampaigns,
      campaign: campaignView(activeCampaigns),
      decisions: clone(snapshot.decisions || []),
      trades: clone(snapshot.trades || []),
      reports: clone(snapshot.reports || []),
      proposals: clone(snapshot.proposals || []),
      learning: clone(snapshot.learning || { jobs: [], attempts: [], candidates: [] }),
      auditEvents: clone(snapshot.nexus_events || state.auditEvents),
      connection: { status: "live", lastUpdated: Date.now() },
    };
    for (const item of state.auditEvents) {
      if (typeof item?.event_id === "string") seenEventIds.add(item.event_id);
    }
    for (const item of state.positions) {
      const key = `${item.lane}:${item.contract_id}`;
      positionEpochs.set(key, Number(item.update_epoch || 0));
    }
    if (emit) notify({ kind: "snapshot", type: "snapshot", snapshotVersion: state.snapshotVersion });
    return true;
  };

  const apply = (event) => {
    const marketEvent = validMarketEvent(event);
    if ((!marketEvent && !validEvent(event))
      || seenEventIds.has(event.event_id)
      || (!marketEvent && event.snapshot_version < state.snapshotVersion)) return false;
    const payload = clone(event.payload || {});
    const next = {
      ...state,
      snapshotVersion: marketEvent
        ? state.snapshotVersion
        : Math.max(state.snapshotVersion, event.snapshot_version),
    };

    if (event.type === "market.history") {
      next.market = clone(event);
      next.lastTick = null;
    } else if (event.type === "market.tick") {
      if (!state.market) return false;
      const market = clone(state.market);
      const point = event.candle;
      if (point && Number.isFinite(Number(point.time))) {
        const points = [...(market.points || [])];
        if (points.at(-1)?.time === point.time) points[points.length - 1] = clone(point);
        else points.push(clone(point));
        market.points = points.slice(-500);
      }
      next.market = market;
      next.lastTick = clone(event);
    } else if (event.type === "nexus.runtime") {
      const runtime = payload.runtime && typeof payload.runtime === "object" ? payload.runtime : payload;
      next.runtime = { ...state.runtime, ...runtime };
      next.emergencyStop = Boolean(payload.emergency_stop ?? next.runtime.emergency_stop ?? state.emergencyStop);
      next.championManagement = clone(payload.champion_management || state.championManagement);
    } else if (event.type === "nexus.learning") {
      next.learning = clone(payload.learning || state.learning);
    } else if (event.type === "nexus.decision") {
      next.decisions = upsert(state.decisions, payload, ["id", "decision_id"]);
    } else if (event.type === "nexus.trade") {
      const tradeIdentity = identityOf(payload, ["id", "contract_id"]);
      const alreadyKnown = Boolean(tradeIdentity) && state.trades.some(
        (item) => identityOf(item, ["id", "contract_id"]) === tradeIdentity,
      );
      next.trades = upsert(state.trades, payload, ["id", "contract_id"]);
      const trialCampaign = state.activeCampaigns.find((item) => (
        item?.lane === "challenger_trial"
        && item?.status === "ACTIVE"
        && item?.id === payload.campaign_id
      ));
      if (!alreadyKnown && payload.lane === "challenger_trial"
        && payload.status === "closed" && trialCampaign) {
        next.activeCampaigns = state.activeCampaigns.map((item) => {
          if (item.id !== trialCampaign.id) return item;
          const current = campaignView([item]).progress;
          return {
            ...item,
            progress: { completed: current.completed + 1, target: current.target },
          };
        });
        next.campaign = campaignView(next.activeCampaigns);
      }
    } else if (event.type === "nexus.position") {
      if (!validPosition(payload)) return false;
      const key = `${payload.lane}:${payload.contract_id}`;
      const previousEpoch = positionEpochs.get(key);
      if (closedPositions.has(key) || (previousEpoch !== undefined && payload.update_epoch <= previousEpoch)) return false;
      const parallel = state.positions.some((item) => item.lane === payload.lane && item.contract_id !== payload.contract_id);
      if (parallel) return false;
      positionEpochs.set(key, payload.update_epoch);
      if (payload.status === "CLOSED") {
        closedPositions.add(key);
        next.positions = state.positions.filter((item) => item.lane !== payload.lane);
      } else {
        const previous = state.positions.find((item) => item.lane === payload.lane) || {};
        next.positions = upsert(state.positions, { ...previous, ...payload }, ["lane"]);
      }
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
    next.auditEvents = marketEvent
      ? state.auditEvents
      : [...state.auditEvents, clone(event)].slice(-200);
    next.connection = { status: "live", lastUpdated: Date.now() };
    state = next;
    notify({ kind: "event", type: event.type, eventId: event.event_id, snapshotVersion: event.snapshot_version });
    return true;
  };

  const setConnection = (status) => {
    if (!["idle", "connecting", "live", "stale", "offline"].includes(status)) return false;
    state = { ...state, connection: { ...state.connection, status } };
    notify({ kind: "connection", type: status });
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

export async function reconcileNexusTradeStore(store, snapshotLoader) {
  if (!store?.hydrate || !store?.setConnection || typeof snapshotLoader !== "function") {
    throw new Error("Reconciliação NexusTrade inválida");
  }
  store.setConnection("connecting");
  try {
    const snapshot = await snapshotLoader();
    if (!store.hydrate(snapshot)) throw new Error("Snapshot de reconnect stale ou inválido");
    return true;
  } catch (error) {
    store.setConnection("stale");
    throw error;
  }
}

export { NEXUS_BOT_ID };
