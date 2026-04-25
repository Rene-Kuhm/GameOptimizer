from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import system


class FakeNvidiaProvider:
    source = "nvml"

    def is_available(self) -> tuple[bool, str]:
        return True, "ok"

    def collect(self) -> system.ProviderOutput:
        return system.ProviderOutput(
            [
                {
                    "name": "NVIDIA GeForce RTX 4060 Laptop GPU",
                    "vendor": "nvidia",
                    "utilization_percent": 33.0,
                    "telemetry_backend": {"backend": "nvml"},
                }
            ],
            "sampled",
        )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("NVIDIA GeForce RTX 4060", "nvidia"),
        ("AMD Radeon RX 7800 XT", "amd"),
        ("Intel(R) UHD Graphics", "intel"),
        ("Microsoft Basic Display Adapter", "unknown"),
    ],
)
def test_gpu_vendor_detection(name: str, expected: str) -> None:
    assert system._gpu_vendor(name) == expected


def test_native_provider_keeps_dual_gpu_wmi_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system, "_provider_chain", lambda: [FakeNvidiaProvider()])
    monkeypatch.setattr(
        system,
        "_wmi_video_controllers",
        lambda: [
            SimpleNamespace(Name="Intel(R) UHD Graphics", DriverVersion="31", AdapterRAM=1024),
            SimpleNamespace(Name="NVIDIA GeForce RTX 4060 Laptop GPU", DriverVersion="555", AdapterRAM=8192),
        ],
    )

    source, confidence, _reason, gpu_list, notes, _source_note = system._select_gpu_telemetry()

    assert source == "nvml"
    assert confidence == pytest.approx(0.95)
    assert notes[-1] == "nvml: sampled"
    assert [gpu["vendor"] for gpu in gpu_list] == ["nvidia", "intel"]
    intel = gpu_list[1]
    assert intel["telemetry_backend"]["backend"] == "wmi_video_controller"
    assert intel["utilization_percent"] is None


def test_unavailable_provider_reports_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyProvider:
        source = "wmi"

        def is_available(self) -> tuple[bool, str]:
            return True, "available"

        def collect(self) -> system.ProviderOutput:
            return system.ProviderOutput([], "empty")

    monkeypatch.setattr(system, "_provider_chain", lambda: [EmptyProvider()])

    source, confidence, reason, gpu_list, notes, _source_note = system._select_gpu_telemetry()

    assert source == "unavailable"
    assert confidence == pytest.approx(0.05)
    assert gpu_list == []
    assert "wmi: empty" in notes
    assert "No GPU telemetry" in reason
