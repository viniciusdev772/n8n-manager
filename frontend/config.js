/* ── N8N Manager — Config Page ────────────────── */

const API = '';

/* ── Helpers (reutilizados do app.js) ────────── */

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

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

/* ── Auth ────────────────────────────────────── */

async function doLogin() {
  const token = document.getElementById('token-input').value.trim();
  if (!token) return;
  localStorage.setItem('n8n_token', token);
  try {
    await api('/health');
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
  loadConfig();
  loadSystemInfo();
}

/* ── Config Load/Save ────────────────────────── */

// Campos editaveis (id do input sem prefixo "cfg-")
const EDITABLE_FIELDS = [
  'BASE_DOMAIN', 'ACME_EMAIL', 'CF_DNS_API_TOKEN',
  'SERVER_PORT', 'ALLOWED_ORIGINS', 'API_AUTH_TOKEN',
  'DEFAULT_N8N_VERSION', 'DEFAULT_TIMEZONE',
  'INSTANCE_MEM_LIMIT', 'INSTANCE_MEM_RESERVATION', 'INSTANCE_CPU_SHARES',
  'CLEANUP_MAX_AGE_DAYS', 'CLEANUP_INTERVAL_SECONDS',
];

// Campos somente leitura
const READONLY_FIELDS = [
  'DOCKER_NETWORK', 'RABBITMQ_HOST', 'RABBITMQ_PORT',
  'REDIS_HOST', 'REDIS_PORT', 'JOB_TTL',
];

let originalConfig = {};

async function loadConfig() {
  try {
    const data = await api('/config');
    const cfg = data.config;
    originalConfig = { ...cfg };

    // Preencher campos editaveis
    for (const key of EDITABLE_FIELDS) {
      const el = document.getElementById('cfg-' + key);
      if (el && cfg[key] !== undefined) {
        el.value = cfg[key];
      }
    }

    // Preencher campos read-only
    for (const key of READONLY_FIELDS) {
      const el = document.getElementById('cfg-' + key);
      if (el && cfg[key] !== undefined) {
        el.value = cfg[key];
      }
    }
  } catch (e) {
    toast('Erro ao carregar configuracoes: ' + e.message, 'error');
  }
}

async function saveConfig() {
  const updates = {};

  for (const key of EDITABLE_FIELDS) {
    const el = document.getElementById('cfg-' + key);
    if (!el) continue;
    const val = el.value.trim();
    // Só enviar se mudou
    if (val !== originalConfig[key]) {
      updates[key] = val;
    }
  }

  if (Object.keys(updates).length === 0) {
    toast('Nenhuma alteracao detectada');
    return;
  }

  try {
    const result = await api('/config', {
      method: 'PUT',
      body: JSON.stringify({ config: updates }),
    });

    toast('Configuracoes salvas!', 'success');
    document.getElementById('save-msg').textContent = 'Salvo: ' + result.updated_keys.join(', ');

    if (result.needs_restart) {
      document.getElementById('restart-banner').classList.remove('hidden');
    }

    // Atualizar original para não detectar mudança duplicada
    for (const [k, v] of Object.entries(updates)) {
      originalConfig[k] = v;
    }
  } catch (e) {
    toast('Erro ao salvar: ' + e.message, 'error');
  }
}

/* ── System Info ─────────────────────────────── */

async function loadSystemInfo() {
  try {
    const info = await api('/config/system-info');

    if (info.ram) {
      document.getElementById('sys-ram').textContent = info.ram.total_mb + ' MB';
      document.getElementById('sys-ram-avail').textContent = info.ram.available_mb + ' MB';
    }
    if (info.swap) {
      document.getElementById('sys-swap').textContent = info.swap.total_mb + ' / ' + info.swap.used_mb + ' MB';
    }
    if (info.docker) {
      document.getElementById('sys-docker').textContent = 'v' + info.docker.version;
    }
    if (info.uptime) {
      document.getElementById('sys-uptime').textContent = info.uptime;
    }
    if (info.capacity) {
      document.getElementById('sys-capacity').textContent =
        info.capacity.active_instances + ' / ' + info.capacity.max_instances;
    }
  } catch (e) {
    // Silencioso — info é secundaria
  }
}

/* ── Cloudflare Test ─────────────────────────── */

async function testCloudflare() {
  const input = document.getElementById('cfg-CF_DNS_API_TOKEN');
  let token = input.value.trim();

  // Se mascarado, pedir para revelar primeiro
  if (token.startsWith('****')) {
    try {
      const data = await api('/config?reveal=CF_DNS_API_TOKEN');
      token = data.config.CF_DNS_API_TOKEN;
      input.value = token;
    } catch {
      toast('Erro ao revelar token', 'error');
      return;
    }
  }

  if (!token) {
    toast('Insira o token antes de testar', 'error');
    return;
  }

  toast('Testando token...');
  try {
    const result = await api('/config/test-cloudflare', {
      method: 'POST',
      body: JSON.stringify({ token }),
    });
    if (result.valid) {
      toast('Token valido!', 'success');
    } else {
      toast('Token invalido: ' + result.message, 'error');
    }
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

/* ── Restart Service ─────────────────────────── */

async function restartService() {
  if (!confirm('Reiniciar o servico n8n-manager? O painel ficara indisponivel por alguns segundos.')) return;

  toast('Reiniciando servico...');
  try {
    await api('/config/restart-service', { method: 'POST' });
  } catch {
    // Esperado — o servico vai cair
  }

  document.getElementById('restart-banner').classList.add('hidden');

  // Polling ate voltar
  let attempts = 0;
  const poll = setInterval(async () => {
    attempts++;
    try {
      await fetch(API + '/health', { headers: { 'Authorization': 'Bearer ' + getToken() } });
      clearInterval(poll);
      toast('Servico reiniciado com sucesso!', 'success');
      document.getElementById('save-msg').textContent = '';
      loadConfig();
      loadSystemInfo();
    } catch {
      if (attempts > 30) {
        clearInterval(poll);
        toast('Timeout: servico nao respondeu em 30s', 'error');
      }
    }
  }, 1000);
}

/* ── UI Helpers ──────────────────────────────── */

function togglePassword(inputId) {
  const input = document.getElementById(inputId);
  input.type = input.type === 'password' ? 'text' : 'password';

  // Se mascarado, revelar do servidor
  if (input.value.startsWith('****') && input.type === 'text') {
    const key = inputId.replace('cfg-', '');
    api('/config?reveal=' + key).then(data => {
      input.value = data.config[key];
    }).catch(() => {});
  }
}

async function copyField(inputId) {
  const input = document.getElementById(inputId);
  let value = input.value;

  // Se mascarado, buscar valor real
  if (value.startsWith('****')) {
    try {
      const key = inputId.replace('cfg-', '');
      const data = await api('/config?reveal=' + key);
      value = data.config[key];
      input.value = value;
    } catch {
      toast('Erro ao revelar valor', 'error');
      return;
    }
  }

  try {
    await navigator.clipboard.writeText(value);
    toast('Copiado!', 'success');
  } catch {
    // Fallback
    input.type = 'text';
    input.select();
    document.execCommand('copy');
    input.type = 'password';
    toast('Copiado!', 'success');
  }
}

/* ── Init ────────────────────────────────────── */

document.getElementById('token-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') doLogin();
});

if (getToken()) {
  api('/health').then(() => showApp()).catch(() => doLogout());
} else {
  document.getElementById('login-screen').classList.remove('hidden');
}
