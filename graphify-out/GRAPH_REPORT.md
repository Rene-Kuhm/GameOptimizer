# Graph Report - /Volumes/Programacion/GameOptimizer  (2026-04-25)

## Corpus Check
- 21 files · ~35,404 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 254 nodes · 577 edges · 15 communities detected
- Extraction: 83% EXTRACTED · 17% INFERRED · 0% AMBIGUOUS · INFERRED: 97 edges (avg confidence: 0.7)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]

## God Nodes (most connected - your core abstractions)
1. `GameWatcher` - 23 edges
2. `GameEntry` - 20 edges
3. `OptimizationProfiles` - 19 edges
4. `discover_all_games()` - 13 edges
5. `_collect_amd_with_adl_ctypes()` - 12 edges
6. `apply_profile()` - 12 edges
7. `rollback_session()` - 12 edges
8. `ProviderOutput` - 11 edges
9. `_build_entry()` - 11 edges
10. `_gpu_vendor()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `_gpu_vendor()` --calls--> `test_gpu_vendor_detection()`  [INFERRED]
  /Volumes/Programacion/GameOptimizer/backend/app/system.py → /Volumes/Programacion/GameOptimizer/backend/tests/test_system_gpu.py
- `_select_gpu_telemetry()` --calls--> `test_unavailable_provider_reports_diagnostics()`  [INFERRED]
  /Volumes/Programacion/GameOptimizer/backend/app/system.py → /Volumes/Programacion/GameOptimizer/backend/tests/test_system_gpu.py
- `get_system_metrics()` --calls--> `system_metrics()`  [INFERRED]
  /Volumes/Programacion/GameOptimizer/backend/app/system.py → /Volumes/Programacion/GameOptimizer/backend/app/main.py
- `GameEntry` --uses--> `ProviderContext`  [INFERRED]
  /Volumes/Programacion/GameOptimizer/backend/app/models.py → /Volumes/Programacion/GameOptimizer/backend/app/discovery.py
- `GameEntry` --uses--> `DiscoveryProvider`  [INFERRED]
  /Volumes/Programacion/GameOptimizer/backend/app/models.py → /Volumes/Programacion/GameOptimizer/backend/app/discovery.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.09
Nodes (40): _ADLAdapterInfo, _ADLPMActivity, AmdNativeHookProvider, _append_missing_wmi_adapters(), _build_gpu_diagnostics(), _coalesce_memory_usage(), _coerce_records(), _collect_amd_with_adl_ctypes() (+32 more)

### Community 1 - "Community 1"
Cohesion: 0.11
Nodes (29): BaseModel, get_executable_metadata_cache(), OptimizeRequest, GameEntry, WatcherEvent, _match_override(), _normalize_path_text(), _normalize_text() (+21 more)

### Community 2 - "Community 2"
Cohesion: 0.12
Nodes (27): _build_entry(), _deduplicate_games(), discover_all_games(), _discover_executable_paths(), _display_icon_to_path(), _epic_manifest_paths(), EpicProvider, GenericFilesystemProvider (+19 more)

### Community 3 - "Community 3"
Cohesion: 0.13
Nodes (14): AppState, _env_float(), _env_int(), hardware(), health(), on_shutdown(), on_startup(), optimize_apply() (+6 more)

### Community 4 - "Community 4"
Cohesion: 0.17
Nodes (17): buildDiagnosticsPayload(), copyDiagnosticsToClipboard(), copyWithExecCommand(), fallbackGpuDiagnostics(), formatBytes(), getGpuDiagnostics(), getWatcherLastEventValue(), hasNumericGpuSample() (+9 more)

### Community 5 - "Community 5"
Cohesion: 0.21
Nodes (17): get_logger(), setup_logging(), apply_profile(), _collect_targets(), _get_process_affinity(), _get_process_priority(), _legacy_profile_settings(), _new_action() (+9 more)

### Community 6 - "Community 6"
Cohesion: 0.24
Nodes (7): backendCommand(), createTray(), createTrayIcon(), findPython(), getEmbeddedPythonPath(), resolveBackendDir(), startBackend()

### Community 7 - "Community 7"
Cohesion: 0.24
Nodes (6): _confidence_for_source(), _select_gpu_telemetry(), FakeNvidiaProvider, test_gpu_vendor_detection(), test_native_provider_keeps_dual_gpu_wmi_adapter(), test_unavailable_provider_reports_diagnostics()

### Community 8 - "Community 8"
Cohesion: 0.39
Nodes (2): ExecutableMetadataCache, _normalize_path()

### Community 9 - "Community 9"
Cohesion: 0.33
Nodes (3): DiscoveryProvider, Protocol, GpuTelemetryProvider

### Community 10 - "Community 10"
Cohesion: 0.5
Nodes (0): 

### Community 11 - "Community 11"
Cohesion: 1.0
Nodes (0): 

### Community 12 - "Community 12"
Cohesion: 1.0
Nodes (0): 

### Community 13 - "Community 13"
Cohesion: 1.0
Nodes (0): 

### Community 14 - "Community 14"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **2 isolated node(s):** `_ADLAdapterInfo`, `Keep native telemetry but still expose extra adapters in dual-GPU laptops.`
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 11`** (2 nodes): `test_windows_oriented_modules_import_on_current_platform()`, `test_import_compat.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 12`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 13`** (1 nodes): `conftest.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 14`** (1 nodes): `preload.js`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `GameWatcher` connect `Community 1` to `Community 3`?**
  _High betweenness centrality (0.068) - this node is a cross-community bridge._
- **Why does `GameEntry` connect `Community 1` to `Community 9`, `Community 2`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Why does `OptimizationProfiles` connect `Community 1` to `Community 3`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Are the 11 inferred relationships involving `GameWatcher` (e.g. with `GameEntry` and `WatcherEvent`) actually correct?**
  _`GameWatcher` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 19 inferred relationships involving `GameEntry` (e.g. with `ProviderContext` and `DiscoveryProvider`) actually correct?**
  _`GameEntry` has 19 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `OptimizationProfiles` (e.g. with `ActiveGame` and `GameProfile`) actually correct?**
  _`OptimizationProfiles` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `discover_all_games()` (e.g. with `.refresh_games()` and `test_discover_all_games_does_not_require_winreg()`) actually correct?**
  _`discover_all_games()` has 2 INFERRED edges - model-reasoned connections that need verification._