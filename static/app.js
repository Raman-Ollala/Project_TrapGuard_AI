/* TrapGuard AI frontend: plain JavaScript, intentionally kept framework-free. */
const API = '';
const $ = (id) => document.getElementById(id);
const state = {
  session: JSON.parse(localStorage.getItem('trapguard_session') || 'null'),
  model: null,
  stream: null,
  facingMode: 'environment',
  running: false,
  detections: [],
  lastDetectAt: 0,
  lastPostAt: 0,
  frames: 0,
  fpsStartedAt: performance.now(),
  logs: JSON.parse(localStorage.getItem('trapguard_logs') || '[]'),
  latestPendingAlertId: null,
  cameras: []
};

function showToast(message, bad = false) {
  const el = $('toast');
  el.textContent = message;
  el.style.background = bad ? '#9f1d18' : '#173126';
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 2600);
}

async function api(path, options = {}) {
  const res = await fetch(API + path, {headers: {'Content-Type':'application/json', ...(options.headers||{})}, ...options});
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed (${res.status})`);
  }
  return res.json();
}

function setStatus(text, mode) {
  const chip = $('liveStatus');
  chip.textContent = text;
  chip.className = `status-chip status-${mode}`;
}

async function loadSigninNames() {
  try {
    const users = await api('/api/rangers');
    $('signinName').innerHTML = users.map(u => `<option value="${u.name}">${u.name}${u.role === 'admin' ? ' (Admin)' : ''}</option>`).join('');
  } catch {
    $('signinName').innerHTML = '<option>Ranger Asha</option><option>Admin</option>';
  }
}

async function signIn() {
  try {
    const data = await api('/api/login', {method:'POST', body:JSON.stringify({name:$('signinName').value, pin:$('signinPin').value})});
    state.session = data;
    localStorage.setItem('trapguard_session', JSON.stringify(data));
    enterApp();
    showToast(`Signed in as ${data.name}`);
  } catch {
    showToast('Incorrect officer or PIN.', true);
  }
}

function enterApp() {
  if (!state.session) return;
  $('signinView').classList.add('hidden');
  $('appShell').classList.remove('hidden');
  $('sessionUser').textContent = state.session.name;
  $('roleChip').textContent = state.session.role.toUpperCase();
  document.querySelectorAll('.admin-only').forEach(el => el.classList.toggle('hidden', state.session.role !== 'admin'));
  if (state.session.role !== 'admin' && document.querySelector('#adminTab.active')) switchTab('camera');
  loadInitialData();
}

function signOut() {
  if (!confirm('Are you sure you want to end your shift and sign out?')) return;
  stopCamera();
  state.session = null;
  localStorage.removeItem('trapguard_session');
  $('appShell').classList.add('hidden');
  $('signinView').classList.remove('hidden');
  $('signinPin').value = '';
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-btn[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $(name + 'Tab').classList.add('active');
  if (name === 'alerts') loadAlerts();
  if (name === 'admin') loadAdmin();
}

async function loadInitialData() {
  try {
    state.cameras = await api('/api/cameras');
    $('cameraSelect').innerHTML = state.cameras.map(c => `<option value="${c.id}">${c.name} · ${c.zone_tag}${c.protected ? ' · protected' : ''}</option>`).join('');
    $('cameraList').innerHTML = state.cameras.map(c => `<div class="camera-node"><div><strong>${c.name}</strong><div class="alert-meta">${c.zone_tag}${c.protected ? ' · protected zone' : ''}</div></div><div class="online-dot">● Online</div></div>`).join('');
  } catch (e) { showToast('Backend offline. Local detection can still run.', true); }
  renderLogs();
}

// ============================================================
// FEATURE 1: CAMERA -> DETECT -> DRAW BOXES
// This is the main student-friendly block to study first.
// ============================================================
async function ensureModel() {
  if (state.model) return;
  try {
    setStatus('LOADING MODEL', 'loading');
    $('modelState').textContent = 'Loading…';
    state.model = await cocoSsd.load({base:'lite_mobilenet_v2'});
    $('modelState').textContent = 'Ready';
    setStatus(state.running ? 'LIVE' : 'STOPPED', state.running ? 'live' : 'stopped');
  } catch (e) {
    $('modelState').textContent = 'Load failed';
    setStatus('STOPPED', 'stopped');
    showToast('Could not load AI model. Check internet connection.', true);
    throw e;
  }
}

async function startCamera() {
  try {
    await ensureModel();
    stopCamera(false);
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera API unavailable. Use HTTPS or localhost.');
    state.stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:state.facingMode}}, audio:false});
    $('video').srcObject = state.stream;
    $('video').removeAttribute('src');
    await $('video').play();
    state.running = true;
    $('cameraEmpty').classList.add('hidden');
    setStatus('LIVE', 'live');
    requestAnimationFrame(detectionLoop);
  } catch (e) { showToast(`Camera unavailable: ${e.message}`, true); }
}

function stopCamera(clearVideo = true) {
  state.running = false;
  if (state.stream) state.stream.getTracks().forEach(t => t.stop());
  state.stream = null;
  if (clearVideo) {
    $('video').pause();
    $('video').srcObject = null;
    $('video').removeAttribute('src');
    $('cameraEmpty').classList.remove('hidden');
    const ctx = $('overlay').getContext('2d'); ctx.clearRect(0,0,$('overlay').width,$('overlay').height);
  }
  setStatus('STOPPED', 'stopped');
}

async function switchCamera() {
  state.facingMode = state.facingMode === 'environment' ? 'user' : 'environment';
  if (state.running && state.stream) await startCamera();
}

async function handleVideoUpload(file) {
  if (!file) return;
  await ensureModel();
  stopCamera(false);
  const url = URL.createObjectURL(file);
  const video = $('video');
  video.srcObject = null; video.src = url; video.loop = true; video.muted = true;
  await video.play();
  state.running = true;
  $('cameraEmpty').classList.add('hidden');
  setStatus('LIVE', 'live');
  requestAnimationFrame(detectionLoop);
}

function classify(label) {
  const animals = new Set(['bird','cat','dog','horse','sheep','cow','elephant','bear','zebra','giraffe']);
  const vehicles = new Set(['bicycle','car','motorcycle','bus','truck']);
  const objects = new Set(['knife','backpack','handbag','suitcase']);
  if (animals.has(label)) return {group:'animal', display:'Animal'};
  if (label === 'person') return {group:'human', display:'Person'};
  if (vehicles.has(label)) return {group:'vehicle', display:label};
  if (objects.has(label)) return {group:'object', display:'Suspicious object'};
  return null;
}

async function detectionLoop(now) {
  if (!state.running) return;
  const video = $('video');
  if (video.readyState >= 2 && state.model && now - state.lastDetectAt >= 250) {
    state.lastDetectAt = now;
    const minScore = Number($('confidenceSlider').value);
    try {
      const predictions = await state.model.detect(video, 20, minScore);
      const filtered = predictions.map(p => ({...p, kind:classify(p.class)})).filter(p => p.kind);
      state.detections = filtered;
      drawDetections(filtered);
      updateLiveCounts(filtered);
      logDetections(filtered);
      state.frames++;
      const elapsed = (performance.now() - state.fpsStartedAt) / 1000;
      if (elapsed >= 1) { $('fpsValue').textContent = Math.round(state.frames / elapsed); state.frames = 0; state.fpsStartedAt = performance.now(); }
      if (filtered.length && now - state.lastPostAt >= 1000) { state.lastPostAt = now; postSightings(filtered); }
    } catch (e) { console.warn('Detection error', e); }
  }
  requestAnimationFrame(detectionLoop);
}

function drawDetections(items) {
  const video = $('video'), canvas = $('overlay'), ctx = canvas.getContext('2d');
  canvas.width = video.videoWidth || 1280; canvas.height = video.videoHeight || 720;
  ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.font = '16px system-ui'; ctx.lineWidth = 3;
  items.forEach(p => {
    const [x,y,w,h] = p.bbox;
    const animal = p.kind.group === 'animal';
    ctx.strokeStyle = animal ? '#21d46b' : '#ff3b30';
    ctx.fillStyle = ctx.strokeStyle;
    ctx.strokeRect(x,y,w,h);
    const text = `${p.kind.display} ${Math.round(p.score*100)}%`;
    const width = ctx.measureText(text).width + 12;
    ctx.fillRect(x, Math.max(0,y-24), width, 24);
    ctx.fillStyle = '#fff'; ctx.fillText(text, x+6, Math.max(17,y-7));
  });
}

function updateLiveCounts(items) {
  $('animalCount').textContent = items.filter(x => x.kind.group === 'animal').length;
  $('riskCount').textContent = items.filter(x => x.kind.group !== 'animal').length;
}

function logDetections(items) {
  const now = new Date().toLocaleTimeString();
  items.forEach(p => {
    state.logs.unshift({time:now, label:p.class, group:p.kind.group, confidence:Math.round(p.score*100)});
  });
  state.logs = state.logs.slice(0,200);
  localStorage.setItem('trapguard_logs', JSON.stringify(state.logs));
  renderLogs();
}

function renderLogs() {
  $('detectionLog').innerHTML = state.logs.length ? state.logs.map(r => `<div class="log-row"><span>${r.time}</span><strong>${r.label}</strong><span>${r.group}</span><span>${r.confidence}%</span></div>`).join('') : '<div class="alert-meta">No detections yet.</div>';
}

function snapshotDataUrl() {
  const video = $('video');
  if (!video.videoWidth) return null;
  const c = document.createElement('canvas');
  const maxW = 640, scale = Math.min(1, maxW / video.videoWidth);
  c.width = Math.round(video.videoWidth * scale); c.height = Math.round(video.videoHeight * scale);
  c.getContext('2d').drawImage(video,0,0,c.width,c.height);
  return c.toDataURL('image/jpeg', .65);
}

async function postSightings(items) {
  const payload = {
    camera_id:Number($('cameraSelect').value),
    detections:items.map(p => ({label:p.class, group:p.kind.group, confidence:p.score, bbox:p.bbox})),
    snapshot_base64:snapshotDataUrl()
  };
  try {
    const result = await api('/api/sightings', {method:'POST', body:JSON.stringify(payload)});
    if (result.created_alerts?.length) result.created_alerts.forEach(handleCreatedAlert);
  } catch (e) {
    const q = JSON.parse(localStorage.getItem('trapguard_queue') || '[]');
    q.push({...payload, snapshot_base64:null, queued_at:new Date().toISOString()});
    localStorage.setItem('trapguard_queue', JSON.stringify(q.slice(-20)));
  }
}

function handleCreatedAlert(alert) {
  $('alertBanner').textContent = `${alert.severity.toUpperCase()}: ${alert.message}`;
  $('alertBanner').classList.remove('hidden');
  if (['high','critical'].includes(alert.severity)) emergencyFeedback();
  if (alert.status === 'pending') openVerifyModal(alert);
  setTimeout(() => $('alertBanner').classList.add('hidden'), 5000);
}

function emergencyFeedback() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination); osc.frequency.value = 880; gain.gain.value = .08; osc.start(); osc.stop(ctx.currentTime + .18);
  } catch {}
  if (navigator.vibrate) navigator.vibrate([180,80,180]);
}
// ====================== END FEATURE 1 BLOCK ======================

function downloadLogs() {
  const lines = ['time,label,group,confidence', ...state.logs.map(r => `${r.time},${r.label},${r.group},${r.confidence}`)];
  const blob = new Blob([lines.join('\n')], {type:'text/csv'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'trapguard-detection-log.csv'; a.click(); URL.revokeObjectURL(a.href);
}

async function simulate(kind) {
  try {
    const r = await api(`/api/simulate/${kind}`, {method:'POST'});
    if (r.alert) { showToast(`Simulated: ${r.alert.message}`); handleCreatedAlert(r.alert); }
    else showToast('Simulation is in 10-second cooldown.');
    loadAlerts();
  } catch { showToast('Could not simulate alert.', true); }
}

async function loadAlerts() {
  try {
    const data = await api('/api/alerts?limit=100');
    const severity = $('severityFilter').value, type = $('typeFilter').value;
    const types = [...new Set(data.map(a => a.type))];
    const old = $('typeFilter').value;
    $('typeFilter').innerHTML = '<option value="">All types</option>' + types.map(t => `<option value="${t}">${t.replaceAll('_',' ')}</option>`).join('');
    $('typeFilter').value = old;
    const filtered = data.filter(a => (!severity || a.severity === severity) && (!type || a.type === type));
    $('alertsList').innerHTML = filtered.length ? filtered.map(alertCard).join('') : '<div class="card" style="padding:18px">No matching alerts.</div>';
    document.querySelectorAll('[data-verify-id]').forEach(b => b.onclick = () => verifyAlert(Number(b.dataset.verifyId), b.dataset.action));
    const pending = data.find(a => a.status === 'pending' && ['human_vehicle','human_animal','suspicious_object'].includes(a.type));
    if (pending && pending.id !== state.latestPendingAlertId) openVerifyModal(pending);
  } catch { $('alertsList').innerHTML = '<div class="card" style="padding:18px">Backend offline.</div>'; }
}

function alertCard(a) {
  const pendingButtons = a.status === 'pending' && ['human_vehicle','human_animal','suspicious_object'].includes(a.type) ? `<div class="alert-actions"><button class="btn btn-primary" data-verify-id="${a.id}" data-action="ranger">✅ It's me</button><button class="btn btn-danger" data-verify-id="${a.id}" data-action="not_me">❌ Not me</button></div>` : '';
  const img = a.snapshot_path ? `<img class="alert-thumb" src="${a.snapshot_path}" alt="snapshot">` : `<div class="alert-thumb"></div>`;
  return `<article class="alert-card ${a.severity}"><div class="alert-top"><div><div class="alert-title">${a.message}</div><div class="alert-meta">${a.camera_name} · ${new Date(a.ts+'Z').toLocaleString()}</div></div><div><span class="severity-badge ${a.severity}">${a.severity}</span> <span class="status-badge">${a.status}</span></div></div><div class="alert-body">${img}<div><div><strong>Type:</strong> ${a.type.replaceAll('_',' ')}</div>${pendingButtons}</div></div></article>`;
}

