const config = window.appConfig;
const appVersion = document.querySelector('meta[name="app-version"]')?.getAttribute('content') || 'unknown';

const latestSnapshot = {
  metrics: null,
  hardware: null,
  watcher: null,
};

const elements = {
  connectionDot: document.getElementById('connectionDot'),
  connectionText: document.getElementById('connectionText'),
  cpuValue: document.getElementById('cpuValue'),
  cpuLogical: document.getElementById('cpuLogical'),
  gpuValue: document.getElementById('gpuValue'),
  gpuDriver: document.getElementById('gpuDriver'),
  gpuSource: document.getElementById('gpuSource'),
  gpuConfidence: document.getElementById('gpuConfidence'),
  gpuDiagMessage: document.getElementById('gpuDiagMessage'),
  gpuDiagTechnical: document.getElementById('gpuDiagTechnical'),
  copyDiagnosticsButton: document.getElementById('copyDiagnosticsButton'),
  copyDiagnosticsStatus: document.getElementById('copyDiagnosticsStatus'),
  ramValue: document.getElementById('ramValue'),
  ramDetail: document.getElementById('ramDetail'),
  hardwareList: document.getElementById('hardwareList'),
  gamesList: document.getElementById('gamesList'),
  gamesCount: document.getElementById('gamesCount'),
  watcherRunning: document.getElementById('watcherRunning'),
  watcherActive: document.getElementById('watcherActive'),
  watcherLastEvent: document.getElementById('watcherLastEvent'),
  watcherLastGame: document.getElementById('watcherLastGame'),
  optimizeForm: document.getElementById('optimizeForm'),
  processName: document.getElementById('processName'),
  profileSelect: document.getElementById('profileSelect'),
  optimizeResult: document.getElementById('optimizeResult'),
};

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) {
    return '--';
  }
  const gb = bytes / (1024 ** 3);
  return `${gb.toFixed(1)} GB`;
}

function setConnected(connected) {
  elements.connectionDot.classList.toggle('connected', connected);
  elements.connectionDot.classList.toggle('disconnected', !connected);
  elements.connectionText.textContent = connected ? 'Live telemetry connected' : 'Disconnected';
}

function hasNumericGpuSample(gpuList) {
  if (!Array.isArray(gpuList)) {
    return false;
  }

  return gpuList.some((gpu) => typeof gpu?.utilization_percent === 'number' && Number.isFinite(gpu.utilization_percent));
}

function fallbackGpuDiagnostics(metrics) {
  const source = metrics?.gpu_source || 'unavailable';
  const confidenceReason = metrics?.gpu_confidence_reason || 'No telemetry confidence reason available.';
  const sampleAvailable = hasNumericGpuSample(metrics?.gpu);

  if (sampleAvailable) {
    return {
      status: 'ok',
      reason: 'Live GPU utilization sample available.',
      source_note: confidenceReason,
      provider_notes: [],
      sample_state: 'sample_available',
    };
  }

  if (source === 'fallback' || source === 'pdh') {
    return {
      status: 'metadata_only',
      reason: 'This provider exposes adapter metadata only, so utilization is shown as n/a.',
      source_note: confidenceReason,
      provider_notes: [],
      sample_state: 'metadata_only',
    };
  }

  if (source === 'wmi' || source === 'intel_counter') {
    return {
      status: 'no_sample',
      reason: 'No GPUEngine utilization sample was returned in this capture window.',
      source_note: confidenceReason,
      provider_notes: [],
      sample_state: 'no_sample',
    };
  }

  return {
    status: source === 'unavailable' ? 'provider_unavailable' : 'unknown',
    reason: source === 'unavailable'
      ? 'No GPU telemetry provider is currently available.'
      : 'GPU diagnostics field is not present in backend response.',
    source_note: confidenceReason,
    provider_notes: [],
    sample_state: source === 'unavailable' ? 'provider_unavailable' : 'unknown',
  };
}

