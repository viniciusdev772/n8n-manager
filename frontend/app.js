/* ── N8N Manager — Frontend ──────────────────── */

const API = '';
let currentInstance = null;

/* ── Helpers ─────────────────────────────────── */

function getToken() {
  return localStorage.getItem('n8n_token') || '';
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    ...opts,
    headers: {
      'Authorization': 'Bearer ' + getToken(),
      'Content-Type': 'application/json',
      ...(opts.headers || {}),
    },
  });
  if (res.status === 401 || res.status === 403) {
    doLogout();
    throw new Error('Token invalido');
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Erro na API');
  return data;
}

function toast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast ' + type;
  el.classList.remove('hidden');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add('hidden'), 3500);
}

function statusBadge(status) {
  const cls = status === 'running' ? 'status-running' : status === 'exited' ? 'status-exited' : 'status-created';
  return `<span class="status-badge ${cls}">${status}</span>`;
}

function openModal(msg, onConfirm) {
  document.getElementById('modal-msg').textContent = msg;
  document.getElementById('modal-overlay').classList.remove('hidden');
  const btn = document.getElementById('modal-confirm');
  btn.onclick = () => { closeModal(); onConfirm(); };
}

function closeModal() {
  document.getElementById('modal-overlay').classList.add('hidden');
}

/* ── Auth ────────────────────────────────────── */

async function doLogin() {
  const token = document.getElementById('token-input').value.trim();
  if (!token) return;
  localStorage.setItem('n8n_token', token);
  try {
    await api('/instances');
    showApp();
  } catch {
    localStorage.removeItem('n8n_token');
    const err = document.getElementById('login-error');
    err.textContent = 'Token invalido ou servidor indisponivel';
    err.classList.remove('hidden');
  }
}

function doLogout() {
  localStorage.removeItem('n8n_token');
  document.getElementById('app').classList.add('hidden');
  document.getElementById('login-screen').classList.remove('hidden');
  document.getElementById('token-input').value = '';
  document.getElementById('login-error').classList.add('hidden');
}

function showApp() {
  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  navigate('dashboard');
}

/* ── Navigation ──────────────────────────────── */

function navigate(page) {
  // Limpar polling de jobs ao sair da pagina
  if (_jobsInterval) { clearInterval(_jobsInterval); _jobsInterval = null; }

  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  const target = document.getElementById('page-' + page);
  if (target) target.classList.remove('hidden');

  document.querySelectorAll('.nav-item[data-page]').forEach(b => {
    b.classList.toggle('active', b.dataset.page === page);
  });

  if (page === 'dashboard') loadDashboard();
  else if (page === 'instances') loadInstances();
  else if (page === 'jobs') loadJobs();
  else if (page === 'create') loadCreateForm();
  else if (page === 'cleanup') loadCleanup();
}

/* ── Dashboard ───────────────────────────────── */

async function loadDashboard() {
  try {
    const [health, capacity, instances] = await Promise.all([
      api('/health'),
      api('/capacity'),
      api('/instances'),
    ]);

    document.getElementById('dash-status').textContent = health.status;
    document.getElementById('dash-status').style.color = health.status === 'ok' ? 'var(--green)' : 'var(--amber)';
    document.getElementById('dash-redis').textContent = health.checks.redis;
    document.getElementById('dash-redis').style.color = health.checks.redis === 'ok' ? 'var(--green)' : 'var(--red)';
    document.getElementById('dash-docker').textContent = health.checks.docker;
    document.getElementById('dash-docker').style.color = health.checks.docker === 'ok' ? 'var(--green)' : 'var(--red)';
    document.getElementById('dash-active').textContent = capacity.active_instances;
    document.getElementById('dash-capacity').textContent = capacity.active_instances + '/' + capacity.max_instances;

    renderInstancesTable('dash-instances-list', instances.instances.slice(0, 8));

    // Jobs ativos no dashboard
    try {
      const jobsData = await api('/jobs');
      const section = document.getElementById('dash-jobs-section');
      if (jobsData.jobs.length) {
        section.classList.remove('hidden');
        renderJobsTable('dash-jobs-list', jobsData.jobs);
      } else {
        section.classList.add('hidden');
      }
    } catch { /* ignora se falhar */ }
  } catch (e) {
    toast('Erro ao carregar dashboard: ' + e.message, 'error');
  }
}

/* ── Jobs ────────────────────────────────────── */

let _jobsInterval = null;

async function loadJobs() {
  clearInterval(_jobsInterval);
  await _fetchJobs();
  _jobsInterval = setInterval(_fetchJobs, 3000);
}