function openVerifyModal(alert) {
  state.latestPendingAlertId = alert.id;
  $('verifyMessage').textContent = `${alert.message} at ${alert.camera_name || 'protected camera'}. Was this you?`;
  if (alert.snapshot_path) { $('verifyImage').src = alert.snapshot_path; $('verifyImage').classList.remove('hidden'); } else $('verifyImage').classList.add('hidden');
  $('verificationModal').classList.remove('hidden');
}

async function verifyAlert(id, action) {
  if (!state.session) return;
  try {
    await api(`/api/alerts/${id}/verify`, {method:'POST', body:JSON.stringify({action, ranger_name:state.session.name})});
    $('verificationModal').classList.add('hidden');
    state.latestPendingAlertId = null;
    showToast(action === 'ranger' ? 'Marked safe: verified ranger.' : 'Reported as NOT ME. Admin notified.');
    loadAlerts(); loadAdmin();
  } catch { showToast('Could not update alert.', true); }
}

async function loadAdmin() {
  if (state.session?.role !== 'admin') return;
  try {
    const [stats, schedule, alerts] = await Promise.all([api('/api/stats'), api('/api/schedule'), api('/api/alerts?limit=100')]);
    $('statTotal').textContent = stats.total; $('statCrimes').textContent = stats.crimes; $('statEscalated').textContent = stats.escalated; $('statPending').textContent = stats.pending;
    const max = Math.max(1, ...stats.hotspots.map(h => h.alert_count));
    $('hotspotBars').innerHTML = stats.hotspots.map(h => `<div class="bar-row"><div class="bar-label"><span>${h.name}</span><span>${h.alert_count}</span></div><div class="bar-track"><div class="bar-fill" style="width:${h.alert_count/max*100}%"></div></div></div>`).join('');
    $('scheduleBody').innerHTML = schedule.map(s => `<tr><td>${s.ranger_name}</td><td>${s.camera_name}</td><td>${new Date(s.start_time+'Z').toLocaleString()}</td><td>${new Date(s.end_time+'Z').toLocaleString()}</td></tr>`).join('');
    const important = alerts.filter(a => ['crime','escalated'].includes(a.status));
    $('adminAlerts').innerHTML = important.length ? important.map(a => `<div class="camera-node"><div><strong>${a.message}</strong><div class="alert-meta">${a.camera_name} · ${a.status}</div></div>${a.snapshot_path ? `<img class="alert-thumb" src="${a.snapshot_path}">` : ''}</div>`).join('') : '<div style="padding:14px" class="alert-meta">No escalated/crime alerts yet.</div>';
  } catch { showToast('Admin dashboard could not refresh.', true); }
}

