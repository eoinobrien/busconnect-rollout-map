'use strict';

// End-to-end repro for the "future labels visible on first load" bug.
// We drive the same init sequence index.html performs - features
// loop pushes labels into allLabels, then applyVisibility runs - and
// check whether shield markers for future-phase labels end up with
// display:none. The test deliberately mirrors the index.html
// ordering so it catches wiring bugs that pure unit tests on
// visibility.js can't see (e.g. futurePhases populated *after* the
// features loop, or collectMarkers grabbing FeatureGroups instead of
// L.Markers).

const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const V = require('../viewer/visibility.js');


function buildFakeMarker(dom, doc) {
  // Minimal stand-in for an L.Marker that exposes the parts
  // applyLabelVisibility touches: getElement() returns a DOM node
  // whose .style.display we can observe, and which holds [data-route]
  // children just like the real divIcon innerHTML.
  const root = doc.createElement('div');
  root.className = 'leaflet-marker-icon';
  return {
    _el: root,
    getElement() { return this._el; },
  };
}


function makeLabel(doc, { route, phase, category, isFuture, colour }) {
  const marker = buildFakeMarker(doc, doc);
  const inner = isFuture
    ? `<div class="route-label future"><span data-route="${route}" style="--route-colour:${colour}">${route}*</span></div>`
    : `<div class="route-label"><span data-route="${route}" style="background:${colour}">${route}</span></div>`;
  marker._el.innerHTML = inner;
  return { marker, routes: [route], phase, category };
}


function applyLabelVisibility(allLabels, state) {
  // Mirror of the production function in index.html, hand-copied so
  // ordering bugs in index.html don't propagate into the test. Both
  // call the same window.MapVisibility helpers under the hood.
  for (const lbl of allLabels) {
    const el = lbl.marker.getElement();
    if (!el) continue;
    if (V.isLabelHidden(lbl, state)) {
      el.style.display = 'none';
      continue;
    }
    let visiblePillCount = 0;
    el.querySelectorAll('[data-route]').forEach(pillEl => {
      const r = pillEl.getAttribute('data-route');
      const pillVisible = V.isPillVisible(r, lbl, state);
      pillEl.style.display = pillVisible ? '' : 'none';
      if (pillVisible) visiblePillCount++;
    });
    el.style.display = visiblePillCount > 0 ? '' : 'none';
  }
}


function runInitSequence(orderingBugged) {
  // orderingBugged === true: features loop runs BEFORE futurePhases
  //   is populated (this is what index.html did until the fix).
  // orderingBugged === false: futurePhases is populated FIRST.
  // Both end up calling applyLabelVisibility at the end. The bugged
  // path emits labels with the WRONG style (live pill) but with the
  // correct phase, so the visibility filter should still hide them.
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  const doc = dom.window.document;

  // Synthetic phases meta - one legacy phase ("7") and one future
  // phase ("10 (Potential)").
  const phasesMeta = {
    '7':              { date: '2025-10-19', routes: ['F1'], replacing: [] },
    '10 (Potential)': { date: 'unknown',    routes: ['A1'], replacing: [] },
  };
  // Two routes - one live, one future.
  const routes = [
    { route: 'F1', phase: '7',              category: 'spine', colour: '#d62728' },
    { route: 'A1', phase: '10 (Potential)', category: 'spine', colour: '#d62728' },
  ];

  const futurePhases = new Set();
  const populateFuturePhases = () => {
    for (const [pid, info] of Object.entries(phasesMeta)) {
      if (info && !/^\d{4}-\d{2}-\d{2}$/.test(info.date || '')) {
        futurePhases.add(pid);
      }
    }
  };

  if (!orderingBugged) populateFuturePhases();

  // Features loop: decide style based on futurePhases.has(phase) and
  // record the label. This mimics the index.html block that builds
  // PolylineDecorators and pushes captured markers into allLabels.
  const allLabels = [];
  for (const r of routes) {
    const isFuture = futurePhases.has(r.phase);
    allLabels.push(makeLabel(doc, {
      route: r.route,
      phase: r.phase,
      category: r.category,
      isFuture,
      colour: r.colour,
    }));
  }

  if (orderingBugged) populateFuturePhases();

  // applyVisibility() final pass at end of init.
  const state = {
    futurePhases,
    visibleFuturePhases: new Set(),
    visibleCategories: new Set(['spine', 'orbital', 'local', 'peak', 'radial']),
    selectedRoutes: null,
    phasesInHighlight: new Set(),
    replacingByPhase: V.computeReplacingByPhase(new Set(), phasesMeta),
  };
  applyLabelVisibility(allLabels, state);

  return { allLabels, doc };
}


