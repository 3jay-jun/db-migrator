from __future__ import annotations

from pathlib import Path

import pytest

from db_migrator.config.models import AppConfig
from db_migrator.connection import TunnelError, TunnelManager


def test_tunnel_manager_resolves_source_and_target_endpoints(tmp_path: Path) -> None:
    key_file, known_hosts = _tunnel_files(tmp_path)
    config = AppConfig.model_validate(
        {
            "source": {
                "host": "10.0.1.10",
                "port": 5432,
                "tunnel": _tunnel_config(key_file, known_hosts),
            },
            "target": {
                "host": "10.0.2.20",
                "port": 3306,
                "tunnel": _tunnel_config(key_file, known_hosts),
            },
        }
    )
    factory = FakeTunnelFactory()

    with TunnelManager(factory).open(config) as resolved:
        assert resolved.original.source.host == "10.0.1.10"
        assert resolved.resolved.source.host == "127.0.0.1"
        assert resolved.resolved.source.port == 15000
        assert resolved.resolved.target.host == "127.0.0.1"
        assert resolved.resolved.target.port == 15001
        assert [tunnel.started for tunnel in factory.tunnels] == [True, True]

    assert [tunnel.stopped for tunnel in factory.tunnels] == [True, True]


def test_tunnel_manager_can_open_only_source_tunnel(tmp_path: Path) -> None:
    key_file, known_hosts = _tunnel_files(tmp_path)
    config = AppConfig.model_validate(
        {
            "source": {"tunnel": _tunnel_config(key_file, known_hosts)},
            "target": {"tunnel": _tunnel_config(key_file, known_hosts)},
        }
    )
    factory = FakeTunnelFactory()

    with TunnelManager(factory).open(config, include_source=True, include_target=False) as resolved:
        assert resolved.resolved.source.port == 15000
        assert resolved.resolved.target.port == 3306

    assert [tunnel.label for tunnel in factory.tunnels] == ["source"]


def test_tunnel_manager_reports_missing_key_file(tmp_path: Path) -> None:
    _key_file, known_hosts = _tunnel_files(tmp_path)
    config = AppConfig.model_validate(
        {
            "source": {
                "tunnel": _tunnel_config(tmp_path / "missing.pem", known_hosts),
            }
        }
    )

    with pytest.raises(TunnelError, match="private key file does not exist"):
        with TunnelManager(FakeTunnelFactory()).open(config, include_source=True, include_target=False):
            pass


def test_tunnel_manager_accepts_password_auth_without_key_file(tmp_path: Path) -> None:
    _key_file, known_hosts = _tunnel_files(tmp_path)
    config = AppConfig.model_validate(
        {
            "source": {
                "tunnel": {
                    "enabled": True,
                    "ssh_host": "ec2.example.com",
                    "ssh_user": "ec2-user",
                    "auth_type": "password",
                    "ssh_password": "ssh-secret",
                    "known_hosts_path": str(known_hosts),
                },
            }
        }
    )
    factory = FakeTunnelFactory()

    with TunnelManager(factory).open(config, include_source=True, include_target=False) as resolved:
        assert resolved.resolved.source.host == "127.0.0.1"
        assert resolved.resolved.source.port == 15000

    assert factory.tunnels[0].started is True


def test_tunnel_manager_uses_explicit_remote_endpoint(tmp_path: Path) -> None:
    key_file, known_hosts = _tunnel_files(tmp_path)
    config = AppConfig.model_validate(
        {
            "source": {
                "host": "127.0.0.1",
                "port": 15433,
                "tunnel": {
                    **_tunnel_config(key_file, known_hosts),
                    "remote_host": "127.0.0.1",
                    "remote_port": 5432,
                    "local_port": 15433,
                },
            }
        }
    )
    factory = FakeTunnelFactory()

    with TunnelManager(factory).open(config, include_source=True, include_target=False) as resolved:
        assert resolved.resolved.source.host == "127.0.0.1"
        assert resolved.resolved.source.port == 15000

    assert factory.tunnels[0].endpoint_host == "127.0.0.1"
    assert factory.tunnels[0].endpoint_port == 5432


def test_tunnel_manager_preserves_keepalive_config(tmp_path: Path) -> None:
    key_file, known_hosts = _tunnel_files(tmp_path)
    config = AppConfig.model_validate(
        {
            "source": {
                "tunnel": {
                    **_tunnel_config(key_file, known_hosts),
                    "keepalive_interval_seconds": 15,
                },
            }
        }
    )
    factory = FakeTunnelFactory()

    with TunnelManager(factory).open(config, include_source=True, include_target=False):
        pass

    assert factory.tunnels[0].config.keepalive_interval_seconds == 15


class FakeTunnelFactory:
    def __init__(self) -> None:
        self.tunnels: list[FakeTunnel] = []

    def create(self, *, label: str, endpoint_host: str, endpoint_port: int, config):
        tunnel = FakeTunnel(label=label, endpoint_host=endpoint_host, endpoint_port=endpoint_port, local_port=15000 + len(self.tunnels), config=config)
        self.tunnels.append(tunnel)
        return tunnel


class FakeTunnel:
    def __init__(self, *, label: str, endpoint_host: str, endpoint_port: int, local_port: int, config) -> None:
        self.label = label
        self.endpoint_host = endpoint_host
        self.endpoint_port = endpoint_port
        self.config = config
        self.local_bind_host = "127.0.0.1"
        self.local_bind_port = local_port
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def _tunnel_files(tmp_path: Path) -> tuple[Path, Path]:
    key_file = tmp_path / "service.pem"
    key_file.write_text("fake key", encoding="utf-8")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fake known hosts", encoding="utf-8")
    return key_file, known_hosts


def _tunnel_config(key_file: Path, known_hosts: Path) -> dict[str, object]:
    return {
        "enabled": True,
        "ssh_host": "ec2.example.com",
        "ssh_user": "ec2-user",
        "private_key_path": str(key_file),
        "known_hosts_path": str(known_hosts),
        "local_port": 0,
    }
