from __future__ import annotations

import os
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from db_migrator.config.models import AppConfig, SshAuthenticationType, SshTunnelConfig


class TunnelError(RuntimeError):
    pass


class ManagedTunnel(Protocol):
    local_bind_host: str
    local_bind_port: int

    def start(self) -> None:
        """Open the SSH tunnel."""

    def stop(self) -> None:
        """Close the SSH tunnel."""


class TunnelFactory(Protocol):
    def create(self, *, label: str, endpoint_host: str, endpoint_port: int, config: SshTunnelConfig) -> ManagedTunnel:
        """Create a managed tunnel for one DB endpoint."""


@dataclass(frozen=True)
class ResolvedAppConfig:
    original: AppConfig
    resolved: AppConfig


class TunnelManager:
    def __init__(self, tunnel_factory: TunnelFactory | None = None) -> None:
        self._tunnel_factory = tunnel_factory or SshTunnelFactory()

    def open(self, app_config: AppConfig, *, include_source: bool = True, include_target: bool = True) -> TunnelSession:
        return TunnelSession(app_config, self._tunnel_factory, include_source=include_source, include_target=include_target)


class TunnelSession:
    def __init__(self, app_config: AppConfig, tunnel_factory: TunnelFactory, *, include_source: bool, include_target: bool) -> None:
        self._original = app_config
        self._resolved = app_config.model_copy(deep=True)
        self._tunnel_factory = tunnel_factory
        self._include_source = include_source
        self._include_target = include_target
        self._stack = ExitStack()

    def __enter__(self) -> ResolvedAppConfig:
        if self._include_source:
            source_tunnel = self._open_tunnel(
                label="source",
                endpoint_host=self._original.source.tunnel.remote_host or self._original.source.host,
                endpoint_port=self._original.source.tunnel.remote_port or self._original.source.port,
                config=self._original.source.tunnel,
            )
            if source_tunnel is not None:
                self._resolved.source.host = source_tunnel.local_bind_host
                self._resolved.source.port = source_tunnel.local_bind_port

        if self._include_target:
            target_tunnel = self._open_tunnel(
                label="target",
                endpoint_host=self._original.target.tunnel.remote_host or self._original.target.host,
                endpoint_port=self._original.target.tunnel.remote_port or self._original.target.port,
                config=self._original.target.tunnel,
            )
            if target_tunnel is not None:
                self._resolved.target.host = target_tunnel.local_bind_host
                self._resolved.target.port = target_tunnel.local_bind_port

        return ResolvedAppConfig(original=self._original, resolved=self._resolved)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stack.close()

    def _open_tunnel(
        self,
        *,
        label: str,
        endpoint_host: str,
        endpoint_port: int,
        config: SshTunnelConfig,
    ) -> ManagedTunnel | None:
        if not config.enabled:
            return None
        _validate_tunnel_config(label, config)
        tunnel = self._tunnel_factory.create(
            label=label,
            endpoint_host=endpoint_host,
            endpoint_port=endpoint_port,
            config=config,
        )
        try:
            tunnel.start()
        except TunnelError:
            raise
        except Exception as exc:
            raise TunnelError(f"{label} SSH tunnel failed to start: {exc}") from exc
        self._stack.callback(tunnel.stop)
        return tunnel


class SshTunnelFactory:
    def create(self, *, label: str, endpoint_host: str, endpoint_port: int, config: SshTunnelConfig) -> ManagedTunnel:
        return SshTunnel(label=label, endpoint_host=endpoint_host, endpoint_port=endpoint_port, config=config)


class SshConnectionTester:
    def test(self, *, label: str, config: SshTunnelConfig) -> None:
        _validate_tunnel_config(label, config)
        try:
            import paramiko
        except ImportError as exc:
            raise TunnelError("paramiko is not installed. Install project dependencies first.") from exc

        assert config.ssh_host is not None
        assert config.ssh_user is not None
        client = paramiko.SSHClient()
        client.get_host_keys().load(str(_known_hosts_path(config)))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(
                hostname=config.ssh_host,
                port=config.ssh_port,
                username=config.ssh_user,
                password=_ssh_password(config),
                key_filename=config.private_key_path if config.auth_type is SshAuthenticationType.KEY else None,
                passphrase=_private_key_passphrase(config),
                timeout=10.0,
                banner_timeout=10.0,
                auth_timeout=10.0,
                look_for_keys=False,
                allow_agent=False,
            )
            _set_ssh_keepalive(client, config.keepalive_interval_seconds)
        except Exception as exc:
            raise TunnelError(f"{label} SSH connection failed: {_safe_tunnel_error(exc)}") from exc
        finally:
            client.close()


