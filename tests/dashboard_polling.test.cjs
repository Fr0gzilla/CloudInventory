const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../app/static/dashboard.js'), 'utf8');

function dashboard(statuses, launch = { status: 202, body: { id: 19, status_url: '/runs/19/status' } }) {
  const elements = new Map();
  function element(tag = 'div') {
    return {
      tag, textContent: '', hidden: true, disabled: false, value: 0, children: [],
      append(...children) { this.children.push(...children); },
      addEventListener(event, callback) { this[event] = callback; },
      querySelector() { return button; },
      reportValidity() { return true; },
    };
  }
  const button = element('button');
  for (const id of ['dashboard-message', 'dashboard-run-message', 'dashboard-run-form',
    'dashboard-run-progress', 'dashboard-run-bar', 'dashboard-run-step',
    'dashboard-run-result', 'dashboard-run-steps']) elements.set(id, element());
  elements.get('dashboard-run-form').action = '/dashboard/run';
  const requests = [];
  const fetch = async (url) => {
    requests.push(url);
    if (url === '/dashboard/data') return {
      ok: true, json: async () => ({ stats: { matches: {}, anomalies: {}, run: null }, runs: [] }),
    };
    const response = url === '/dashboard/run' ? launch : statuses.shift();
    assert.ok(response, `unexpected request: ${url}`);
    return { ok: response.status >= 200 && response.status < 300, status: response.status,
      json: async () => response.body };
  };
  vm.runInNewContext(script, {
    document: { getElementById: id => elements.get(id) || element('canvas'), createElement: element },
    fetch, Chart: class { destroy() {} }, FormData: class {},
    setTimeout: callback => callback(),
  });
  return {
    get: id => elements.get(`dashboard-run-${id}`), requests,
    submit: () => elements.get('dashboard-run-form').submit({ preventDefault() {} }),
    button,
  };
}

test('failed polling hides technical error and shows failed step in result', async () => {
  const ui = dashboard([{ status: 200, body: { status: 'FAIL', completed_steps: 3,
    current_step: 4, error_message: 'internal database secret', redirect_url: null } }]);
  await ui.submit();
  assert.equal(ui.get('result').textContent, 'Inventaire échoué à l’étape 4 : Correspondance.');
  assert.equal(ui.get('result').textContent.includes('internal database secret'), false);
  assert.equal(ui.get('bar').value, 3);
  assert.equal(ui.button.disabled, false);
});

test('conflict on launch displays conflict in run message without polling', async () => {
  const ui = dashboard([], { status: 409, body: { error: 'technical details' } });
  await ui.submit();
  assert.equal(ui.get('message').textContent, 'Un inventaire est déjà en cours.');
  assert.deepEqual(ui.requests.filter(url => url.includes('/status')), []);
  assert.equal(ui.button.disabled, false);
});

test('unknown status displays an explicit error in run message', async () => {
  const ui = dashboard([{ status: 200, body: { status: 'UNKNOWN', completed_steps: 2,
    current_step: 3 } }]);
  await ui.submit();
  assert.equal(ui.get('message').textContent, 'Statut de l’inventaire inconnu.');
  assert.equal(ui.get('bar').value, 2);
  assert.equal(ui.button.disabled, false);
});

test('old run with missing progress fields completes without invalid step', async () => {
  const ui = dashboard([{ status: 200, body: { status: 'SUCCESS', completed_steps: null,
    current_step: null, redirect_url: '/runs/19' } }]);
  await ui.submit();
  assert.equal(ui.get('step').textContent, '0 étape(s) sur 7 terminée(s). Dernière étape : Collecte Proxmox.');
  assert.equal(ui.get('result').textContent, 'Inventaire terminé avec succès.');
  assert.equal(ui.get('result').children[1].href, '/runs/19');
  assert.equal(ui.get('bar').value, 0);
});
