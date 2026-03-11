"""Tests for Docker manager security and lifecycle."""

from __future__ import annotations

import pytest

from backend.docker_manager import _validate_name


class TestDockerManagerSecurity:
    """Verify docker_manager input validation and security flags."""

    def test_validate_name_accepts_valid(self):
        _validate_name("cua-environment", "container")
        _validate_name("cua-ubuntu:latest", "image")
        _validate_name("my_container.v2", "label")

    def test_validate_name_rejects_empty(self):
        with pytest.raises(ValueError):
            _validate_name("", "container")

    def test_validate_name_rejects_metacharacters(self):
        with pytest.raises(ValueError):
            _validate_name("name; rm -rf /", "container")

    def test_validate_name_rejects_spaces(self):
        with pytest.raises(ValueError):
            _validate_name("name with spaces", "container")

    def test_validate_name_rejects_long_names(self):
        with pytest.raises(ValueError):
            _validate_name("a" * 200, "container")

    def test_validate_name_rejects_leading_special(self):
        with pytest.raises(ValueError):
            _validate_name(".hidden", "container")
        with pytest.raises(ValueError):
            _validate_name("-dash", "container")

    def test_start_container_args_have_security_flags(self):
        """Verify the source code includes --security-opt and resource limits."""
        import inspect
        from backend.docker_manager import start_container
        source = inspect.getsource(start_container)
        assert "--security-opt=no-new-privileges:true" in source
        assert "--memory=4g" in source
        assert "--cpus=2" in source
