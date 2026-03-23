const config = window.appConfig;

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

function renderMetrics(metrics) {
  if (!metrics) {
    return;
  }

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

loadInitialData();
connectWebSocket();
