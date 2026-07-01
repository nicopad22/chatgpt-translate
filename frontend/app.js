// ===== Config =====
const API_URL = 'https://translate-api-351640193452.us-central1.run.app'; // Set after first Cloud Run deploy

// ===== State =====
var token = localStorage.getItem('token') || '';
var username = localStorage.getItem('username') || '';
var selectedFiles = [];
var currentJobId = null;
var pollInterval = null;

// ===== DOM refs =====
var loginScreen = document.getElementById('login-screen');
var uploadScreen = document.getElementById('upload-screen');
var resultsScreen = document.getElementById('results-screen');

var loginForm = document.getElementById('login-form');
var usernameInput = document.getElementById('username-input');
var passwordInput = document.getElementById('password-input');
var loginBtn = document.getElementById('login-btn');
var loginError = document.getElementById('login-error');

var headerUsername = document.getElementById('header-username');
var headerUsername2 = document.getElementById('header-username-2');
var logoutBtn = document.getElementById('logout-btn');
var logoutBtn2 = document.getElementById('logout-btn-2');

var dropZone = document.getElementById('drop-zone');
var fileInput = document.getElementById('file-input');
var fileList = document.getElementById('file-list');
var languageSelect = document.getElementById('language-select');
var translateBtn = document.getElementById('translate-btn');
var uploadError = document.getElementById('upload-error');

var progressSection = document.getElementById('progress-section');
var currentFileEl = document.getElementById('current-file');
var progressText = document.getElementById('progress-text');
var resultsSection = document.getElementById('results-section');
var downloadList = document.getElementById('download-list');
var statsBox = document.getElementById('stats-box');
var newTranslationBtn = document.getElementById('new-translation-btn');

// ===== Screen Management =====
function showScreen(screenId) {
  loginScreen.hidden = true;
  uploadScreen.hidden = true;
  resultsScreen.hidden = true;
  document.getElementById(screenId).hidden = false;

  if (screenId === 'upload-screen' || screenId === 'results-screen') {
    headerUsername.textContent = username;
    headerUsername2.textContent = username;
  }
}

// ===== Auth =====
function getAuthHeaders() {
  return { 'Authorization': 'Bearer ' + token };
}

async function login() {
  loginError.hidden = true;
  var user = usernameInput.value.trim();
  var pass = passwordInput.value;

  if (!user || !pass) {
    showError(loginError, 'Ingresa usuario y contraseña');
    return;
  }

  loginBtn.disabled = true;
  loginBtn.textContent = 'Entrando...';

  try {
    var form = new FormData();
    form.append('username', user);
    form.append('password', pass);

    var res = await fetch(API_URL + '/auth/login', { method: 'POST', body: form });

    if (!res.ok) {
      var data = await res.json().catch(function () { return {}; });
      throw new Error(data.detail || 'Credenciales incorrectas');
    }

    var data = await res.json();
    token = data.access_token || data.token;
    username = user;
    localStorage.setItem('token', token);
    localStorage.setItem('username', username);

    usernameInput.value = '';
    passwordInput.value = '';
    showScreen('upload-screen');
  } catch (err) {
    showError(loginError, err.message || 'Error al iniciar sesión');
  } finally {
    loginBtn.disabled = false;
    loginBtn.textContent = 'Entrar';
  }
}

function logout() {
  token = '';
  username = '';
  selectedFiles = [];
  currentJobId = null;
  if (pollInterval) clearInterval(pollInterval);
  localStorage.removeItem('token');
  localStorage.removeItem('username');
  showScreen('login-screen');
}

function handleUnauthorized(res) {
  if (res.status === 401) {
    logout();
    showError(loginError, 'Sesión expirada. Inicia sesión de nuevo.');
    return true;
  }
  return false;
}

// ===== File Handling =====
var ALLOWED_EXT = ['.docx', '.xlsx', '.pptx'];

function getExtension(filename) {
  var i = filename.lastIndexOf('.');
  return i >= 0 ? filename.substring(i).toLowerCase() : '';
}

function addFiles(fileListInput) {
  uploadError.hidden = true;
  var rejected = [];

  for (var i = 0; i < fileListInput.length; i++) {
    var file = fileListInput[i];
    var ext = getExtension(file.name);

    if (ext === '.pdf') {
      rejected.push(file.name);
      continue;
    }

    if (ALLOWED_EXT.indexOf(ext) === -1) {
      continue; // silently ignore unknown formats
    }

    // Avoid duplicates
    var isDuplicate = selectedFiles.some(function (f) { return f.name === file.name && f.size === file.size; });
    if (!isDuplicate) {
      selectedFiles.push(file);
    }
  }

  if (rejected.length > 0) {
    showError(uploadError, 'PDF no está disponible aún: ' + rejected.join(', '));
  }

  renderFileList();
  translateBtn.disabled = selectedFiles.length === 0;
}