class SshTunnel:
    def __init__(self, *, label: str, endpoint_host: str, endpoint_port: int, config: SshTunnelConfig) -> None:
        self._label = label
        self._endpoint_host = endpoint_host
        self._endpoint_port = endpoint_port
        self._config = config
        self._forwarder: Any | None = None
        self.local_bind_host = config.local_host
        self.local_bind_port = config.local_port

    def start(self) -> None:
        try:
            from sshtunnel import BaseSSHTunnelForwarderError, HandlerSSHTunnelForwarderError, SSHTunnelForwarder
        except ImportError as exc:
            raise TunnelError("sshtunnel is not installed. Install project dependencies first.") from exc

        ssh_host_key = _load_ssh_host_key(self._label, self._config)
        passphrase = _private_key_passphrase(self._config)
        try:
            self._forwarder = SSHTunnelForwarder(
                (self._config.ssh_host, self._config.ssh_port),
                ssh_username=self._config.ssh_user,
                ssh_password=_ssh_password(self._config),
                ssh_pkey=self._config.private_key_path if self._config.auth_type is SshAuthenticationType.KEY else None,
                ssh_private_key_password=passphrase,
                ssh_host_key=ssh_host_key,
                remote_bind_address=(self._endpoint_host, self._endpoint_port),
                local_bind_address=(self._config.local_host, self._config.local_port),
                set_keepalive=self._config.keepalive_interval_seconds,
            )
            self._forwarder.start()
        except (BaseSSHTunnelForwarderError, HandlerSSHTunnelForwarderError) as exc:
            raise TunnelError(f"{self._label} SSH tunnel failed: {_safe_tunnel_error(exc)}") from exc
        except OSError as exc:
            raise TunnelError(f"{self._label} SSH tunnel local bind failed: {_safe_tunnel_error(exc)}") from exc
        except Exception as exc:
            raise TunnelError(f"{self._label} SSH tunnel failed: {_safe_tunnel_error(exc)}") from exc

        self.local_bind_host = self._forwarder.local_bind_host
        self.local_bind_port = int(self._forwarder.local_bind_port)

    def stop(self) -> None:
        if self._forwarder is not None:
            self._forwarder.stop()
            self._forwarder = None


def _validate_tunnel_config(label: str, config: SshTunnelConfig) -> None:
    missing = [
        field_name
        for field_name in ("ssh_host", "ssh_user")
        if not getattr(config, field_name)
    ]
    if config.auth_type is SshAuthenticationType.KEY and not config.private_key_path:
        missing.append("private_key_path")
    if config.auth_type is SshAuthenticationType.PASSWORD and not config.ssh_password:
        missing.append("ssh_password")
    if missing:
        raise TunnelError(f"{label} SSH tunnel config is missing: {', '.join(missing)}")
    if config.auth_type is SshAuthenticationType.KEY and config.private_key_path is not None and not Path(config.private_key_path).expanduser().exists():
        raise TunnelError(f"{label} SSH private key file does not exist: {config.private_key_path}")
    known_hosts_path = _known_hosts_path(config)
    if not known_hosts_path.exists():
        raise TunnelError(f"{label} SSH known_hosts file does not exist: {known_hosts_path}")


def _load_ssh_host_key(label: str, config: SshTunnelConfig) -> Any:
    try:
        import paramiko
    except ImportError as exc:
        raise TunnelError("paramiko is not installed. Install project dependencies first.") from exc

    assert config.ssh_host is not None
    host_keys = paramiko.HostKeys(str(_known_hosts_path(config)))
    host_key_name = _host_key_name(config.ssh_host, config.ssh_port)
    host_entry = host_keys.lookup(host_key_name) or host_keys.lookup(config.ssh_host)
    if host_entry is None:
        raise TunnelError(f"{label} SSH known_hosts does not contain host key for {host_key_name}.")
    return next(iter(host_entry.values()))


def _host_key_name(ssh_host: str, ssh_port: int) -> str:
    if ssh_port == 22:
        return ssh_host
    return f"[{ssh_host}]:{ssh_port}"


def _private_key_passphrase(config: SshTunnelConfig) -> str | None:
    if config.auth_type is not SshAuthenticationType.KEY or config.private_key_passphrase_env is None:
        return None
    return os.environ.get(config.private_key_passphrase_env)


def _ssh_password(config: SshTunnelConfig) -> str | None:
    if config.auth_type is not SshAuthenticationType.PASSWORD:
        return None
    return config.ssh_password


def _known_hosts_path(config: SshTunnelConfig) -> Path:
    return Path(config.known_hosts_path).expanduser() if config.known_hosts_path else Path.home() / ".ssh" / "known_hosts"


def _set_ssh_keepalive(client: Any, interval_seconds: float) -> None:
    transport = client.get_transport()
    if transport is not None:
        transport.set_keepalive(interval_seconds)


def _safe_tunnel_error(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__
