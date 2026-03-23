from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from .executable_meta import get_executable_metadata_cache
from .models import GameEntry, WatcherEvent
from .optimizer import apply_profile


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


class GameWatcher:
    def __init__(self, poll_interval_seconds: int = 3) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._games: list[GameEntry] = []
        self._profiles: list[GameProfile] = []
        self._active_by_id: dict[str, ActiveGame] = {}
        self._metadata_cache = get_executable_metadata_cache()
        self.last_detected: dict[str, Any] | None = None
        self.last_event: dict[str, Any] | None = None
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

        score = min(1.0, score)
        matched = score >= 0.62
        reason = "+".join(reasons) if reasons else "no_match"
        return MatchResult(matched=matched, confidence=score, reason=reason, matched_via=reasons)

    def _iter_processes(self) -> list[ProcessSnapshot]:
        items: list[ProcessSnapshot] = []
        try:
            process_iter = psutil.process_iter(["pid", "name", "exe"])
        except Exception:
            return items

        for proc in process_iter:
            proc_name = (proc.info.get("name") or "").strip().lower()
            if not proc_name:
                continue
            executable = proc.info.get("exe")
            items.append(
                ProcessSnapshot(
                    pid=proc.pid,
                    name=proc_name,
                    executable_path=executable if isinstance(executable, str) else None,
                )
            )
        return items

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
            started = [item for gid, item in scanned.items() if gid not in self._active_by_id]
            stopped = [item for gid, item in self._active_by_id.items() if gid not in scanned]

            for game in started:
                optimize_result = apply_profile(game.process_name, profile=default_profile)
                event = WatcherEvent(
                    event="game_started",
                    game_id=game.game.id,
                    game_name=game.game.name,
                    process_name=game.process_name,
                    pid=game.pid,
                    timestamp=_now_iso(),
                    details={
                        "optimization": optimize_result,
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
                event = WatcherEvent(
                    event="game_stopped",
                    game_id=game.game.id,
                    game_name=game.game.name,
                    process_name=game.process_name,
                    pid=game.pid,
                    timestamp=_now_iso(),
                    details={
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

            self._active_by_id = scanned
            await asyncio.sleep(self.poll_interval_seconds)

    def stop(self) -> None:
        self.running = False

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "active_count": len(self._active_by_id),
            "last_detected": self.last_detected,
            "last_event": self.last_event,
            "matching_strategy": {
                "name": "scored_executable_path_title",
                "confidence_threshold": 0.62,
            },
        }
