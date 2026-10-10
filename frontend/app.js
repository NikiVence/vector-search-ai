/* API contract: ../API_CONTRACT.md. No Telegram or backend implementation here. */
const $ = (id) => document.getElementById(id);
const MAX_FILE_SIZE = 10 * 1024 * 1024;
const ACCEPTED_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
const API_STORAGE_KEY = 'frame.apiUrl';
let apiUrl = 'http://localhost:8000';
try { apiUrl = normalizeApiUrl(localStorage.getItem(API_STORAGE_KEY) || apiUrl); } catch { /* Use default if storage is unavailable. */ }
let mode = 'search';
let selectedFile = null;
let previewUrl = null;
let requestController = null;
let busy = false;
let collections = [];
let selectedCollectionId = '';
let collectionController = null;

function renderCollections(preferredId = selectedCollectionId) {
  const select = $('collection-select');
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = collections.length ? 'Выбери коллекцию' : 'Создай первую коллекцию';
  select.replaceChildren(placeholder);
  for (const item of collections) {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = item.name;
    select.append(option);
  }
  const nextId = collections.some(item => item.id === preferredId) ? preferredId : '';
  if (nextId !== selectedCollectionId) { cancelRequest(); emptyResults(); }
  selectedCollectionId = nextId;
  select.value = selectedCollectionId;
  select.disabled = !collections.length;
  setBusy(busy);
}

async function collectionRequest(name) {
  collectionController?.abort();
  const controller = new AbortController();
  collectionController = controller;
  const timeout = setTimeout(() => controller.abort(), 15000);
  $('collection-create').disabled = true;
  $('collections-refresh').disabled = true;
  $('collection-status').textContent = name === undefined ? 'Загружаем коллекции…' : 'Создаём коллекцию…';
  try {
    const options = {signal: controller.signal};
    if (name !== undefined) Object.assign(options, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name}),
    });
    const response = await fetch(`${apiUrl}/api/collections`, options);
    let body;
    try { body = await response.json(); }
    catch { throw new Error(`Ответ без JSON (HTTP ${response.status}).`); }
    if (collectionController !== controller) return;
    if (!response.ok) throw new Error(responseError(body || {}, response.status));
    const items = name === undefined ? body?.collections : [body];
    if (!Array.isArray(items) || items.some(item => !item || typeof item.id !== 'string' || !item.id || typeof item.name !== 'string')) {
      throw new Error('Ожидаются коллекции с полями id и name.');
    }
    if (name === undefined) collections = items;
    else {
      collections = [...collections.filter(item => item.id !== body.id), body];
      $('collection-name').value = '';
    }
    renderCollections(name === undefined ? selectedCollectionId : body.id);
    $('collection-status').textContent = name === undefined
      ? (collections.length ? '' : 'Коллекций пока нет. Введи название и нажми «Создать».')
      : 'Коллекция создана и выбрана.';
  } catch (error) {
    if (collectionController !== controller) return;
    if (name === undefined) { collections = []; renderCollections(); }
    $('collection-status').textContent = controller.signal.aborted
      ? 'Сервер не ответил вовремя. Обнови список перед повторной попыткой.'
      : error instanceof TypeError ? 'Нет связи с сервером. Проверь подключение и нажми «Обновить».' : error.message;
  } finally {
    clearTimeout(timeout);
    if (collectionController === controller) {
      collectionController = null;
      $('collection-create').disabled = false;
      $('collections-refresh').disabled = false;
    }
  }
}

function normalizeApiUrl(value) {
  const url = new URL(value.trim());
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('Укажи HTTP(S)-адрес без пароля, параметров и #.');
  }
  return url.href.replace(/\/+$/, '');
}

function showFeedback(message, type = 'error') {
  $('feedback').textContent = message;
  $('feedback').className = `feedback ${type}`;
  $('feedback').hidden = false;
}

function setBusy(value) {
  busy = value;
  $('submit-button').disabled = value || !selectedFile || !selectedCollectionId;
  $('spinner').hidden = !value;
  $('submit-icon').hidden = value;
  $('results-panel').setAttribute('aria-busy', String(value && mode === 'search'));
  $('submit-label').textContent = value
    ? (mode === 'search' ? 'Ищем совпадения…' : 'Добавляем фото…')
    : (mode === 'search' ? 'Найти похожие' : 'Добавить в базу');
}

function cancelRequest() {
  requestController?.abort();
  requestController = null;
  setBusy(false);
}

function emptyResults(title = 'Здесь появятся совпадения', description = 'Выбери фотографию слева и запусти поиск. Результаты покажем от самых похожих.') {
  $('results-grid').replaceChildren();
  $('results-grid').hidden = true;
  $('empty-state').hidden = false;
  $('empty-title').textContent = title;
  $('empty-description').textContent = description;
  $('result-count').textContent = 'ТОП 3';
}

