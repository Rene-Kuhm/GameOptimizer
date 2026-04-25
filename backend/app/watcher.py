from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from .discovery import KNOWN_IGNORED_PROCESS_NAMES
from .executable_meta import get_executable_metadata_cache
from .logging_setup import get_logger
from .models import GameEntry, WatcherEvent
from .optimizer import apply_profile, rollback_session
from .profiles import OptimizationProfiles

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _normalize_path(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return str(Path(path).resolve(strict=False)).lower()
    except OSError:
        return str(Path(path)).lower()


def _title_tokens(title: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]{3,}", title.lower())}


@dataclass
class ActiveGame:
    game: GameEntry
    pid: int
    process_name: str
    process_exe: str | None
    confidence: float
    reason: str
    matched_via: list[str]


@dataclass
class GameProfile:
    game: GameEntry
    normalized_title: str
    title_tokens: set[str]
    executable_names: set[str]
    executable_stems: set[str]
    executable_paths: set[str]
    hashes: set[str]
    signer_subjects: set[str]
    hash_compare_paths: list[str]


@dataclass
class MatchResult:
    matched: bool
    confidence: float
    reason: str
    matched_via: list[str]


@dataclass
class ProcessSnapshot:
    pid: int
    name: str
    executable_path: str | None
    metadata: dict[str, Any] | None = None
    parent_pid: int | None = None
    parent_name: str | None = None


def _env_float(name: str, default: float, *, min_value: float = 0.0, max_value: float = 120.0) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning("invalid float env var name=%s value=%s using_default=%s", name, raw, default)
        return default
    return max(min_value, min(max_value, value))


def _is_ignored_process_name(name: str | None) -> bool:
    if not name:
        return False
    return name.strip().lower() in KNOWN_IGNORED_PROCESS_NAMES


class GameWatcher:
    def __init__(
        self,
        poll_interval_seconds: int = 3,
        profile_resolver: OptimizationProfiles | None = None,
        optimization_delay_seconds: float | None = None,
    ) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self.optimization_delay_seconds = (
            _env_float("GAME_OPTIMIZER_OPTIMIZATION_DELAY_SECONDS", default=0.0)
            if optimization_delay_seconds is None
            else max(0.0, optimization_delay_seconds)
        )
        self._games: list[GameEntry] = []
        self._profiles: list[GameProfile] = []
        self._active_by_id: dict[str, ActiveGame] = {}
        self._pending_by_id: dict[str, dict[str, Any]] = {}
        self._optimization_sessions: dict[str, dict[str, Any]] = {}
        self._profile_resolver = profile_resolver or OptimizationProfiles()
        self._metadata_cache = get_executable_metadata_cache()
        self.last_detected: dict[str, Any] | None = None
        self.last_event: dict[str, Any] | None = None
        self.last_optimization_action: dict[str, Any] | None = None
        self.running = False

    def set_games(self, games: list[GameEntry]) -> None:
        self._games = games
        self._profiles = [self._build_profile(game) for game in games]

    def _build_profile(self, game: GameEntry) -> GameProfile:
        executable_names = {_normalize_name(exe) for exe in game.executable_names if exe}
        executable_stems = {
            _normalize_name(exe.removesuffix(".exe"))
            for exe in game.executable_names
            if exe
        }

        executable_paths: set[str] = set()
        hashes: set[str] = set()
        signer_subjects: set[str] = set()
        hash_compare_paths: list[str] = []

        executable_metadata = game.metadata.get("executables", []) if isinstance(game.metadata, dict) else []
        for payload in executable_metadata:
            if not isinstance(payload, dict):
                continue
            path_value = _normalize_path(payload.get("path"))
            if path_value:
                executable_paths.add(path_value)
                if len(hash_compare_paths) < 3:
                    hash_compare_paths.append(path_value)
            digest = payload.get("sha256")
            if isinstance(digest, str) and digest:
                hashes.add(digest.lower())
            signature = payload.get("signature")
            if isinstance(signature, dict):
                subject = signature.get("subject")
                if isinstance(subject, str) and subject:
                    signer_subjects.add(subject.lower())

        return GameProfile(
            game=game,
            normalized_title=_normalize_name(game.name),
            title_tokens=_title_tokens(game.name),
            executable_names=executable_names,
            executable_stems=executable_stems,
            executable_paths=executable_paths,
            hashes=hashes,
            signer_subjects=signer_subjects,
            hash_compare_paths=hash_compare_paths,
        )

    def _match(self, process: ProcessSnapshot, profile: GameProfile) -> MatchResult:
        score = 0.0
        reasons: list[str] = []

        if _is_ignored_process_name(process.name):
            return MatchResult(matched=False, confidence=0.0, reason="ignored_process", matched_via=[])

        proc_name = process.name.lower()
        proc_normalized = _normalize_name(proc_name)
        proc_stem = _normalize_name(proc_name.removesuffix(".exe"))

        if proc_normalized in profile.executable_names:
            score += 0.55
            reasons.append("exact_executable_name")
        elif proc_stem in profile.executable_stems:
            score += 0.5
            reasons.append("exact_executable_stem")
        elif any(token and token in proc_normalized for token in profile.executable_stems):
            score += 0.2
            reasons.append("executable_substring")

        process_path = _normalize_path(process.executable_path)
        if process_path and process_path in profile.executable_paths:
            score += 0.35
            reasons.append("canonical_path")

        if profile.title_tokens:
            proc_tokens = _title_tokens(proc_name)
            overlap = len(proc_tokens & profile.title_tokens)
            if overlap:
                ratio = overlap / max(1, len(profile.title_tokens))
                score += min(0.3, ratio * 0.3)
                reasons.append("title_token_overlap")

        if score >= 0.5 and (profile.hashes or profile.signer_subjects):
            if process.metadata is None and process_path:
                process.metadata = self._metadata_cache.collect(
                    process_path,
                    include_hash=bool(profile.hashes),
                    include_signature=bool(profile.signer_subjects),
                )

            if process.metadata and profile.hashes:
                proc_hash = process.metadata.get("sha256")
                if isinstance(proc_hash, str) and proc_hash.lower() in profile.hashes:
                    score += 0.2
                    reasons.append("sha256_match")

            if process.metadata and profile.signer_subjects:
                signature = process.metadata.get("signature")
                subject = signature.get("subject") if isinstance(signature, dict) else None
                if isinstance(subject, str) and subject.lower() in profile.signer_subjects:
                    score += 0.1
                    reasons.append("signature_subject_match")

        if score >= 0.7 and process_path and profile.hash_compare_paths and "sha256_match" not in reasons:
            if process.metadata is None or "sha256" not in process.metadata:
                process.metadata = self._metadata_cache.collect(process_path, include_hash=True, include_signature=False)

            proc_hash = process.metadata.get("sha256") if process.metadata else None
            if isinstance(proc_hash, str) and proc_hash:
                for known_path in profile.hash_compare_paths:
                    known_meta = self._metadata_cache.collect(known_path, include_hash=True, include_signature=False)
                    known_hash = known_meta.get("sha256")
                    if isinstance(known_hash, str) and known_hash == proc_hash:
                        score += 0.15
                        reasons.append("sha256_match")
                        break

        if score >= 0.5 and _is_ignored_process_name(process.parent_name):
            score += 0.08
            reasons.append("launcher_child_process")

        score = min(1.0, score)
        matched = score >= 0.62
        reason = "+".join(reasons) if reasons else "no_match"
        return MatchResult(matched=matched, confidence=score, reason=reason, matched_via=reasons)

    def _iter_processes(self) -> list[ProcessSnapshot]:
        items: list[ProcessSnapshot] = []
        try:
            process_iter = list(psutil.process_iter(["pid", "name", "exe", "ppid"]))
        except Exception:
            return items

        names_by_pid: dict[int, str] = {}
        for proc in process_iter:
            proc_name = (proc.info.get("name") or "").strip().lower()
            if proc_name:
                names_by_pid[proc.pid] = proc_name

        for proc in process_iter:
            proc_name = (proc.info.get("name") or "").strip().lower()
            if not proc_name:
                continue
            if _is_ignored_process_name(proc_name):
                continue
            executable = proc.info.get("exe")
            parent_pid = proc.info.get("ppid")
            parent_pid = parent_pid if isinstance(parent_pid, int) else None
            items.append(
                ProcessSnapshot(
                    pid=proc.pid,
                    name=proc_name,
                    executable_path=executable if isinstance(executable, str) else None,
                    parent_pid=parent_pid,
                    parent_name=names_by_pid.get(parent_pid) if parent_pid is not None else None,
                )
            )
        return items

    def _ready_started_games(self, scanned: dict[str, ActiveGame]) -> list[ActiveGame]:
        now = datetime.now(timezone.utc)
        ready: list[ActiveGame] = []
        active_ids = set(self._active_by_id)

        for game_id, game in scanned.items():
            if game_id in active_ids:
                self._pending_by_id.pop(game_id, None)
                continue

            pending = self._pending_by_id.get(game_id)
            signature = (game.pid, game.process_name, game.process_exe)
            if pending is None or pending.get("signature") != signature:
                self._pending_by_id[game_id] = {
                    "first_seen_at": now,
                    "signature": signature,
                    "game": game,
                }
                if self.optimization_delay_seconds > 0:
                    continue
                ready.append(game)
                continue

            pending["game"] = game
            elapsed = (now - pending["first_seen_at"]).total_seconds()
            if elapsed >= self.optimization_delay_seconds:
                ready.append(game)

        for game_id in list(self._pending_by_id):
            if game_id not in scanned:
                self._pending_by_id.pop(game_id, None)

        return ready

    def _scan(self) -> dict[str, ActiveGame]:
        found: dict[str, ActiveGame] = {}
        if not self._profiles:
            return found

        for process in self._iter_processes():
            for profile in self._profiles:
                if profile.game.id in found:
                    continue

                result = self._match(process, profile)
                if not result.matched:
                    continue

                found[profile.game.id] = ActiveGame(
                    game=profile.game,
                    pid=process.pid,
                    process_name=process.name,
                    process_exe=_normalize_path(process.executable_path),
                    confidence=result.confidence,
                    reason=result.reason,
                    matched_via=result.matched_via,
                )

        return found

    async def run(self, event_callback, default_profile: str = "default") -> None:
        self.running = True
        while self.running:
            scanned = self._scan()
            started = self._ready_started_games(scanned)
            stopped = [item for gid, item in self._active_by_id.items() if gid not in scanned]

            for game in started:
                profile_name, profile_config, profile_resolution = self._profile_resolver.resolve_for_game(
                    game.game,
                    default_profile,
                    process_name=game.process_name,
                    process_path=game.process_exe,
                )
                optimize_result = apply_profile(
                    game.process_name,
                    profile=profile_name,
                    target_pid=game.pid,
                    profile_config=profile_config,
                )

                session_changes = optimize_result.get("session_changes")
                if isinstance(session_changes, list) and session_changes:
                    self._optimization_sessions[game.game.id] = {
                        "pid": game.pid,
                        "process_name": game.process_name,
                        "profile": profile_name,
                        "session_changes": session_changes,
                        "started_at": _now_iso(),
                    }

                self.last_optimization_action = {
                    "phase": "apply",
                    "timestamp": _now_iso(),
                    "game_id": game.game.id,
                    "game_name": game.game.name,
                    "profile": profile_name,
                    "applied": len(optimize_result.get("applied", [])),
                    "skipped": len(optimize_result.get("skipped", [])),
                    "failed": len(optimize_result.get("failed", [])),
                    "success": optimize_result.get("success", False),
                }
                logger.info(
                    "watcher_apply game=%s pid=%s profile=%s applied=%s skipped=%s failed=%s",
                    game.game.name,
                    game.pid,
                    profile_name,
                    len(optimize_result.get("applied", [])),
                    len(optimize_result.get("skipped", [])),
                    len(optimize_result.get("failed", [])),
                )

                event = WatcherEvent(
                    event="game_started",
                    game_id=game.game.id,
                    game_name=game.game.name,
                    process_name=game.process_name,
                    pid=game.pid,
                    timestamp=_now_iso(),
                    details={
                        "optimization": optimize_result,
                        "profile_resolution": profile_resolution,
                        "matching": {
                            "confidence": round(game.confidence, 3),
                            "reason": game.reason,
                            "matched_via": game.matched_via,
                            "process_path": game.process_exe,
                        },
                    },
                )
                self.last_detected = {
                    "game_id": game.game.id,
                    "game_name": game.game.name,
                    "process_name": game.process_name,
                    "pid": game.pid,
                    "match_confidence": round(game.confidence, 3),
                    "match_reason": game.reason,
                }
                self.last_event = {
                    "type": event.event,
                    "timestamp": event.timestamp,
                    "game": game.game.name,
                }
                await event_callback(event)

            for game in stopped:
                rollback_result: dict[str, Any] | None = None
                optimization_session = self._optimization_sessions.pop(game.game.id, None)
                if optimization_session is not None:
                    rollback_result = rollback_session(optimization_session.get("session_changes"))
                    self.last_optimization_action = {
                        "phase": "rollback",
                        "timestamp": _now_iso(),
                        "game_id": game.game.id,
                        "game_name": game.game.name,
                        "profile": optimization_session.get("profile"),
                        "applied": len(rollback_result.get("applied", [])),
                        "skipped": len(rollback_result.get("skipped", [])),
                        "failed": len(rollback_result.get("failed", [])),
                        "success": rollback_result.get("success", False),
                        "reason": "process_exit_detected",
                    }
                    logger.info(
                        "watcher_rollback game=%s pid=%s applied=%s skipped=%s failed=%s",
                        game.game.name,
                        game.pid,
                        len(rollback_result.get("applied", [])),
                        len(rollback_result.get("skipped", [])),
                        len(rollback_result.get("failed", [])),
                    )

                event = WatcherEvent(
                    event="game_stopped",
                    game_id=game.game.id,
                    game_name=game.game.name,
                    process_name=game.process_name,
                    pid=game.pid,
                    timestamp=_now_iso(),
                    details={
                        "rollback": rollback_result,
                        "matching": {
                            "confidence": round(game.confidence, 3),
                            "reason": game.reason,
                            "matched_via": game.matched_via,
                            "process_path": game.process_exe,
                        }
                    },
                )
                self.last_event = {
                    "type": event.event,
                    "timestamp": event.timestamp,
                    "game": game.game.name,
                }
                await event_callback(event)

            next_active = {
                game_id: scanned[game_id]
                for game_id in self._active_by_id
                if game_id in scanned
            }
            for game in started:
                next_active[game.game.id] = game
                self._pending_by_id.pop(game.game.id, None)
            self._active_by_id = next_active
            await asyncio.sleep(self.poll_interval_seconds)

    def restore_active_sessions(self, reason: str = "watcher_stop") -> dict[str, Any]:
        restored: list[dict[str, Any]] = []
        for game_id, session in list(self._optimization_sessions.items()):
            rollback_result = rollback_session(session.get("session_changes"))
            restored.append({"game_id": game_id, "rollback": rollback_result})
            self._optimization_sessions.pop(game_id, None)

        if restored:
            self.last_optimization_action = {
                "phase": "rollback",
                "timestamp": _now_iso(),
                "reason": reason,
                "restored_sessions": len(restored),
                "success": all(item["rollback"].get("success", False) for item in restored),
            }

        return {"restored": restored, "count": len(restored), "reason": reason}

    def stop(self) -> None:
        self.restore_active_sessions(reason="watcher_stop")
        self.running = False

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "active_count": len(self._active_by_id),
            "tracked_optimization_sessions": len(self._optimization_sessions),
            "last_detected": self.last_detected,
            "last_event": self.last_event,
            "last_optimization_action": self.last_optimization_action,
            "matching_strategy": {
                "name": "scored_executable_path_title",
                "confidence_threshold": 0.62,
                "ignored_processes_count": len(KNOWN_IGNORED_PROCESS_NAMES),
                "optimization_delay_seconds": self.optimization_delay_seconds,
            },
        }