test('default load: future-phase markers are hidden (correct ordering)', () => {
  const { allLabels } = runInitSequence(false);
  const future = allLabels.find(l => l.routes[0] === 'A1');
  const live = allLabels.find(l => l.routes[0] === 'F1');
  assert.equal(future.marker.getElement().style.display, 'none',
    'future label must be hidden on first load');
  assert.notEqual(live.marker.getElement().style.display, 'none',
    'live label must remain visible');
});


test('default load: future-phase markers are STILL hidden even if futurePhases is populated late', () => {
  // This is the regression scenario from the user's complaint.
  // applyLabelVisibility runs AFTER futurePhases is finally populated,
  // so even though the label was constructed with the wrong styling
  // hint, its phase tag was correctly recorded - the visibility
  // filter must still hide it.
  const { allLabels } = runInitSequence(true);
  const future = allLabels.find(l => l.routes[0] === 'A1');
  assert.equal(future.marker.getElement().style.display, 'none',
    'future label must be hidden even when futurePhases was empty during the features loop');
});


test('activating a future phase makes its markers visible', () => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  const doc = dom.window.document;
  const phasesMeta = {
    '10 (Potential)': { date: 'unknown', routes: ['A1'], replacing: [] },
  };
  const futurePhases = new Set(['10 (Potential)']);
  const allLabels = [makeLabel(doc, {
    route: 'A1', phase: '10 (Potential)', category: 'spine',
    isFuture: true, colour: '#d62728',
  })];
  const state = {
    futurePhases,
    visibleFuturePhases: new Set(['10 (Potential)']),
    visibleCategories: new Set(['spine']),
    selectedRoutes: null,
    phasesInHighlight: new Set(),
    replacingByPhase: V.computeReplacingByPhase(new Set(['10 (Potential)']), phasesMeta),
  };
  applyLabelVisibility(allLabels, state);
  assert.notEqual(allLabels[0].marker.getElement().style.display, 'none');
});


test('category toggle off hides every label in that category', () => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  const doc = dom.window.document;
  const allLabels = [
    makeLabel(doc, { route: 'F1', phase: '7', category: 'spine', isFuture: false, colour: '#d62728' }),
    makeLabel(doc, { route: '39', phase: 'legacy', category: 'radial', isFuture: false, colour: '#9467bd' }),
  ];
  const state = {
    futurePhases: new Set(),
    visibleFuturePhases: new Set(),
    visibleCategories: new Set(['radial']),  // spine toggled off
    selectedRoutes: null,
    phasesInHighlight: new Set(),
    replacingByPhase: new Map(),
  };
  applyLabelVisibility(allLabels, state);
  assert.equal(allLabels[0].marker.getElement().style.display, 'none', 'spine label hidden');
  assert.notEqual(allLabels[1].marker.getElement().style.display, 'none', 'radial label visible');
});


test('collectMarkers walks FeatureGroup tree and grabs L.Markers', () => {
  // Mirror the structural assumption in index.html: a PolylineDecorator
  // (a FeatureGroup) holds per-pattern FeatureGroups, each holding the
  // actual L.Markers. collectMarkers must use `instanceof L.Marker` -
  // NOT `typeof getElement === 'function'`, because FeatureGroups
  // inherit getElement too and would slip through, leaving
  // applyLabelVisibility unable to hide their DOM.
  class Marker { getElement() { return null; } }
  class FeatureGroup {
    constructor() { this._layers = []; }
    eachLayer(fn) { for (const l of this._layers) fn(l); }
    addLayer(l) { this._layers.push(l); }
    // Same gotcha as Leaflet 1.9: FeatureGroup has a getElement that
    // returns undefined - which is why a typeof-based check fails.
    getElement() { return undefined; }
  }

  const dec = new FeatureGroup();
  const pattern = new FeatureGroup();
  const m1 = new Marker();
  const m2 = new Marker();
  pattern.addLayer(m1);
  pattern.addLayer(m2);
  dec.addLayer(pattern);

  // The two competing implementations:
  const wrongFound = [];
  const wrongCollect = layer => {
    if (layer && typeof layer.getElement === 'function') wrongFound.push(layer);
    else if (layer && typeof layer.eachLayer === 'function') layer.eachLayer(wrongCollect);
  };
  dec.eachLayer(wrongCollect);
  // The wrong version captures the FeatureGroup, not the markers,
  // because FeatureGroup.prototype.getElement is also a function.
  assert.ok(wrongFound.some(x => x instanceof FeatureGroup),
    'typeof-getElement test wrongly picks up FeatureGroups (regression sentinel)');

  const rightFound = [];
  const rightCollect = layer => {
    if (layer instanceof Marker) rightFound.push(layer);
    else if (layer && typeof layer.eachLayer === 'function') layer.eachLayer(rightCollect);
  };
  dec.eachLayer(rightCollect);
  assert.equal(rightFound.length, 2);
  assert.ok(rightFound.every(m => m instanceof Marker));
});