function getGpuDiagnostics(metrics) {
  const diagnostics = metrics?.gpu_diagnostics;
  if (diagnostics && typeof diagnostics === 'object') {
    return {
      status: diagnostics.status || 'unknown',
      reason: diagnostics.reason || 'No diagnostic reason provided.',
      source_note: diagnostics.source_note || metrics?.gpu_confidence_reason || 'No source note available.',
      provider_notes: Array.isArray(diagnostics.provider_notes) ? diagnostics.provider_notes : [],
      sample_state: diagnostics.sample_state || 'unknown',
    };
  }

  return fallbackGpuDiagnostics(metrics);
}

function isPathLikeString(value) {
  if (typeof value !== 'string') {
    return false;
  }

  const trimmed = value.trim();
  if (!trimmed || /^https?:\/\//i.test(trimmed)) {
    return false;
  }

  const hasSeparator = trimmed.includes('\\') || trimmed.includes('/');
  if (!hasSeparator) {
    return false;
  }

  return /^[a-zA-Z]:[\\/]/.test(trimmed)
    || /^\\\\/.test(trimmed)
    || /^\//.test(trimmed)
    || /(\\Users\\|\/Users\/|\\Program Files\\|\/home\/)/i.test(trimmed);
}

function redactPathLikeString(value) {
  const segments = value.split(/[\\/]+/).filter(Boolean);
  const tail = segments[segments.length - 1];
  return tail ? `[redacted-path]/${tail}` : '[redacted-path]';
}

function sanitizeString(value) {
  if (isPathLikeString(value)) {
    return redactPathLikeString(value);
  }

  if (value.length > 400) {
    return `${value.slice(0, 400)}...[truncated]`;
  }

  return value;
}

function sanitizeDiagnosticsValue(value, seen = new WeakSet(), depth = 0) {
  if (value === null || value === undefined) {
    return value;
  }

  if (depth > 8) {
    return '[max-depth]';
  }

  if (typeof value === 'string') {
    return sanitizeString(value);
  }

  if (typeof value !== 'object') {
    return value;
  }

  if (seen.has(value)) {
    return '[circular]';
  }

  seen.add(value);

  if (Array.isArray(value)) {
    return value.map((item) => sanitizeDiagnosticsValue(item, seen, depth + 1));
  }

  const sanitized = {};
  Object.entries(value).forEach(([key, item]) => {
    sanitized[key] = sanitizeDiagnosticsValue(item, seen, depth + 1);
  });

  return sanitized;
}

function getWatcherLastEventValue(watcher) {
  const lastEvent = watcher?.last_event;
  if (!lastEvent) {
    return '--';
  }

  if (typeof lastEvent === 'string') {
    return lastEvent;
  }

  return lastEvent.type || '--';
}

function buildDiagnosticsPayload() {
  const now = new Date();
  const metrics = latestSnapshot.metrics || {};
  const hardware = latestSnapshot.hardware || {};
  const watcher = latestSnapshot.watcher || {};
  const gpu = Array.isArray(metrics.gpu) && metrics.gpu.length > 0 ? metrics.gpu[0] : null;
  const diagnostics = metrics.gpu_diagnostics && typeof metrics.gpu_diagnostics === 'object'
    ? metrics.gpu_diagnostics
    : getGpuDiagnostics(metrics);

  const payload = {
    timestamp_local: now.toLocaleString(),
    app_version: appVersion,
    gpu_source: metrics.gpu_source || '--',
    gpu_confidence: typeof metrics.gpu_confidence === 'number' ? metrics.gpu_confidence : null,
    gpu_confidence_reason: metrics.gpu_confidence_reason || '--',
    gpu_diagnostics: diagnostics,
    gpu_first_item_summary: {
      name: gpu?.name || '--',
      vendor: gpu?.vendor || '--',
      driver: gpu?.driver_version || '--',
      utilization: typeof gpu?.utilization_percent === 'number' ? gpu.utilization_percent : null,
      telemetry_backend: gpu?.telemetry_backend || '--',
    },
    watcher_status: {
      running: typeof watcher.running === 'boolean' ? watcher.running : null,
      active_count: watcher.active_count ?? null,
      last_event: getWatcherLastEventValue(watcher),
    },
    hardware_summary: {
      os: hardware.os || '--',
      machine: hardware.machine || '--',
      processor: hardware.processor || '--',
    },
  };

  return sanitizeDiagnosticsValue(payload);
}