function removeFile(index) {
  selectedFiles.splice(index, 1);
  renderFileList();
  translateBtn.disabled = selectedFiles.length === 0;
}

function renderFileList() {
  if (selectedFiles.length === 0) {
    fileList.innerHTML = '';
    return;
  }

  var html = '';
  for (var i = 0; i < selectedFiles.length; i++) {
    html += '<div class="file-item">' +
      '<span>' + escapeHtml(selectedFiles[i].name) + '</span>' +
      '<button class="file-remove" onclick="removeFile(' + i + ')" title="Quitar">✕</button>' +
      '</div>';
  }
  fileList.innerHTML = html;
}

// ===== Drop Zone Events =====
dropZone.addEventListener('click', function () {
  fileInput.click();
});

fileInput.addEventListener('change', function () {
  if (fileInput.files.length > 0) {
    addFiles(fileInput.files);
    fileInput.value = '';
  }
});

dropZone.addEventListener('dragover', function (e) {
  e.preventDefault();
  dropZone.classList.add('dragover');
});

dropZone.addEventListener('dragleave', function () {
  dropZone.classList.remove('dragover');
});

dropZone.addEventListener('drop', function (e) {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length > 0) {
    addFiles(e.dataTransfer.files);
  }
});

// ===== Translation =====
async function startTranslation() {
  if (selectedFiles.length === 0) return;

  uploadError.hidden = true;
  translateBtn.disabled = true;

  try {
    var form = new FormData();
    for (var i = 0; i < selectedFiles.length; i++) {
      form.append('files', selectedFiles[i]);
    }
    form.append('language', languageSelect.value);

    var res = await fetch(API_URL + '/jobs', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: form
    });

    if (handleUnauthorized(res)) return;

    if (!res.ok) {
      var errData = await res.json().catch(function () { return {}; });
      throw new Error(errData.detail || 'Error al enviar archivos');
    }

    var data = await res.json();
    currentJobId = data.job_id;

    // Show results screen with progress
    showScreen('results-screen');
    progressSection.hidden = false;
    resultsSection.hidden = true;
    currentFileEl.textContent = '';
    progressText.textContent = '';
    var progressBarFill = document.getElementById('progress-bar-fill');
    var progressWords = document.getElementById('progress-words');
    if (progressBarFill) progressBarFill.style.width = '0%';
    if (progressWords) progressWords.textContent = 'Calculando palabras...';

    // Start polling
    pollStatus(currentJobId);

  } catch (err) {
    showError(uploadError, err.message || 'Error de conexión');
    translateBtn.disabled = false;
  }
}

function pollStatus(jobId) {
  if (pollInterval) clearInterval(pollInterval);

  pollInterval = setInterval(async function () {
    try {
      var res = await fetch(API_URL + '/jobs/' + jobId, {
        headers: getAuthHeaders()
      });

      if (handleUnauthorized(res)) {
        clearInterval(pollInterval);
        return;
      }

      if (!res.ok) {
        throw new Error('Error al consultar estado');
      }

      var data = await res.json();

      // Update progress display
      if (data.current_file) {
        currentFileEl.textContent = data.current_file;
      }
      if (data.files_done != null && data.files_total != null) {
        progressText.textContent = data.files_done + ' de ' + data.files_total + ' archivos';
      }

      var progressBarFill = document.getElementById('progress-bar-fill');
      var progressWords = document.getElementById('progress-words');

      if (data.words_total != null && data.words_total > 0) {
        var wordsTrans = data.words_translated || 0;
        var pct = Math.round((wordsTrans / data.words_total) * 100);
        pct = Math.min(99, Math.max(0, pct)); // clamp to 99% during translation
        
        if (progressBarFill) {
          progressBarFill.style.width = pct + '%';
        }
        if (progressWords) {
          progressWords.textContent = 'Progreso: ' + pct + '% (' + formatNumber(wordsTrans) + ' de ' + formatNumber(data.words_total) + ' palabras)';
        }
      } else {
        if (progressBarFill) {
          progressBarFill.style.width = '0%';
        }
        if (progressWords) {
          progressWords.textContent = 'Calculando palabras...';
        }
      }

      // Check completion
      if (data.status === 'listo') {
        if (progressBarFill) {
          progressBarFill.style.width = '100%';
        }
        if (progressWords) {
          progressWords.textContent = 'Progreso: 100% (' + formatNumber(data.words_total || 0) + ' de ' + formatNumber(data.words_total || 0) + ' palabras)';
        }
        clearInterval(pollInterval);
        showResults(data);
      } else if (data.status === 'error') {
        clearInterval(pollInterval);
        progressSection.hidden = true;
        resultsSection.hidden = false;
        downloadList.innerHTML = '';
        statsBox.innerHTML = '';
        resultsSection.innerHTML = '<div class="error-msg">Error en la traducción: ' +
          escapeHtml(data.error || 'Error desconocido') + '</div>' +
          '<button id="new-translation-btn" onclick="goBackToUpload()">Nueva traducción</button>';
      }

    } catch (err) {
      clearInterval(pollInterval);
      progressSection.hidden = true;
      resultsSection.hidden = false;
      resultsSection.innerHTML = '<div class="error-msg">Error de conexión al consultar estado</div>' +
        '<button id="new-translation-btn" onclick="goBackToUpload()">Nueva traducción</button>';
    }
  }, 3000);
}