async function _fetchJobs() {
  try {
    const data = await api('/jobs');
    const listEl = document.getElementById('jobs-list');
    const emptyEl = document.getElementById('jobs-empty');
    if (!data.jobs.length) {
      listEl.innerHTML = '';
      emptyEl.classList.remove('hidden');
      return;
    }
    emptyEl.classList.add('hidden');
    renderJobsTable('jobs-list', data.jobs);
  } catch (e) {
    toast('Erro ao carregar jobs: ' + e.message, 'error');
  }
}

function renderJobsTable(containerId, jobs) {
  const el = document.getElementById(containerId);
  el.innerHTML = `
    <table>
      <thead><tr><th>Job ID</th><th>Instancia</th><th>Estado</th><th>Progresso</th><th>Ultima Mensagem</th><th></th></tr></thead>
      <tbody>${jobs.map(j => `
        <tr>
          <td><code style="font-size:.78rem;color:var(--text-mute)">${esc(j.job_id.substring(0, 8))}</code></td>
          <td><strong>${esc(j.name || '--')}</strong></td>
          <td>${jobStateBadge(j.state)}</td>
          <td>
            <div style="display:flex;align-items:center;gap:.5rem">
              <div class="progress-track" style="width:100px">
                <div class="progress-fill" style="width:${j.progress || 0}%"></div>
              </div>
              <span style="font-family:var(--font-mono);font-size:.75rem;color:var(--text-dim)">${j.progress || 0}%</span>
            </div>
          </td>
          <td style="font-size:.82rem;color:var(--text-dim);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(j.last_message || '--')}</td>
          <td><button class="btn btn-outline btn-sm" onclick="watchJob('${esc(j.job_id)}')">Acompanhar</button></td>
        </tr>
      `).join('')}</tbody>
    </table>`;
}

function jobStateBadge(state) {
  if (state === 'running') return '<span class="status-badge status-running">running</span>';
  if (state === 'pending') return '<span class="status-badge status-created">pending</span>';
  return '<span class="status-badge">' + esc(state) + '</span>';
}

function watchJob(jobId) {
  navigate('create');
  const progress = document.getElementById('create-progress');
  const fill = document.getElementById('create-progress-fill');
  const msg = document.getElementById('create-progress-msg');
  const result = document.getElementById('create-result');
  const btn = document.getElementById('create-btn');
  progress.classList.remove('hidden');
  result.classList.add('hidden');
  btn.disabled = true;
  fill.style.width = '0%';
  msg.textContent = 'Acompanhando job...';
  pollJob(jobId, fill, msg, result, btn);
}

/* ── Instances ───────────────────────────────── */

async function loadInstances() {
  try {
    const data = await api('/instances');
    renderInstancesTable('instances-list', data.instances);
  } catch (e) {
    toast('Erro ao carregar instancias: ' + e.message, 'error');
  }
}

