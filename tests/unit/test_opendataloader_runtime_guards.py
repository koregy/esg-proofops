"""Runtime guards of the OpenDataLoader adapter: Java 21 identity and artifact path reach."""

from __future__ import annotations

import sys
from hashlib import sha256
from pathlib import Path

import pytest
from proofops.adapters.parsing import opendataloader as odl
from proofops.application.ingest.graph_fusion import ParserProfile, SourceArtifact


@pytest.mark.parametrize(
    "output",
    [
        'openjdk version "21" 2023-09-19\nOpenJDK Runtime Environment (build 21+35-2513)',
        'java version "21.0.2" 2024-01-16 LTS',
        'openjdk version "21.0.4" 2024-07-16',
        'openjdk version "21-ea" 2023-06-08',
        'openjdk version "21+35"',
    ],
)
def test_feature_release_21_is_accepted_including_the_bare_ga_string(output):
    assert odl.is_java_21(output)


@pytest.mark.parametrize(
    "output",
    [
        'openjdk version "22" 2024-03-19',
        'openjdk version "22.0.1"',
        'openjdk version "210"',
        'openjdk version "210.0.1"',
        'openjdk version "2.1"',
        'openjdk version "17.0.12"',
        'openjdk version "1.8.0_21"',
        "",
        "java: command not found",
    ],
)
def test_other_feature_releases_are_rejected(output):
    assert not odl.is_java_21(output)


def _final(root: Path) -> Path:
    return root / ("t" * 36) / ("v" * 36) / ("m" * 36)


def test_artifact_paths_are_unrestricted_off_windows_or_with_long_paths(tmp_path):
    deep = tmp_path / ("x" * 300)
    assert odl.artifact_paths_fit(_final(deep), platform="linux")
    assert odl.artifact_paths_fit(_final(deep), platform="darwin")
    assert odl.artifact_paths_fit(_final(deep), platform="win32", long_paths=True)


def test_windows_without_long_paths_needs_every_artifact_below_max_path(tmp_path):
    longest = max(map(len, odl._ARTIFACT_NAMES))
    base = len(str(Path(tmp_path).absolute() / _final(Path("")))) + 1
    fitting = tmp_path / ("a" * max(0, 259 - base - longest - 2))
    assert odl.artifact_paths_fit(_final(fitting), platform="win32", long_paths=False)
    # The observed failure: a 250-character manifest directory whose artifacts
    # reach 264-272 characters once the work directory is renamed into it.
    too_long = tmp_path / ("b" * (262 - base - longest))
    assert not odl.artifact_paths_fit(_final(too_long), platform="win32", long_paths=False)


@pytest.mark.skipif(sys.platform != "win32", reason="MAX_PATH applies to Windows only")
def test_parse_refuses_before_creating_anything_when_artifacts_would_be_unreachable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(odl, "_windows_long_paths_enabled", lambda: False)
    root = tmp_path / ("r" * max(1, 200 - len(str(tmp_path))))
    root.mkdir()
    content = b"%PDF-1.7 guard test"
    source = SourceArtifact(
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        "00000000-0000-4000-8000-000000000003",
        sha256(content).hexdigest(),
        "guard-object",
        content,
    )
    profile = ParserProfile("00000000-0000-4000-8000-000000000004")
    spawned = []
    monkeypatch.setattr(odl.shutil, "which", lambda *_a: spawned.append(_a) or None)
    with pytest.raises(odl.ParseFailure, match="PARSER_ARTIFACT_PATH_TOO_LONG"):
        odl.OpenDataLoaderParser(root).parse(source, profile, tenant_id=source.tenant_id)
    assert list(root.iterdir()) == [] and spawned == []

    # A short root passes the guard and reaches the next check unchanged.
    short = tmp_path / "s"
    short.mkdir()
    with pytest.raises(odl.ParseFailure, match="JAVA_21_REQUIRED"):
        odl.OpenDataLoaderParser(short).parse(source, profile, tenant_id=source.tenant_id)