function showResults(data) {
  progressSection.hidden = true;
  resultsSection.hidden = false;

  // Render download buttons — translated_files is a flat string array
  var html = '';
  var files = data.translated_files || [];
  for (var i = 0; i < files.length; i++) {
    html += '<div class="download-item">' +
      '<span>' + escapeHtml(files[i]) + '</span>' +
      '<button class="download-btn" onclick="downloadFile(\'' +
      escapeAttr(currentJobId) + '\', \'' + escapeAttr(files[i]) + '\')">Descargar</button>' +
      '</div>';
  }
  downloadList.innerHTML = html;

  // Render stats — backend sends word_count and cost_clp directly
  var totalWords = data.word_count || 0;
  var costCLP = data.cost_clp || 0;
  statsBox.innerHTML =
    'Palabras traducidas: <strong>' + formatNumber(totalWords) + '</strong><br>' +
    'Costo estimado: <strong>$' + formatNumber(costCLP) + ' CLP</strong>';
}

async function downloadFile(jobId, filename) {
  try {
    var res = await fetch(API_URL + '/jobs/' + jobId + '/download/' + encodeURIComponent(filename), {
      headers: getAuthHeaders()
    });

    if (handleUnauthorized(res)) return;

    if (!res.ok) {
      throw new Error('Error al descargar');
    }

    var blob = await res.blob();
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

  } catch (err) {
    alert('Error al descargar el archivo: ' + err.message);
  }
}

// ===== Navigation =====
function goBackToUpload() {
  selectedFiles = [];
  currentJobId = null;
  if (pollInterval) clearInterval(pollInterval);
  renderFileList();
  translateBtn.disabled = true;
  uploadError.hidden = true;

  var progressBarFill = document.getElementById('progress-bar-fill');
  var progressWords = document.getElementById('progress-words');
  if (progressBarFill) progressBarFill.style.width = '0%';
  if (progressWords) progressWords.textContent = '';

  // Reset results section to original structure
  resultsSection.innerHTML =
    '<h2>Archivos traducidos</h2>' +
    '<div id="download-list"></div>' +
    '<div id="stats-box" class="stats-box"></div>' +
    '<button id="new-translation-btn" onclick="goBackToUpload()">Nueva traducción</button>';
  downloadList = document.getElementById('download-list');
  statsBox = document.getElementById('stats-box');

  showScreen('upload-screen');
}

// ===== Utilities =====
function formatNumber(n) {
  return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
}

function escapeHtml(str) {
  var div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function escapeAttr(str) {
  return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function showError(el, message) {
  el.textContent = message;
  el.hidden = false;
}

// ===== Event Listeners =====
loginForm.addEventListener('submit', function (e) {
  e.preventDefault();
  login();
});

logoutBtn.addEventListener('click', logout);
logoutBtn2.addEventListener('click', logout);
translateBtn.addEventListener('click', startTranslation);
newTranslationBtn.addEventListener('click', goBackToUpload);

// ===== Init =====
(function init() {
  if (token && username) {
    showScreen('upload-screen');
  } else {
    showScreen('login-screen');
  }
})();