function renderInstancesTable(containerId, instances) {
  const el = document.getElementById(containerId);
  if (!instances.length) {
    el.innerHTML = '<div class="empty-state"><p>Nenhuma instancia encontrada</p></div>';
    return;
  }
  el.innerHTML = `
    <table>
      <thead><tr><th>Nome</th><th>Status</th><th>Versao</th><th>Idade</th><th>URL</th><th></th></tr></thead>
      <tbody>${instances.map(i => `
        <tr>
          <td><strong>${esc(i.name || i.instance_id || '--')}</strong></td>
          <td>${statusBadge(i.status || 'unknown')}</td>
          <td><code style="font-size:.8rem">${esc(i.version || '--')}</code></td>
          <td>${i.age_days != null ? '<span style="font-family:var(--font-mono);font-size:.82rem">' + i.age_days + 'd</span>' : '--'}</td>
          <td>${i.url ? `<a href="${esc(i.url)}" target="_blank">${esc(i.url)}</a>` : '--'}</td>
          <td><button class="btn btn-outline btn-sm" onclick="openInstance('${esc(i.name || i.instance_id)}')">Detalhes</button></td>
        </tr>
      `).join('')}</tbody>
    </table>`;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

/* ── Instance Detail ─────────────────────────── */

async function openInstance(id) {
  currentInstance = id;
  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  document.getElementById('page-instance-detail').classList.remove('hidden');
  document.getElementById('detail-title').textContent = id;
  document.getElementById('version-update-form').classList.add('hidden');

  try {
    const data = await api('/instance/' + encodeURIComponent(id) + '/status');
    document.getElementById('detail-status').innerHTML = statusBadge(data.status);
    document.getElementById('detail-version').textContent = data.version || '--';
    document.getElementById('detail-memory').textContent = data.memory ? data.memory.usage_mb + ' / ' + data.memory.limit_mb + ' MB' : '--';
    document.getElementById('detail-url').innerHTML = data.url ? `<a href="${esc(data.url)}" target="_blank" style="font-size:.82rem;word-break:break-all">${esc(data.url)}</a>` : '--';
    loadInstanceLogs();
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

async function loadInstanceLogs() {
  if (!currentInstance) return;
  try {
    const data = await api('/instance/' + encodeURIComponent(currentInstance) + '/logs?tail=80');
    document.getElementById('detail-logs').textContent = data.logs || '(sem logs)';
  } catch (e) {
    document.getElementById('detail-logs').textContent = 'Erro ao carregar logs: ' + e.message;
  }
}

async function restartInstance() {
  if (!currentInstance) return;
  openModal('Reiniciar instancia ' + currentInstance + '?', async () => {
    try {
      await api('/instance/' + encodeURIComponent(currentInstance) + '/restart', { method: 'POST' });
      toast('Instancia reiniciada', 'success');
      setTimeout(() => openInstance(currentInstance), 2000);
    } catch (e) { toast('Erro: ' + e.message, 'error'); }
  });
}

async function resetInstance() {
  if (!currentInstance) return;
  openModal('RESETAR instancia ' + currentInstance + '? Todos os dados serao perdidos!', async () => {
    try {
      await api('/instance/' + encodeURIComponent(currentInstance) + '/reset', {
        method: 'POST',
        body: JSON.stringify({ version: 'latest' }),
      });
      toast('Instancia resetada', 'success');
      setTimeout(() => openInstance(currentInstance), 3000);
    } catch (e) { toast('Erro: ' + e.message, 'error'); }
  });
}

async function deleteInstance() {
  if (!currentInstance) return;
  openModal('EXCLUIR instancia ' + currentInstance + '? Esta acao e irreversivel!', async () => {
    try {
      await api('/delete-instance/' + encodeURIComponent(currentInstance), { method: 'DELETE' });
      toast('Instancia excluida', 'success');
      currentInstance = null;
      navigate('instances');
    } catch (e) { toast('Erro: ' + e.message, 'error'); }
  });
}

async function showUpdateVersion() {
  const form = document.getElementById('version-update-form');
  if (!form.classList.contains('hidden')) {
    form.classList.add('hidden');
    return;
  }
  try {
    const data = await api('/versions');
    const select = document.getElementById('update-version-select');
    select.innerHTML = data.versions.map(v => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('');
    form.classList.remove('hidden');
  } catch (e) { toast('Erro ao carregar versoes: ' + e.message, 'error'); }
}

async function doUpdateVersion() {
  if (!currentInstance) return;
  const version = document.getElementById('update-version-select').value;
  openModal('Atualizar ' + currentInstance + ' para versao ' + version + '?', async () => {
    try {
      await api('/instance/' + encodeURIComponent(currentInstance) + '/update-version', {
        method: 'POST',
        body: JSON.stringify({ version }),
      });
      toast('Versao atualizada', 'success');
      document.getElementById('version-update-form').classList.add('hidden');
      setTimeout(() => openInstance(currentInstance), 3000);
    } catch (e) { toast('Erro: ' + e.message, 'error'); }
  });
}

/* ── Create Instance ─────────────────────────── */

async function loadCreateForm() {
  document.getElementById('create-progress').classList.add('hidden');
  document.getElementById('create-result').classList.add('hidden');
  document.getElementById('create-btn').disabled = false;
  try {
    const data = await api('/versions');
    const select = document.getElementById('create-version');
    select.innerHTML = data.versions.map(v => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('');
  } catch {
    document.getElementById('create-version').innerHTML = '<option value="latest">latest</option>';
  }
}

async function doCreate() {
  const name = document.getElementById('create-name').value.trim();
  const version = document.getElementById('create-version').value;
  if (!name) { toast('Nome obrigatorio', 'error'); return; }

  const btn = document.getElementById('create-btn');
  btn.disabled = true;
  const progress = document.getElementById('create-progress');
  const fill = document.getElementById('create-progress-fill');
  const msg = document.getElementById('create-progress-msg');
  const result = document.getElementById('create-result');
  progress.classList.remove('hidden');
  result.classList.add('hidden');
  fill.style.width = '5%';
  msg.textContent = 'Enfileirando...';

  try {
    const job = await api('/enqueue-instance', {
      method: 'POST',
      body: JSON.stringify({ name, version }),
    });
    pollJob(job.job_id, fill, msg, result, btn);
  } catch (e) {
    msg.textContent = 'Erro: ' + e.message;
    result.className = 'error';
    result.textContent = e.message;
    result.classList.remove('hidden');
    btn.disabled = false;
  }
}

async function pollJob(jobId, fill, msg, resultEl, btn) {
  let idx = 0;
  const poll = async () => {
    try {
      const data = await api('/job/' + jobId + '/events?since=' + idx);
      for (const ev of data.events) {
        const pct = ev.progress || 0;
        fill.style.width = pct + '%';
        msg.textContent = ev.message || ev.status || '...';
        idx++;

        if (ev.status === 'complete') {
          fill.style.width = '100%';
          resultEl.className = 'success';
          resultEl.innerHTML = 'Instancia criada! <a href="' + esc(ev.url || '') + '" target="_blank">' + esc(ev.url || '') + '</a>';
          resultEl.classList.remove('hidden');
          btn.disabled = false;
          return;
        }
        if (ev.status === 'error') {
          resultEl.className = 'error';
          resultEl.textContent = ev.message || 'Erro desconhecido';
          resultEl.classList.remove('hidden');
          btn.disabled = false;
          return;
        }
      }
      setTimeout(poll, 1500);
    } catch (e) {
      msg.textContent = 'Erro ao consultar progresso';
      btn.disabled = false;
    }
  };
  poll();
}

/* ── Cleanup ─────────────────────────────────── */

async function loadCleanup() {
  try {
    const data = await api('/cleanup-preview');
    const el = document.getElementById('cleanup-list');
    if (!data.instances.length) {
      el.innerHTML = '<div class="empty-state"><p>Nenhuma instancia</p></div>';
      return;
    }
    el.innerHTML = `
      <table>
        <thead><tr><th>Nome</th><th>Status</th><th>Idade</th><th>Dias Restantes</th><th>Sera Removida</th></tr></thead>
        <tbody>${data.instances.map(i => `
          <tr style="${i.will_be_deleted ? 'background:rgba(248,113,113,.04)' : ''}">
            <td><strong>${esc(i.name || i.instance_id || '--')}</strong></td>
            <td>${statusBadge(i.status || 'unknown')}</td>
            <td><span style="font-family:var(--font-mono);font-size:.82rem">${i.age_days != null ? i.age_days + 'd' : '--'}</span></td>
            <td><span style="font-family:var(--font-mono);font-size:.82rem">${i.days_remaining != null ? i.days_remaining + 'd' : '--'}</span></td>
            <td>${i.will_be_deleted ? '<span style="color:var(--red);font-weight:600;font-size:.82rem">Sim</span>' : '<span style="color:var(--green);font-size:.82rem">Nao</span>'}</td>
          </tr>
        `).join('')}</tbody>
      </table>`;
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

/* ── Debug ───────────────────────────────────── */

async function loadAllContainers() {
  try {
    const data = await api('/debug/all-containers');
    const el = document.getElementById('debug-containers');
    el.innerHTML = `
      <table>
        <thead><tr><th>Nome</th><th>Imagem</th><th>Status</th><th>Portas</th><th>Redes</th></tr></thead>
        <tbody>${data.containers.map(c => `
          <tr>
            <td><strong>${esc(c.name)}</strong></td>
            <td><code style="font-size:.75rem;color:var(--text-dim)">${esc(c.image)}</code></td>
            <td>${statusBadge(c.status)}</td>
            <td style="font-size:.8rem">${c.ports.map(esc).join(', ') || '--'}</td>
            <td style="font-size:.8rem">${c.networks.map(esc).join(', ')}</td>
          </tr>
        `).join('')}</tbody>
      </table>`;
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

async function loadInfraNetworks() {
  try {
    const data = await api('/debug/infra-networks');
    document.getElementById('debug-networks').textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

async function recreateTraefik() {
  openModal('Recriar Traefik? Pode causar downtime temporario.', async () => {
    const el = document.getElementById('debug-traefik');
    el.classList.remove('hidden');
    el.textContent = 'Recriando...';
    try {
      const data = await api('/debug/recreate-traefik', { method: 'POST' });
      el.textContent = JSON.stringify(data, null, 2);
      toast('Traefik recriado', 'success');
    } catch (e) {
      el.textContent = 'Erro: ' + e.message;
      toast('Erro: ' + e.message, 'error');
    }
  });
}

/* ── Init ────────────────────────────────────── */

document.getElementById('token-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') doLogin();
});

if (getToken()) {
  api('/instances').then(() => showApp()).catch(() => doLogout());
} else {
  document.getElementById('login-screen').classList.remove('hidden');
}
