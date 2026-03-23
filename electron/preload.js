const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('appConfig', {
  backendUrl: 'http://127.0.0.1:8765',
  wsUrl: 'ws://127.0.0.1:8765/ws/metrics',
});