function setCopyDiagnosticsStatus(message, kind) {
  if (!elements.copyDiagnosticsStatus) {
    return;
  }

  elements.copyDiagnosticsStatus.textContent = message;
  elements.copyDiagnosticsStatus.classList.remove('is-success', 'is-error');

  if (kind === 'success' || kind === 'error') {
    elements.copyDiagnosticsStatus.classList.add(`is-${kind}`);
  }
}

function copyWithExecCommand(text) {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  textarea.style.pointerEvents = 'none';
  document.body.appendChild(textarea);
  textarea.select();
  textarea.setSelectionRange(0, text.length);

  let copied = false;
  try {
    copied = document.execCommand('copy');
  } catch (error) {
    copied = false;
  }

  document.body.removeChild(textarea);
  return copied;
}

async function copyDiagnosticsToClipboard() {
  const diagnosticsText = JSON.stringify(buildDiagnosticsPayload(), null, 2);

  try {
    await navigator.clipboard.writeText(diagnosticsText);
    setCopyDiagnosticsStatus('Diagnostics copied to clipboard.', 'success');
    return;
  } catch (error) {
    const fallbackCopied = copyWithExecCommand(diagnosticsText);
    if (fallbackCopied) {
      setCopyDiagnosticsStatus('Diagnostics copied using fallback clipboard mode.', 'success');
      return;
    }
  }

  setCopyDiagnosticsStatus('Clipboard is blocked. A manual copy dialog was opened (Ctrl+C).', 'error');
  window.prompt('Copy diagnostics manually (Ctrl+C, Enter):', diagnosticsText);
}

function renderMetrics(metrics) {
  if (!metrics) {
    return;
  }

  latestSnapshot.metrics = metrics;

  elements.cpuValue.textContent = `${Number(metrics.cpu?.percent || 0).toFixed(1)}%`;
  elements.cpuLogical.textContent = metrics.cpu?.count_logical ?? '--';

  const gpu = metrics.gpu && metrics.gpu.length > 0 ? metrics.gpu[0] : null;
  const gpuUtil = typeof gpu?.utilization_percent === 'number' ? `${gpu.utilization_percent.toFixed(1)}%` : 'n/a';
  elements.gpuValue.textContent = `${gpuUtil}`;
  elements.gpuDriver.textContent = `${gpu?.name || 'Unavailable'} | Driver: ${gpu?.driver_version || '--'}`;
  elements.gpuSource.textContent = `Source: ${metrics.gpu_source || '--'}`;

  const confidenceScore = typeof metrics.gpu_confidence === 'number'
    ? `${Math.round(metrics.gpu_confidence * 100)}%`
    : '--';
  const confidenceReason = metrics.gpu_confidence_reason || 'No telemetry confidence reason available.';
  elements.gpuConfidence.textContent = `Confidence: ${confidenceScore} (${confidenceReason})`;

  const diagnostics = getGpuDiagnostics(metrics);
  const providerNotes = diagnostics.provider_notes.length > 0
    ? diagnostics.provider_notes.join(' | ')
    : 'No provider notes available.';
  elements.gpuDiagMessage.textContent = diagnostics.reason;
  elements.gpuDiagTechnical.textContent = `Status: ${diagnostics.status}\nSample state: ${diagnostics.sample_state}\nSource note: ${diagnostics.source_note}\nProvider notes: ${providerNotes}`;

  elements.ramValue.textContent = `${Number(metrics.memory?.percent || 0).toFixed(1)}%`;
  elements.ramDetail.textContent = `${formatBytes(metrics.memory?.used)} / ${formatBytes(metrics.memory?.total)}`;
}

