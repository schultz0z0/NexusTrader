const HARD_GATES = new Set([
  "MINIMUM_SAMPLE",
  "DATA_INTEGRITY",
  "COMPARABLE_PROVENANCE",
  "RISK_LIMITS",
  "CHANGE_BUDGET",
]);
const UNSAFE_POSITIONS = new Set(["RESERVED", "SUBMITTING", "RECONCILE_PENDING", "QUARANTINED", "ACTIVE"]);

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function configurationRows(before = {}, after = {}) {
  const rows = [];
  const indicators = [...new Set([...Object.keys(before || {}), ...Object.keys(after || {})])].sort();
  for (const indicator of indicators) {
    if (!(indicator in before)) {
      rows.push({ path: indicator, before: null, after: after[indicator], change: "added", family: "indicator_addition" });
      continue;
    }
    if (!(indicator in after)) {
      rows.push({ path: indicator, before: before[indicator], after: null, change: "removed", family: "indicator_removal" });
      continue;
    }
    const left = before[indicator];
    const right = after[indicator];
    if (left && right && typeof left === "object" && typeof right === "object" && !Array.isArray(left) && !Array.isArray(right)) {
      const fields = [...new Set([...Object.keys(left), ...Object.keys(right)])].sort();
      for (const field of fields) {
        if (!sameValue(left[field], right[field])) {
          rows.push({
            path: `${indicator}.${field}`,
            before: left[field] ?? null,
            after: right[field] ?? null,
            change: field in left && field in right ? "modified" : field in right ? "added" : "removed",
            family: "indicator_configuration",
          });
        }
      }
    } else if (!sameValue(left, right)) {
      rows.push({ path: indicator, before: left, after: right, change: "modified", family: "indicator_configuration" });
    }
  }
  return rows;
}

function setRows(prefix, before = [], after = [], family) {
  const left = new Set(Array.isArray(before) ? before.map(String) : []);
  const right = new Set(Array.isArray(after) ? after.map(String) : []);
  return [
    ...[...left].filter((item) => !right.has(item)).sort().map((item) => ({ path: `${prefix}.${item}`, before: true, after: false, change: "removed", family })),
    ...[...right].filter((item) => !left.has(item)).sort().map((item) => ({ path: `${prefix}.${item}`, before: false, after: true, change: "added", family })),
  ];
}

export function buildDiff(before = {}, after = {}) {
  const rows = [
    ...configurationRows(before.configuration, after.configuration),
    ...setRows("features", before.feature_schema, after.feature_schema, "feature_schema"),
    ...setRows("entry_rules", before.entry_rules, after.entry_rules, "entry_rule"),
  ];
  if (!sameValue(before.model, after.model)) {
    rows.push({ path: "model", before: before.model ?? null, after: after.model ?? null, change: "modified", family: "model" });
  }
  return rows;
}

function championPosition(snapshot) {
  const lane = (snapshot?.lanes || []).find((item) => item?.lane === "champion_baseline") || {};
  return String(lane?.state?.position_status || lane?.position_status || "").toUpperCase();
}

export function evaluatePromotionReadiness({ snapshot = {}, proposal = null, report = null } = {}) {
  const reasons = [];
  const expectedRevision = Number.isInteger(snapshot.snapshotVersion) && snapshot.snapshotVersion > 0
    ? snapshot.snapshotVersion : null;
  if (!expectedRevision) reasons.push("Revisão durável indisponível.");
  if (!proposal || proposal.status !== "PENDING_USER_REVIEW") reasons.push("Não existe proposta pendente para esta campanha.");
  if (Boolean(snapshot.runtime?.champion_enabled ?? snapshot.runtime?.enabled)) reasons.push("Pare o Champion antes de decidir.");
  const position = championPosition(snapshot);
  if (!position || UNSAFE_POSITIONS.has(position)) reasons.push("A lane Champion ainda possui posição, intent ou reconciliação pendente.");
  const openChampion = (snapshot.trades || []).some((trade) => {
    const lane = trade?.lane || trade?.metadata?.lane;
    return lane === "champion_baseline" && !["won", "lost", "tie", "closed"].includes(String(trade?.result || trade?.status).toLowerCase());
  });
  if (openChampion) reasons.push("Há contrato Champion ainda aberto.");

  const gates = Array.isArray(report?.gates) ? report.gates : [];
  const failedGates = gates.filter((gate) => gate?.status !== "PASS").map((gate) => String(gate.code));
  const gateMap = new Map(gates.map((gate) => [String(gate?.code), String(gate?.status)]));
  const hardFailures = [...HARD_GATES].filter((code) => gateMap.get(code) !== "PASS");
  if (hardFailures.length) reasons.push(`Gate não negociável pendente/falhou: ${hardFailures.join(", ")}.`);

  const recommendation = String(report?.recommendation || "INCONCLUSIVE");
  const requiresReinforced = recommendation === "REANALYZE" && failedGates.some((code) => !HARD_GATES.has(code));
  if (!["EVOLVE", "RECOMMEND_EVOLUTION", "REANALYZE"].includes(recommendation)) reasons.push("A recomendação ainda não permite promoção.");
  if (recommendation !== "REANALYZE" && failedGates.length) reasons.push("A recomendação possui gates não aprovados.");

  return {
    available: reasons.length === 0,
    requiresReinforced,
    expectedRevision,
    reasons,
    failedGates,
  };
}

export function buildGovernancePayload({
  expectedRevision,
  reason,
  reinforcedConfirmation = false,
  requestId = globalThis.crypto?.randomUUID?.(),
} = {}) {
  if (!Number.isInteger(expectedRevision) || expectedRevision < 1) throw new Error("Revisão durável inválida.");
  const normalizedReason = String(reason || "").trim();
  if (!normalizedReason) throw new Error("A justificativa humana é obrigatória.");
  const normalizedRequestId = String(requestId || "").trim();
  if (!normalizedRequestId) throw new Error("Identidade da ação humana indisponível.");
  return {
    expected_revision: expectedRevision,
    request_id: normalizedRequestId,
    reason: normalizedReason,
    reinforced_confirmation: Boolean(reinforcedConfirmation),
  };
}

export { HARD_GATES };
