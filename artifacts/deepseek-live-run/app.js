(function () {
  "use strict";

  var DEMO_COMMAND = "python3 scripts/run_demo.py";
  var data = window.DRIFTGUARD_DATA;
  var activeCommand = isObject(data) && typeof data.command === "string" ? data.command : DEMO_COMMAND;
  var report = data && isObject(data.report) ? data.report : {};
  var selectedTile = null;

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function hasValue(value) {
    return value !== undefined && value !== null && value !== "";
  }

  function setText(id, value, fallback) {
    var element = byId(id);
    if (!element) {
      return;
    }
    element.textContent = hasValue(value) ? String(value) : (fallback || "Unavailable");
  }

  function get(source, path, fallback) {
    var cursor = source;
    for (var index = 0; index < path.length; index += 1) {
      if (!isObject(cursor) && !Array.isArray(cursor)) {
        return fallback;
      }
      cursor = cursor[path[index]];
      if (cursor === undefined || cursor === null) {
        return fallback;
      }
    }
    return cursor;
  }

  function finiteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function fixed(value, digits) {
    return finiteNumber(value) ? value.toFixed(digits) : "--";
  }

  function signed(value, digits) {
    if (!finiteNumber(value)) {
      return "--";
    }
    return (value > 0 ? "+" : "") + value.toFixed(digits);
  }

  function percent(value, digits) {
    return finiteNumber(value) ? (value * 100).toFixed(digits || 0) + "%" : "--";
  }

  function metricValue(value) {
    return finiteNumber(value) ? value.toFixed(2) + " (" + percent(value, 0) + ")" : "--";
  }

  function deltaValue(value) {
    return finiteNumber(value) ? signed(value, 2) + " (" + signed(value * 100, 0) + " pp)" : "--";
  }

  function pValue(value) {
    if (!finiteNumber(value)) {
      return "--";
    }
    return value > 0 && value < 0.000001 ? value.toExponential(3) : value.toFixed(6);
  }

  function humanize(value) {
    if (!hasValue(value)) {
      return "Unavailable";
    }
    return String(value).replace(/_/g, " ");
  }

  function setBadge(id, label, tone) {
    var element = byId(id);
    if (!element) {
      return;
    }
    element.textContent = label;
    element.className = "status-badge status-" + tone;
  }

  function setBar(id, value) {
    var element = byId(id);
    if (!element) {
      return;
    }
    var bounded = finiteNumber(value) ? Math.max(0, Math.min(1, value)) : 0;
    element.style.width = (bounded * 100).toFixed(1) + "%";
  }

  function formatTimestamp(value) {
    if (!finiteNumber(value)) {
      return "Timestamp unavailable";
    }
    try {
      return new Intl.DateTimeFormat("en", {
        month: "short",
        day: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      }).format(new Date(value * 1000));
    } catch (error) {
      return "Timestamp unavailable";
    }
  }

  function showDataError(message) {
    var alert = byId("data-alert");
    if (alert) {
      alert.hidden = false;
    }
    setText("data-alert-detail", message, "Dashboard data is invalid.");
  }

  function renderVerdict() {
    var action = get(report, ["decision", "action"], "");
    var isRollback = String(action).indexOf("rollback") !== -1;
    var isKeep = action === "keep_change";
    var reasons = get(report, ["decision", "reasons"], []);
    var summary = get(report, ["summary"], {});
    var comparison = get(report, ["comparison"], {});
    var resolved = finiteNumber(comparison.records_resolved) ? comparison.records_resolved : get(summary, ["records_resolved"], null);
    var registered = finiteNumber(comparison.records_registered) ? comparison.records_registered : get(summary, ["records_registered"], null);
    var ledgerIntegrity = get(report, ["integrity", "ledger"], {});
    var comparisonIntegrity = get(report, ["integrity", "comparison_ledger"], {});
    var proposalIntegrity = get(report, ["integrity", "proposal_log"], {});
    var experimentIntegrity = get(report, ["integrity", "experiment_log"], {});
    var chainReports = [ledgerIntegrity, proposalIntegrity];
    if (hasValue(experimentIntegrity.valid)) {
      chainReports.push(experimentIntegrity);
    }
    if (hasValue(comparisonIntegrity.valid)) {
      chainReports.push(comparisonIntegrity);
    }
    var chainValid = chainReports.every(function (chain) { return chain.valid === true; });
    var verifiedEvents = chainReports.reduce(function (total, chain) {
      return total + (finiteNumber(chain.events_verified) ? chain.events_verified : 0);
    }, 0);

    var tone = isRollback ? "danger" : (isKeep ? "success" : "warning");
    setText("verdict-heading", isRollback ? "ROLLBACK" : humanize(action).toUpperCase());
    setBadge(
      "proposal-status",
      isRollback ? "CHANGE REJECTED" : (isKeep ? "CHANGE VERIFIED" : "REVIEW REQUIRED"),
      tone
    );
    setText("verdict-summary", Array.isArray(reasons) && reasons.length ? reasons[0] : "No decision reason was recorded.");
    setText("proposal-id", get(data, ["proposal", "id"], "Unavailable"));
    setText("action-value", isRollback ? "ROLLBACK" : humanize(action).toUpperCase());
    var learningAction = get(report, ["policy_execution", "learning_action"], "");
    var learningPaused = String(learningAction).indexOf("pause") !== -1;
    setText("lesson-action", isRollback || learningPaused ? "PAUSE LESSONS" : (isKeep ? "NO NEW LESSON" : "PAUSE LEARNING"));
    byId("lesson-action").className = isRollback || learningPaused ? "warning-text" : "success-text";
    setText("resolution-count", hasValue(resolved) && hasValue(registered) ? resolved + "/" + registered + " resolved" : "Resolution count unavailable");
    setText("chain-status", chainValid ? "Hash chain verified" : "Hash chain unverified");
    setText("integrity-footer", chainValid ? verifiedEvents + " hash-chained events verified" : "Integrity check failed");
    byId("chain-status").className = chainValid ? "success-text" : "danger-text";
    byId("action-value").className = tone + "-text";
    byId("verdict-band").classList.toggle("is-success", isKeep);
  }

  function renderPrediction() {
    var verification = get(report, ["proposal_verification"], {});
    var proposal = get(data, ["proposal"], {});
    var baseline = finiteNumber(verification.baseline) ? verification.baseline : proposal.baseline;
    var predicted = finiteNumber(verification.predicted_delta) ? verification.predicted_delta : proposal.predicted_delta;
    var candidate = verification.current_value;
    var actual = verification.actual_delta;

    setText("metric-name", humanize(proposal.metric));
    setText("baseline-value", metricValue(baseline));
    setText("predicted-delta", deltaValue(predicted));
    setText("candidate-value", metricValue(candidate));
    setText("actual-delta", deltaValue(actual));
    var holdout = get(report, ["comparison", "scope"], "") === "holdout";
    setText("baseline-note", holdout ? "development baseline" : "locked before change");
    setText("candidate-note", holdout ? "holdout candidate" : "observed after change");
    var verified = verification.status === "verified";
    byId("actual-delta").className = verified ? "success-text" : "danger-text";
    byId("actual-delta").parentElement.classList.toggle("is-success", verified);
    if (finiteNumber(predicted) && finiteNumber(actual)) {
      setText("prediction-miss", signed(actual - predicted, 2) + " vs locked claim");
    } else {
      setText("prediction-miss", "Verification data unavailable");
    }
  }

  function findFirstEvent(events, predicate) {
    if (!Array.isArray(events)) {
      return null;
    }
    for (var index = 0; index < events.length; index += 1) {
      if (predicate(events[index])) {
        return events[index];
      }
    }
    return null;
  }

  function renderTimeline() {
    var experimentEvents = Array.isArray(data.experiment_events) ? data.experiment_events : [];
    var ledger = Array.isArray(data.ledger) ? data.ledger : [];
    var proposalEvents = Array.isArray(data.proposal_events) ? data.proposal_events : [];
    var baseline = findFirstEvent(experimentEvents, function (event) { return event.ev === "baseline_started"; }) || findFirstEvent(ledger, function (event) {
      return event.ev === "register" && get(event, ["payload", "phase"], "") === "baseline";
    });
    var locked = findFirstEvent(experimentEvents, function (event) { return event.ev === "proposal_locked"; }) || findFirstEvent(proposalEvents, function (event) { return event.ev === "propose"; });
    var candidate = findFirstEvent(experimentEvents, function (event) { return event.ev === "candidate_started"; }) || findFirstEvent(ledger, function (event) {
      return event.ev === "register" && get(event, ["payload", "phase"], "") === "candidate";
    });
    var verification = findFirstEvent(experimentEvents, function (event) { return event.ev === "decision_made"; }) || findFirstEvent(proposalEvents, function (event) { return event.ev === "verify"; });

    setText("time-baseline", formatTimestamp(baseline && baseline.ts));
    setText("time-proposal", formatTimestamp(locked && locked.ts));
    setText("time-candidate", formatTimestamp(candidate && candidate.ts));
    setText("time-verification", formatTimestamp(verification && verification.ts));
    setText(
      "candidate-step-label",
      get(report, ["comparison", "scope"], "") === "holdout" ?
        "Holdout control + candidate" :
        get(report, ["comparison", "mode"], "") === "interleaved_control" ?
          "Interleaved control + candidate" : "Candidate"
    );
    var kept = get(report, ["decision", "action"], "") === "keep_change";
    byId("verification-step").className = "timeline-step " + (kept ? "is-success" : "is-danger");
  }

  function pairLedgerEvents() {
    var events = Array.isArray(data.ledger) ? data.ledger : [];
    var records = {};
    var order = [];

    events.forEach(function (event) {
      if (!isObject(event) || !hasValue(event.id)) {
        return;
      }
      if (!records[event.id]) {
        records[event.id] = { id: String(event.id), register: null, review: null };
        order.push(String(event.id));
      }
      if (event.ev === "register") {
        records[event.id].register = event;
      } else if (event.ev === "review") {
        records[event.id].review = event;
      }
    });

    return order.map(function (id) { return records[id]; });
  }

  function countOutcomes(records) {
    var hits = records.filter(function (record) { return get(record, ["review", "outcome"], "") === "hit"; }).length;
    var misses = records.filter(function (record) { return get(record, ["review", "outcome"], "") === "miss"; }).length;
    return hits + " hit" + (hits === 1 ? "" : "s") + " / " + misses + " miss" + (misses === 1 ? "" : "es");
  }

  function inspectRecord(record, tile) {
    if (selectedTile) {
      selectedTile.classList.remove("is-selected");
      selectedTile.setAttribute("aria-pressed", "false");
    }
    selectedTile = tile;
    if (selectedTile) {
      selectedTile.classList.add("is-selected");
      selectedTile.setAttribute("aria-pressed", "true");
    }

    var outcome = get(record, ["review", "outcome"], "pending");
    setText("inspector-title", record.id);
    setBadge("inspector-outcome", String(outcome).toUpperCase(), outcome === "hit" ? "success" : (outcome === "miss" ? "danger" : "warning"));
    setText("register-event", record.register ? JSON.stringify(record.register, null, 2) : "Register event unavailable.");
    setText("review-event", record.review ? JSON.stringify(record.review, null, 2) : "Review event unavailable.");
  }

  function createEvidenceTile(record) {
    var tile = document.createElement("button");
    var id = document.createElement("span");
    var meta = document.createElement("span");
    var outcome = get(record, ["review", "outcome"], "pending");
    var phase = get(record, ["register", "payload", "phase"], "unknown");

    tile.type = "button";
    tile.className = "evidence-tile";
    tile.dataset.outcome = outcome;
    tile.setAttribute("aria-label", record.id + ", " + phase + ", " + outcome + ". Show event details.");
    tile.setAttribute("aria-pressed", "false");

    id.className = "tile-id";
    id.textContent = record.id;
    meta.className = "tile-meta";
    meta.textContent = phase + " / " + outcome;
    tile.appendChild(id);
    tile.appendChild(meta);
    tile.addEventListener("click", function () { inspectRecord(record, tile); });
    return tile;
  }

  function renderLedger() {
    var records = pairLedgerEvents();
    var anchorRecords = records.filter(function (record) {
      return get(record, ["register", "kind"], "") === "anchor_task" || record.id.indexOf("anchor-") === 0;
    });
    var agentRecords = records.filter(function (record) {
      return get(record, ["register", "kind"], "") === "agent_task" || record.id.indexOf("agent-") === 0;
    });
    var anchorGrid = byId("anchor-grid");
    var agentGrid = byId("agent-grid");
    var integrity = get(report, ["integrity", "comparison_ledger"], get(report, ["integrity", "ledger"], {}));

    anchorRecords.forEach(function (record) { anchorGrid.appendChild(createEvidenceTile(record)); });
    agentRecords.forEach(function (record) { agentGrid.appendChild(createEvidenceTile(record)); });

    setText("anchor-summary", anchorRecords.length ? countOutcomes(anchorRecords) : "No anchor records");
    setText("agent-summary", agentRecords.length ? countOutcomes(agentRecords) : "No agent records");
    setText("ledger-meta", finiteNumber(integrity.events_verified) ? integrity.events_verified + " events / SHA-256 / append-only" : "Ledger integrity unavailable");

    if (records.length) {
      var firstTile = anchorGrid.querySelector(".evidence-tile") || agentGrid.querySelector(".evidence-tile");
      inspectRecord(records[0], firstTile);
    } else {
      anchorGrid.textContent = "No anchor evidence found.";
      agentGrid.textContent = "No agent evidence found.";
    }
  }

  function renderHealth() {
    var health = get(report, ["health"], {});
    var signals = Array.isArray(health.signals) ? health.signals : [];
    var hitRate = findFirstEvent(signals, function (signal) { return signal.name === "hit_rate"; }) || {};
    var brier = findFirstEvent(signals, function (signal) { return signal.name === "brier"; }) || {};
    var healthy = health.healthy === true;

    setBadge("health-status", healthy ? "HEALTHY" : "DEGRADED", healthy ? "success" : "danger");
    setText("hit-rate-values", percent(hitRate.prior, 0) + " to " + percent(hitRate.recent, 0));
    setText("brier-values", fixed(brier.prior, 3) + " to " + fixed(brier.recent, 3));
    setBar("hit-rate-prior-bar", hitRate.prior);
    setBar("hit-rate-recent-bar", hitRate.recent);
    setBar("brier-prior-bar", brier.prior);
    setBar("brier-recent-bar", brier.recent);
    if (finiteNumber(hitRate.delta) && finiteNumber(brier.delta)) {
      setText(
        "health-note",
        healthy ?
          "Hit rate improved " + percent(Math.abs(hitRate.delta), 0) + " while Brier error fell " + fixed(Math.abs(brier.delta), 3) + ". Health gate passed." :
          "Hit rate fell " + percent(Math.abs(hitRate.delta), 0) + " while Brier error rose " + signed(brier.delta, 3) + ". Rollback threshold triggered."
      );
    } else if (finiteNumber(hitRate.delta)) {
      setText(
        "health-note",
        (get(health, ["scope"], "") === "holdout_control_candidate" ?
          "Holdout control to candidate hit rate changed " : "Hit rate changed ") +
          signed(hitRate.delta * 100, 0) + " pp. Brier is unavailable because the runner did not report probabilities."
      );
    } else {
      setText("health-note", "Health signals unavailable.");
    }
  }

  function renderDrift() {
    var drift = get(report, ["drift"], {});
    var stable = drift.status === "stable";
    var insufficient = drift.status === "insufficient_data" || drift.status === "not_configured";
    setBadge(
      "drift-status",
      stable ? "STABLE" : (insufficient ? "INSUFFICIENT DATA" : (hasValue(drift.status) ? "DRIFT DETECTED" : "UNAVAILABLE")),
      stable ? "success" : (insufficient ? "neutral" : (hasValue(drift.status) ? "warning" : "neutral"))
    );
    setText("anchor-brier", fixed(drift.anchor_brier, 4));
    setText("overall-brier", fixed(drift.overall_brier, 4));
    setText("drift-gap", fixed(drift.gap, 4));
    setText(
      "drift-note",
      drift.detail,
      insufficient ? "Drift calibration requires reported probabilities for both anchor and measured tasks." : "Drift signals unavailable."
    );
    var marker = byId("gap-marker");
    var markerPosition = finiteNumber(drift.gap) ? Math.max(0, Math.min(1, drift.gap / 0.25)) * 100 : 0;
    marker.style.left = "calc(" + markerPosition.toFixed(1) + "% - 1px)";
  }

  function renderReliability() {
    var effect = get(report, ["effect_gate"], {});
    if (finiteNumber(effect.paired_n)) {
      var interval = isObject(effect.confidence_interval) ? effect.confidence_interval : {};
      var orderCounts = get(report, ["comparison", "measured_order_counts"], {});
      setText("effect-title", "Paired effect");
      setText("effect-primary-label", "Observed lift");
      setText("effect-secondary-label", "Minimum lift");
      setText("effect-stat-one-label", "Test");
      setText("effect-stat-two-label", "Paired tasks");
      setText("effect-stat-three-label", "Exact p-value");
      setBadge("reliability-status", effect.passed === true ? "VERIFIED" : "UNPROVEN", effect.passed === true ? "success" : "danger");
      setText("reliability-pred", finiteNumber(effect.actual_delta) ? signed(effect.actual_delta * 100, 0) + " pp" : "--");
      setText("reliability-actual", finiteNumber(effect.minimum_absolute_delta) ? signed(effect.minimum_absolute_delta * 100, 0) + " pp" : "--");
      byId("reliability-pred-bar").className = "bar success";
      byId("reliability-actual-bar").className = "bar threshold";
      setBar("reliability-pred-bar", Math.max(0, effect.actual_delta));
      setBar("reliability-actual-bar", Math.max(0, effect.minimum_absolute_delta));
      setText("reliability-bucket", "Exact paired");
      setText("reliability-samples", effect.paired_n);
      setText("agent-brier", pValue(effect.exact_p_value));
      if (finiteNumber(interval.lower) && finiteNumber(interval.upper)) {
        var orderSummary = finiteNumber(orderCounts.control_first) && finiteNumber(orderCounts.candidate_first) ?
          orderCounts.control_first + "/" + orderCounts.candidate_first + " control/candidate-first. " : "";
        setText(
          "reliability-note",
          orderSummary + effect.improvements + " improved, " + effect.regressions + " regressed. " +
            percent(interval.confidence, 0) + " paired bootstrap CI: " +
            signed(interval.lower * 100, 0) + " to " + signed(interval.upper * 100, 0) + " pp."
        );
      } else {
        setText("reliability-note", "Paired effect interval unavailable.");
      }
      return;
    }

    var reliability = get(report, ["metrics", "agent_reliability"], []);
    var bucket = Array.isArray(reliability) && reliability.length ? reliability[0] : {};
    var agentBrier = get(report, ["metrics", "agent_brier", "brier"], null);
    var gap = finiteNumber(bucket.mean_pred) && finiteNumber(bucket.actual_rate) ? bucket.mean_pred - bucket.actual_rate : null;
    var calibrated = finiteNumber(gap) && Math.abs(gap) <= 0.1;

    setBadge("reliability-status", calibrated ? "CALIBRATED" : "MISCALIBRATED", calibrated ? "success" : "danger");
    setText("reliability-pred", percent(bucket.mean_pred, 1));
    setText("reliability-actual", percent(bucket.actual_rate, 1));
    setBar("reliability-pred-bar", bucket.mean_pred);
    setBar("reliability-actual-bar", bucket.actual_rate);
    setText("reliability-bucket", bucket.bucket, "--");
    setText("reliability-samples", bucket.n, "--");
    setText("agent-brier", fixed(agentBrier, 4));
    if (finiteNumber(gap)) {
      setText(
        "reliability-note",
        calibrated ?
          "Prediction and observed success are within 10 percentage points. Calibration gate passed." :
          (gap > 0 ? "Confidence exceeded observed success by " : "Observed success exceeded confidence by ") +
            percent(Math.abs(gap), 1) + "."
      );
    } else {
      setText("reliability-note", "Reliability data unavailable.");
    }
  }

  function renderDecision() {
    var lessons = get(report, ["lessons"], {});
    var restored = get(report, ["decision", "restored_config"], null);
    var action = get(report, ["decision", "action"], "");
    var kept = action === "keep_change";
    var distilled = Array.isArray(lessons.distilled) ? lessons.distilled.length : null;

    setText("decision-title", kept ? "NO NEW LESSON" : "BLOCKED");
    setText("gate-icon", kept ? "-" : "X");
    setText("lesson-gate-reason", hasValue(lessons.gate) ? humanize(lessons.gate) + ". No unsupported lesson enters the feedback loop." : "Lesson gate data unavailable.");
    setText("minimum-samples", lessons.minimum_samples, "--");
    setText("minimum-confidence", percent(lessons.minimum_confidence, 0), "--");
    setText("lessons-distilled", distilled, "--");
    setText("config-eyebrow", kept ? "VERIFIED OUTPUT" : "ROLLBACK OUTPUT");
    setText("config-title", kept ? "Retained change" : "Restored config");
    setBadge("config-status", kept ? "KEPT" : "RESTORED", "success");
    setText("restored-config", JSON.stringify(restored || get(data, ["proposal", "change"], {}), null, 2));
    byId("lesson-panel").classList.toggle("is-success", kept);
    setText(
      "diagnostics-summary",
      kept && get(report, ["drift", "recommend"], null) ?
        "Paired effect supports the config change; drift keeps lesson injection paused." :
      kept ? "Health, paired effect, and integrity support the config change." :
        "Health, drift, and calibration independently reject the change."
    );
  }

  function renderArtifactLinks() {
    var comparisonLink = byId("comparison-ledger-link");
    var controlLink = byId("control-outputs-link");
    if (comparisonLink && hasValue(get(report, ["artifacts", "comparison_ledger"], null))) {
      comparisonLink.hidden = false;
    }
    if (controlLink && hasValue(get(report, ["artifacts", "control_outputs"], null))) {
      controlLink.hidden = false;
    }
  }

  function fallbackCopy(text) {
    var textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    var copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (error) {
      copied = false;
    }
    document.body.removeChild(textarea);
    return copied;
  }

  function setupCopyButton() {
    var button = byId("copy-command");
    if (!button) {
      return;
    }
    button.addEventListener("click", function () {
      var copiedSynchronously = fallbackCopy(activeCommand);
      var copyPromise = copiedSynchronously ?
        Promise.resolve(true) :
        (navigator.clipboard && navigator.clipboard.writeText ?
          navigator.clipboard.writeText(activeCommand).then(function () { return true; }).catch(function () { return false; }) :
          Promise.resolve(false));

      copyPromise.then(function (copied) {
        button.textContent = copied ? "Command copied" : "Copy failed";
        window.setTimeout(function () { button.textContent = "Copy command"; }, 1600);
      });
    });
  }

  function render() {
    setText("command-display", activeCommand);
    setupCopyButton();

    if (!isObject(data)) {
      showDataError("Expected dashboard-data.js to define window.DRIFTGUARD_DATA.");
      return;
    }
    if (!isObject(data.report)) {
      showDataError("window.DRIFTGUARD_DATA.report is missing or invalid. Empty states are shown below.");
    }

    try {
      renderVerdict();
      renderPrediction();
      renderTimeline();
      renderLedger();
      renderHealth();
      renderDrift();
      renderReliability();
      renderDecision();
      renderArtifactLinks();
    } catch (error) {
      showDataError("The dashboard artifact could not be rendered: " + error.message);
    }
  }

  render();
}());