function bindEvents() {
  $('signinBtn').onclick = signIn; $('signinPin').addEventListener('keydown', e => { if (e.key === 'Enter') signIn(); }); $('signoutBtn').onclick = signOut;
  document.querySelectorAll('.nav-btn[data-tab]').forEach(b => b.onclick = () => switchTab(b.dataset.tab));
  $('startBtn').onclick = startCamera; $('stopBtn').onclick = () => stopCamera(); $('switchBtn').onclick = switchCamera;
  $('videoUpload').onchange = e => handleVideoUpload(e.target.files[0]);
  $('confidenceSlider').oninput = e => $('confidenceValue').textContent = `${Math.round(Number(e.target.value)*100)}%`;
  $('clearLogBtn').onclick = () => { state.logs=[]; localStorage.removeItem('trapguard_logs'); renderLogs(); };
  $('downloadLogBtn').onclick = downloadLogs;
  document.querySelectorAll('[data-sim]').forEach(b => b.onclick = () => simulate(b.dataset.sim));
  $('severityFilter').onchange = loadAlerts; $('typeFilter').onchange = loadAlerts;
  $('verifyYesBtn').onclick = () => verifyAlert(state.latestPendingAlertId, 'ranger'); $('verifyNoBtn').onclick = () => verifyAlert(state.latestPendingAlertId, 'not_me'); $('verifyCloseBtn').onclick = () => $('verificationModal').classList.add('hidden');
}

async function boot() {
  bindEvents(); await loadSigninNames(); if (state.session) enterApp();
  setInterval(() => { if (state.session) loadAlerts(); }, 3000);
  setInterval(() => { if (state.session?.role === 'admin') loadAdmin(); }, 5000);
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/static/sw.js').catch(()=>{});
}
boot();
