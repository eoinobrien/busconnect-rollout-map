// Pure visibility decisions for the map viewer. Exported as a CommonJS
// module so node-based tests can drive them without spinning up a
// browser or Leaflet. The viewer in index.html imports the same logic
// via a small inline shim so the rules can't drift between rendered
// behaviour and tests.

'use strict';

function isRouteReplacedFor(routeId, phaseId, state) {
  // True if `routeId` is in the `replacing` array of some active
  // future phase OTHER than `phaseId` itself. A feature whose own
  // phase is among the replacing phases is the brand new version of
  // the route and must not be hidden.
  const replacingPhases = state.replacingByPhase.get(routeId);
  if (!replacingPhases) return false;
  if (replacingPhases.has(phaseId)) return false;
  return true;
}

function isLabelHidden(label, state) {
  // Future phase that the user hasn't opted in to → hidden.
  if (state.futurePhases.has(label.phase)
      && !state.visibleFuturePhases.has(label.phase)) {
    return true;
  }
  // Category toggle off → hidden.
  if (label.category && !state.visibleCategories.has(label.category)) {
    return true;
  }
  return false;
}

function isPillVisible(routeId, label, state) {
  // A pill is hidden when its route has been replaced by an active
  // future phase that the label doesn't itself belong to.
  if (isRouteReplacedFor(routeId, label.phase, state)) return false;

  // When no highlight is set, every non-replaced pill stays visible.
  if (!state.selectedRoutes || state.selectedRoutes.size === 0) return true;

  // Inside the highlight selection → visible.
  if (state.selectedRoutes.has(routeId)) return true;

  // Network-preview exemption: active future phases stay visible
  // even when a highlight is on, UNLESS the highlight is on a route
  // inside this same phase (then peers dim).
  const isFutureActive = state.futurePhases.has(label.phase)
    && state.visibleFuturePhases.has(label.phase);
  const phaseInHighlight = state.phasesInHighlight.has(label.phase);
  return isFutureActive && !phaseInHighlight;
}

function computeReplacingByPhase(visibleFuturePhases, phasesMeta) {
  const out = new Map();
  for (const pid of visibleFuturePhases) {
    const info = phasesMeta[pid];
    if (!info) continue;
    for (const r of (info.replacing || [])) {
      if (!r) continue;
      if (!out.has(r)) out.set(r, new Set());
      out.get(r).add(pid);
    }
  }
  return out;
}

function computePhasesInHighlight(selectedRoutes, routePhase, futurePhases, visibleFuturePhases) {
  const out = new Set();
  if (!selectedRoutes) return out;
  for (const r of selectedRoutes) {
    const p = routePhase[r];
    if (p && futurePhases.has(p) && visibleFuturePhases.has(p)) {
      out.add(p);
    }
  }
  return out;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    isRouteReplacedFor,
    isLabelHidden,
    isPillVisible,
    computeReplacingByPhase,
    computePhasesInHighlight,
  };
}

if (typeof window !== 'undefined') {
  window.MapVisibility = {
    isRouteReplacedFor,
    isLabelHidden,
    isPillVisible,
    computeReplacingByPhase,
    computePhasesInHighlight,
  };
}
