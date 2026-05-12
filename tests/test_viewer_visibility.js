'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const V = require('../viewer/visibility.js');

function baseState(over) {
  return Object.assign({
    futurePhases: new Set(),
    visibleFuturePhases: new Set(),
    visibleCategories: new Set(['spine', 'orbital', 'local', 'peak', 'radial']),
    selectedRoutes: null,
    phasesInHighlight: new Set(),
    replacingByPhase: new Map(),
  }, over);
}

test('label in inactive future phase is hidden', () => {
  const state = baseState({
    futurePhases: new Set(['10']),
    visibleFuturePhases: new Set(),
  });
  assert.equal(V.isLabelHidden({ phase: '10', category: 'spine' }, state), true);
});

test('label in active future phase is visible at the label level', () => {
  const state = baseState({
    futurePhases: new Set(['10']),
    visibleFuturePhases: new Set(['10']),
  });
  assert.equal(V.isLabelHidden({ phase: '10', category: 'spine' }, state), false);
});

test('label in a category the user toggled off is hidden', () => {
  const state = baseState({
    visibleCategories: new Set(['orbital', 'local', 'peak', 'radial']),
  });
  assert.equal(V.isLabelHidden({ phase: 'legacy', category: 'spine' }, state), true);
});

test('legacy label with all categories visible is not hidden', () => {
  const state = baseState();
  assert.equal(V.isLabelHidden({ phase: 'legacy', category: 'spine' }, state), false);
});

test('default state: every pill visible', () => {
  const state = baseState();
  assert.equal(V.isPillVisible('39', { phase: 'legacy' }, state), true);
});

test('pill replaced by an active future phase hides', () => {
  const state = baseState({
    futurePhases: new Set(['10']),
    visibleFuturePhases: new Set(['10']),
    replacingByPhase: V.computeReplacingByPhase(
      new Set(['10']),
      { '10': { date: 'unknown', replacing: ['80'] } },
    ),
  });
  assert.equal(V.isPillVisible('80', { phase: '7' }, state), false);
});

test('new version of a replaced route stays visible from its own phase', () => {
  // Route 80 is in phase 10's replacing list AND in phase 10's routes
  // list - the new 80 (phase=10) must keep its pill visible even
  // though the old 80 (phase=7) hides.
  const state = baseState({
    futurePhases: new Set(['10']),
    visibleFuturePhases: new Set(['10']),
    replacingByPhase: V.computeReplacingByPhase(
      new Set(['10']),
      { '10': { date: 'unknown', replacing: ['80'] } },
    ),
  });
  assert.equal(V.isPillVisible('80', { phase: '10' }, state), true);
});

test('highlight on one route dims others', () => {
  const state = baseState({
    selectedRoutes: new Set(['A1']),
  });
  assert.equal(V.isPillVisible('A2', { phase: 'legacy' }, state), false);
  assert.equal(V.isPillVisible('A1', { phase: 'legacy' }, state), true);
});

test('network-preview exemption: legacy highlight + active future phase keeps future visible', () => {
  // User highlights legacy routes via the panel; activating a future
  // phase shouldn't dim its routes just because they aren't in the
  // selection.
  const state = baseState({
    selectedRoutes: new Set(['39', '13']),
    futurePhases: new Set(['10']),
    visibleFuturePhases: new Set(['10']),
    phasesInHighlight: new Set(),  // legacy highlight, no future phases
  });
  assert.equal(V.isPillVisible('A1', { phase: '10' }, state), true);
});

test('drilling into an active future phase dims its peers', () => {
  // Activate phase 10, then click A1 inside it -> A1 stays
  // highlighted, A2/A3/A4 fall to dim.
  const state = baseState({
    selectedRoutes: new Set(['A1']),
    futurePhases: new Set(['10']),
    visibleFuturePhases: new Set(['10']),
    phasesInHighlight: new Set(['10']),
  });
  assert.equal(V.isPillVisible('A1', { phase: '10' }, state), true);
  assert.equal(V.isPillVisible('A2', { phase: '10' }, state), false);
});

test('shared corridor: hide pills for replaced routes, keep the rest', () => {
  // Label carries routes [82, 65]; phase 10 replaces 65. The 65 pill
  // should hide while 82 stays visible inside the same label.
  const state = baseState({
    futurePhases: new Set(['10']),
    visibleFuturePhases: new Set(['10']),
    replacingByPhase: V.computeReplacingByPhase(
      new Set(['10']),
      { '10': { date: 'unknown', replacing: ['65'] } },
    ),
  });
  const label = { phase: '7' };
  assert.equal(V.isPillVisible('82', label, state), true);
  assert.equal(V.isPillVisible('65', label, state), false);
});

test('computeReplacingByPhase ignores empty strings and missing phases', () => {
  const map = V.computeReplacingByPhase(
    new Set(['10', 'nonexistent']),
    {
      '10': { replacing: ['80', '', null, 'L1'] },
    },
  );
  assert.deepEqual([...map.keys()].sort(), ['80', 'L1']);
});

test('computePhasesInHighlight maps selected routes to their active future phases', () => {
  const selected = new Set(['A1', '39']);
  const routePhase = { 'A1': '10', '39': 'legacy' };
  const future = new Set(['10']);
  const visible = new Set(['10']);
  const out = V.computePhasesInHighlight(selected, routePhase, future, visible);
  assert.deepEqual([...out], ['10']);
});

test('computePhasesInHighlight ignores inactive future phases', () => {
  const selected = new Set(['A1']);
  const routePhase = { 'A1': '10' };
  const out = V.computePhasesInHighlight(
    selected, routePhase, new Set(['10']), new Set()
  );
  assert.equal(out.size, 0);
});
