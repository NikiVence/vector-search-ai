const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Минимальное DOM-окружение для проверки fetch и состояний без браузера.
class Element {
  constructor() {
    this.hidden = false;
    this.textContent = '';
    this.children = [];
    this.events = {};
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  addEventListener(name, handler) { this.events[name] = handler; }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  showModal() {}
  close() {}
}

function setup(fetch) {
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {getElementById: get, createElement: () => new Element(), createDocumentFragment: () => new Element()},
    window: {addEventListener() {}},
    localStorage: {getItem: () => null, setItem() {}},
    URL, FormData, AbortController, setTimeout, clearTimeout, fetch,
    console,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8'), context);
  return {context, get};
}

function photo() { return new File(['image-bytes'], 'dog.jpg', {type: 'image/jpeg'}); }

test('collections load and creation sends JSON and selects returned UUID', async () => {
  const requests = [];
  const {context, get} = setup(async (url, options) => {
    requests.push({url, ...options});
    return {ok: true, json: async () => options.method === 'POST'
      ? {id: 'new-id', name: '<Cats>'}
      : {collections: [{id: 'old-id', name: 'Dogs'}]}};
  });
  await context.collectionRequest();
  assert.equal(get('collection-select').children[1].textContent, 'Dogs');
  await context.collectionRequest('<Cats>');
  assert.equal(requests[1].body, JSON.stringify({name: '<Cats>'}));
  assert.equal(requests[1].headers['Content-Type'], 'application/json');
  assert.equal(get('collection-select').value, 'new-id');
  assert.equal(get('collection-select').children[2].textContent, '<Cats>');
});

test('empty collections prevent upload and duplicate errors preserve input', async () => {
  const {context, get} = setup(async (url, options) => options.method === 'POST'
    ? {ok: false, status: 409, json: async () => ({detail: 'Already exists'})}
    : {ok: true, json: async () => ({collections: []})});
  await context.collectionRequest();
  assert.equal(get('collection-select').disabled, true);
  await assert.rejects(context.postPhoto('/api/photos', photo()), /Выбери коллекцию/);
  await assert.rejects(context.postPhoto('/api/search', photo()), /Выбери коллекцию/);
  get('collection-name').value = 'Cats';
  await context.collectionRequest('Cats');
  assert.equal(get('collection-name').value, 'Cats');
  assert.equal(get('collection-status').textContent, 'Already exists');
  assert.equal(get('collection-create').disabled, false);
});

test('an older collections response cannot replace a newer one', async () => {
  let resolveOld;
  let count = 0;
  const {context, get} = setup(() => ++count === 1
    ? new Promise(resolve => { resolveOld = resolve; })
    : Promise.resolve({ok: true, json: async () => ({collections: [{id: 'new', name: 'New'}]})}));
  const pending = context.collectionRequest();
  await context.collectionRequest();
  resolveOld({ok: true, json: async () => ({collections: [{id: 'old', name: 'Old'}]})});
  await pending;
  assert.equal(get('collection-select').children[1].textContent, 'New');
});

test('search sends selected UUID and photo with query limit=3', async () => {
  let request;
  const {context} = setup(async (url, options) => {
    request = {url, ...options};
    return {ok: true, json: async () => ({matches: []})};
  });
  vm.runInContext('selectedCollectionId = "search-collection"', context);
  const result = await context.postPhoto('/api/search', photo(), new AbortController().signal);
  assert.equal(request.url, 'http://localhost:8000/api/search?limit=3');
  assert.equal(request.method, 'POST');
  assert.equal(request.body.get('limit'), null);
  assert.equal(request.body.get('collection_name'), 'search-collection');
  assert.equal(request.body.get('photo').name, 'dog.jpg');
  assert.equal(request.headers, undefined);
  assert.equal(result.matches.length, 0);
});

test('upload fetch sends the same file but no search limit', async () => {
  let request;
  const {context} = setup(async (url, options) => {
    request = {url, ...options};
    return {ok: true, json: async () => ({id: 'photo-1'})};
  });
  vm.runInContext('selectedCollectionId = '+JSON.stringify('collection-1'), context);
  await context.postPhoto('/api/photos', photo(), new AbortController().signal);
  assert.equal(request.body.get('collection_name'), 'collection-1');
  assert.equal(request.url, 'http://localhost:8000/api/photos');
  assert.equal(request.body.get('limit'), null);
});

test('FastAPI validation detail is displayed as readable error', async () => {
  const {context} = setup(async () => ({ok: false, status: 422, json: async () => ({detail: [{msg: 'Invalid image'}]})}));
  vm.runInContext('selectedCollectionId = '+JSON.stringify('collection-1'), context);
  await assert.rejects(context.postPhoto('/api/photos', photo()), /Invalid image/);
});

test('non-JSON response reports status instead of crashing the interface', async () => {
  const {context} = setup(async () => ({ok: false, status: 502, json: async () => {throw new Error('HTML');}}));
  vm.runInContext('selectedCollectionId = "search-collection"', context);
  await assert.rejects(context.postPhoto('/api/search', photo()), /502/);
});

test('rendering limits results to 3 and uses textContent for filenames', () => {
  const {context, get} = setup();
  const matches = Array.from({length: 5}, (_, i) => ({filename: '<img onerror=alert(1)>', score: .9 - i * .1, image_url: `/api/photos/${i}/image`}));
  context.renderResults(matches);
  const cards = get('results-grid').children[0].children;
  assert.equal(cards.length, 3);
  assert.equal(cards[0].children[0].href, 'http://localhost:8000/api/photos/0/image');
  assert.equal(cards[0].children[1].children[0].textContent, '<img onerror=alert(1)>');
  assert.equal(get('empty-state').hidden, true);
  assert.throws(() => context.imageUrl('javascript:alert(1)'));
});

test('empty results clear old cards', () => {
  const {context, get} = setup();
  context.renderResults([{filename: 'dog', score: 1, image_url: '/dog.jpg'}]);
  context.renderResults([]);
  assert.equal(get('results-grid').hidden, true);
  assert.equal(get('results-grid').children.length, 0);
  assert.equal(get('empty-state').hidden, false);
});

test('switching mode prevents an old request from replacing the current view', async () => {
  let resolveRequest;
  let signal;
  const {context, get} = setup((url, options) => {
    signal = options.signal;
    return new Promise((resolve) => {resolveRequest = resolve;});
  });
  context.selectFile(photo());
  vm.runInContext('selectedCollectionId = "search-collection"', context);
  const pending = context.submit();
  context.switchMode('upload');
  assert.equal(signal.aborted, true);
  resolveRequest({ok: true, json: async () => ({matches: [{filename: 'old', score: 1, image_url: '/old.jpg'}]})});
  await pending;
  assert.equal(get('results-grid').hidden, true);
  assert.equal(get('submit-button').disabled, false);
  context.clearFile();
});

test('invalid files never enable submission or issue a request', () => {
  const {context, get} = setup(() => {throw new Error('Unexpected request');});
  context.clearFile();
  context.selectFile({name: 'large.jpg', type: 'image/jpeg', size: 11 * 1024 * 1024});
  assert.equal(get('feedback').hidden, false);
  assert.equal(get('submit-button').disabled, true);
  context.selectFile({name: 'file.txt', type: 'text/plain', size: 100});
  assert.equal(get('submit-button').disabled, true);
});