function switchMode(nextMode) {
  if (mode === nextMode) return;
  cancelRequest();
  mode = nextMode;
  for (const value of ['search', 'upload']) {
    $(`mode-${value}`).classList.toggle('active', mode === value);
    $(`mode-${value}`).setAttribute('aria-pressed', String(mode === value));
  }
  $('panel-title').textContent = mode === 'search' ? 'Начни с фотографии' : 'Пополни коллекцию';
  $('panel-description').textContent = mode === 'search'
    ? 'Загрузи снимок, для которого нужно найти похожие.'
    : 'Добавь фотографию, чтобы находить её в будущем.';
  $('input-note').textContent = mode === 'search'
    ? 'Фото для поиска не добавляется в коллекцию.'
    : 'Фотография будет сохранена в выбранную коллекцию.';
  $('feedback').hidden = true;
  emptyResults();
  setBusy(false);
}

function selectFile(file) {
  if (!file) return;
  if (!ACCEPTED_TYPES.has(file.type)) return showFeedback('Выбери изображение JPG, PNG или WEBP.');
  if (!file.size || file.size > MAX_FILE_SIZE) return showFeedback('Файл должен быть непустым и не больше 10 МБ.');
  cancelRequest();
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  selectedFile = file;
  previewUrl = URL.createObjectURL(file);
  $('preview-image').src = previewUrl;
  $('file-name').textContent = file.name;
  $('file-size').textContent = file.size < 1024 * 1024
    ? `${Math.ceil(file.size / 1024)} КБ`
    : `${(file.size / 1024 / 1024).toFixed(1)} МБ`;
  $('preview').hidden = false;
  $('dropzone').hidden = true;
  $('feedback').hidden = true;
  emptyResults();
  setBusy(false);
}

function clearFile() {
  cancelRequest();
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  selectedFile = null;
  $('preview-image').removeAttribute('src');
  $('file-input').value = '';
  $('preview').hidden = true;
  $('dropzone').hidden = false;
  $('feedback').hidden = true;
  emptyResults();
  setBusy(false);
}

function responseError(body, status) {
  if (typeof body.detail === 'string') return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((item) => item.msg || 'Некорректные данные').join('; ');
  return ({404: 'Эндпоинт не найден. Проверь адрес API и маршруты FastAPI.', 413: 'Сервер отклонил слишком большой файл.', 503: 'Сервис поиска временно недоступен. Попробуй позже.'})[status] || `Сервер вернул ошибку ${status}.`;
}

async function postPhoto(endpoint, file, signal) {
  const form = new FormData();
  form.append('photo', file);
  if (!selectedCollectionId) throw new Error('Выбери коллекцию.');
  // Current backend field contains the collection UUID despite its name.
  form.append('collection_name', selectedCollectionId);
  // The current search handler accepts limit as a query parameter.
  const query = endpoint === '/api/search' ? '?limit=3' : '';
  // Не задаём Content-Type: браузер сам добавит multipart boundary.
  const response = await fetch(`${apiUrl}${endpoint}${query}`, {method: 'POST', body: form, signal});
  let body;
  try { body = await response.json(); }
  catch { throw new Error(`Сервер вернул ответ без JSON (HTTP ${response.status}). Проверь адрес API.`); }
  if (!body || typeof body !== 'object') throw new Error('Сервер вернул некорректный JSON.');
  if (!response.ok) throw new Error(responseError(body, response.status));
  return body;
}

function imageUrl(value) {
  if (typeof value !== 'string' || !value) throw new Error('В результате отсутствует image_url.');
  const url = new URL(value, `${apiUrl}/`);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Сервер вернул некорректный адрес изображения.');
  return url.href;
}

function renderResults(matches) {
  if (!Array.isArray(matches)) throw new Error('В ответе сервера ожидается массив matches.');
  if (!matches.length) {
    emptyResults('Пока нет совпадений', 'Добавь фотографии в базу или попробуй другое изображение.');
    $('result-count').textContent = '0 ФОТО';
    return;
  }
  const fragment = document.createDocumentFragment();
  matches.slice(0, 3).forEach((match, index) => {
    if (!match || typeof match.filename !== 'string' || typeof match.score !== 'number' || !Number.isFinite(match.score)) {
      throw new Error('Результат должен содержать filename, score и image_url.');
    }
    const card = document.createElement('article');
    card.className = 'result-card';
    const link = document.createElement('a');
    link.className = 'result-image-link';
    link.href = imageUrl(match.image_url);
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.setAttribute('aria-label', `Открыть ${match.filename}`);
    const image = document.createElement('img');
    image.src = link.href;
    image.alt = match.filename;
    image.loading = 'lazy';
    image.addEventListener('error', () => {
      link.classList.add('image-failed');
      link.textContent = 'Фото недоступно';
    }, {once: true});
    const rank = document.createElement('span');
    rank.className = 'result-rank';
    rank.textContent = String(index + 1).padStart(2, '0');
    link.append(image, rank);
    const details = document.createElement('div');
    const name = document.createElement('p');
    name.className = 'result-name';
    name.textContent = match.filename;
    name.title = match.filename;
    const score = document.createElement('span');
    score.className = 'result-score';
    score.textContent = `Сходство ${match.score.toFixed(3)}`;
    score.title = 'Cosine similarity — оценка сходства, не вероятность совпадения.';
    details.append(name, score);
    card.append(link, details);
    fragment.append(card);
  });
  $('results-grid').replaceChildren(fragment);
  $('results-grid').hidden = false;
  $('empty-state').hidden = true;
  $('result-count').textContent = `${Math.min(matches.length, 3)} ФОТО`;
}