function renderHardware(hardware) {
  if (!hardware) {
    return;
  }

  latestSnapshot.hardware = hardware;

  const lines = [
    `OS: ${hardware.os || '--'}`,
    `Machine: ${hardware.machine || '--'}`,
    `Processor: ${hardware.processor || '--'}`,
  ];

  if (Array.isArray(hardware.gpu) && hardware.gpu.length > 0) {
    const gpuNames = hardware.gpu.map((g) => g.name || 'Unknown').join(', ');
    lines.push(`GPU: ${gpuNames}`);
  }

  elements.hardwareList.innerHTML = lines.map((line) => `<li>${line}</li>`).join('');
}

function renderGames(games) {
  if (!Array.isArray(games)) {
    return;
  }

  elements.gamesCount.textContent = String(games.length);

  if (games.length === 0) {
    elements.gamesList.innerHTML = '<div class="game-item"><h3>No games discovered</h3><div class="game-meta">Install Steam/Epic titles or check launcher metadata.</div></div>';
    return;
  }

  elements.gamesList.innerHTML = games
    .map(
      (game) =>
        `<div class="game-item">
          <h3>${game.name}</h3>
          <div class="game-meta">Source: ${game.source} | Executables: ${(game.executable_names || []).join(', ') || 'n/a'}</div>
        </div>`
    )
    .join('');
}

function renderWatcher(watcher) {
  if (!watcher) {
    return;
  }

  latestSnapshot.watcher = watcher;

  elements.watcherRunning.textContent = watcher.running ? 'Yes' : 'No';
  elements.watcherActive.textContent = String(watcher.active_count ?? 0);
  elements.watcherLastEvent.textContent = watcher.last_event?.type || '--';
  elements.watcherLastGame.textContent = watcher.last_detected?.game_name || '--';
}

async function loadInitialData() {
  try {
    const [hardwareRes, gamesRes] = await Promise.all([
      fetch(`${config.backendUrl}/hardware`),
      fetch(`${config.backendUrl}/games`),
    ]);

    if (hardwareRes.ok) {
      renderHardware(await hardwareRes.json());
    }

    if (gamesRes.ok) {
      const gamesPayload = await gamesRes.json();
      renderGames(gamesPayload.games || []);
    }
  } catch (error) {
    elements.optimizeResult.textContent = `Initial load failed: ${error.message}`;
  }
}

function connectWebSocket() {
  const ws = new WebSocket(config.wsUrl);

  ws.addEventListener('open', () => {
    setConnected(true);
  });

  ws.addEventListener('message', (event) => {
    try {
      const parsed = JSON.parse(event.data);
      if (parsed.type === 'snapshot') {
        renderMetrics(parsed.payload.metrics);
        renderHardware(parsed.payload.hardware);
        renderGames(parsed.payload.games);
        renderWatcher(parsed.payload.watcher);
      }

      if (parsed.type === 'metrics') {
        renderMetrics(parsed.payload);
        renderWatcher(parsed.watcher);
      }

      if (parsed.type === 'watcher_event') {
        renderWatcher(parsed.watcher);
        const gameName = parsed.payload?.game_name || 'Unknown';
        elements.optimizeResult.textContent = `Watcher event: ${parsed.payload?.event} -> ${gameName}`;
      }
    } catch (error) {
      console.error(error);
    }
  });

  ws.addEventListener('close', () => {
    setConnected(false);
    setTimeout(connectWebSocket, 1200);
  });

  ws.addEventListener('error', () => {
    ws.close();
  });
}

elements.optimizeForm.addEventListener('submit', async (e) => {
  e.preventDefault();

  const processName = elements.processName.value.trim().toLowerCase();
  const profile = elements.profileSelect.value;
  if (!processName) {
    return;
  }

  try {
    const res = await fetch(`${config.backendUrl}/optimize/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ process_name: processName, profile }),
    });

    const payload = await res.json();
    elements.optimizeResult.textContent = JSON.stringify(payload, null, 2);
  } catch (error) {
    elements.optimizeResult.textContent = `Failed to apply optimization: ${error.message}`;
  }
});

if (elements.copyDiagnosticsButton) {
  elements.copyDiagnosticsButton.addEventListener('click', () => {
    copyDiagnosticsToClipboard();
  });
}

loadInitialData();
connectWebSocket();