async function submit() {
  if (!selectedFile || busy) return;
  if (!selectedCollectionId) return showFeedback('Выбери коллекцию.');
  const controller = new AbortController();
  requestController = controller;
  const activeMode = mode;
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 120000);
  $('feedback').hidden = true;
  if (activeMode === 'search') emptyResults('Ищем похожие кадры…', 'Первый запрос может занять больше времени, пока сервер загружает модель.');
  setBusy(true);
  try {
    const body = await postPhoto(activeMode === 'search' ? '/api/search' : '/api/photos', selectedFile, controller.signal);
    if (requestController !== controller) return;
    if (activeMode === 'search') renderResults(body.matches);
    else {
      if (typeof body.id !== 'string' || !body.id) throw new Error('В ответе загрузки ожидается строковый id фотографии.');
      showFeedback('Фото добавлено. Выбери другой файл или перейди к поиску.', 'success');
      emptyResults('Фото сохранено', 'Перейди к поиску, чтобы найти похожие фото в выбранной коллекции.');
    }
  } catch (error) {
    if (requestController !== controller) return;
    if (controller.signal.aborted && !timedOut) return;
    const message = timedOut ? 'Сервер не ответил за 2 минуты. Проверь состояние бэкенда. Если добавлял фото, проверь базу перед повторной загрузкой.'
      : error instanceof TypeError ? 'Не удалось связаться с API. Проверь адрес в «Подключение», запуск FastAPI и настройку CORS.'
      : error.message;
    showFeedback(message);
    if (activeMode === 'search') emptyResults('Поиск не завершён', 'Исправь причину ошибки и попробуй ещё раз.');
  } finally {
    clearTimeout(timeout);
    if (requestController === controller) { requestController = null; setBusy(false); }
  }
}

$('mode-search').addEventListener('click', () => switchMode('search'));
$('mode-upload').addEventListener('click', () => switchMode('upload'));
$('dropzone').addEventListener('click', () => $('file-input').click());
$('file-input').addEventListener('change', (event) => { selectFile(event.target.files[0]); event.target.value = ''; });
$('remove-file').addEventListener('click', clearFile);
$('submit-button').addEventListener('click', submit);
for (const eventName of ['dragenter', 'dragover']) {
  $('dropzone').addEventListener(eventName, (event) => { event.preventDefault(); $('dropzone').classList.add('dragging'); });
}
for (const eventName of ['dragleave', 'drop']) {
  $('dropzone').addEventListener(eventName, (event) => { event.preventDefault(); $('dropzone').classList.remove('dragging'); });
}
$('dropzone').addEventListener('drop', (event) => {
  if (event.dataTransfer.files.length !== 1) return showFeedback('Выбери одну фотографию за раз.');
  selectFile(event.dataTransfer.files[0]);
});
// Предотвращаем переход браузера на файл, если он брошен мимо зоны загрузки.
window.addEventListener('dragover', (event) => event.preventDefault());
window.addEventListener('drop', (event) => event.preventDefault());
$('settings-button').addEventListener('click', () => {
  $('api-url').value = apiUrl;
  $('settings-error').textContent = '';
  $('settings-dialog').showModal();
});
$('close-settings').addEventListener('click', () => $('settings-dialog').close());
$('settings-form').addEventListener('submit', (event) => {
  event.preventDefault();
  try {
    const value = normalizeApiUrl($('api-url').value);
    cancelRequest();
    apiUrl = value;
    collections = [];
    renderCollections('');
    collectionRequest();
    try { localStorage.setItem(API_STORAGE_KEY, value); } catch { /* Session-only settings still work. */ }
    $('settings-dialog').close();
    emptyResults();
    showFeedback('Адрес сохранён. Соединение будет проверено при отправке фото.', 'success');
  } catch (error) { $('settings-error').textContent = error.message; }
});
window.addEventListener('beforeunload', () => { requestController?.abort(); if (previewUrl) URL.revokeObjectURL(previewUrl); });
window.addEventListener('beforeunload', () => collectionController?.abort());
$('collection-select').addEventListener('change', () => {
  cancelRequest();
  selectedCollectionId = $('collection-select').value;
  emptyResults();
  setBusy(false);
});
$('collections-refresh').addEventListener('click', () => collectionRequest());
$('collection-form').addEventListener('submit', event => {
  event.preventDefault();
  if (collectionController) return;
  const name = $('collection-name').value.trim();
  if (!name || Array.from(name).length > 100) {
    $('collection-status').textContent = 'Название должно содержать от 1 до 100 символов.';
    return;
  }
  collectionRequest(name);
});
window.addEventListener('DOMContentLoaded', () => collectionRequest());
