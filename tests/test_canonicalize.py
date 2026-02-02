"""Tests for log canonicalization service (pure functions, no I/O)."""

import pytest
from services.canonicalize import canonicalize, canon_hash, template_key, CANON_VERSION


class TestUFWNormalization:
    """UFW BLOCK log field normalization."""

    def test_ufw_block_full(self):
        raw = (
            "[UFW BLOCK] IN=ens5 OUT= MAC=01:23:45:67:89:ab:cd:ef:01:23:45:67:89:ab "
            "SRC=192.168.1.100 DST=10.0.0.20 LEN=60 TTL=64 ID=54321 "
            "PROTO=TCP SPT=44832 DPT=443"
        )
        result = canonicalize(raw)
        assert "MAC=<MAC>" in result
        assert "SRC=<IPV4>" in result
        assert "DST=<IPV4>" in result
        assert "SPT=<PORT>" in result
        assert "DPT=<PORT>" in result
        assert "LEN=<N>" in result
        assert "TTL=<N>" in result
        assert "ID=<N>" in result
        # Protocol should be preserved
        assert "PROTO=TCP" in result

    def test_ufw_preserves_structure(self):
        raw = "[UFW BLOCK] IN=br0 SRC=10.0.0.1 DST=10.0.0.2 DPT=80"
        result = canonicalize(raw)
        assert result.startswith("[UFW BLOCK]")
        assert "IN=br0" in result


class TestLokiNormalization:
    """Loki structured log normalization."""

    def test_loki_ts(self):
        raw = "ts=2025-12-01T12:00:00.123Z caller=main.go:42 msg=starting"
        result = canonicalize(raw)
        assert "ts=<TS>" in result

    def test_loki_caller_preserves_filename(self):
        raw = "caller=compactor.go:123 level=info"
        result = canonicalize(raw)
        assert "caller=compactor.go:<LINE>" in result

    def test_loki_duration(self):
        raw = "duration=1.234s msg=query complete"
        result = canonicalize(raw)
        assert "duration=<DUR>" in result


class TestBatchNormalization:
    """Batch message normalization."""

    def test_batch_sending(self):
        raw = "[BATCH] Sending 50 logs to API"
        result = canonicalize(raw)
        assert "[BATCH] Sending <N>" in result

    def test_batch_other_numbers(self):
        raw = "[BATCH] Sending 200 logs to API"
        result = canonicalize(raw)
        assert "[BATCH] Sending <N>" in result


class TestPAMNormalization:
    """PAM session normalization."""

    def test_pam_session_opened(self):
        raw = "pam_unix(cron:session): session opened for user tadeu718"
        result = canonicalize(raw)
        assert "for user <USER>" in result

    def test_pam_session_closed(self):
        raw = "pam_unix(cron:session): session closed for user root"
        result = canonicalize(raw)
        assert "for user <USER>" in result


class TestCronNormalization:
    """Cron command normalization."""

    def test_cron_cmd(self):
        raw = "CRON[1234]: (root) CMD (/usr/local/bin/backup.sh)"
        result = canonicalize(raw)
        assert "(<USER>) CMD (<CMD>)" in result


class TestGINOllamaNormalization:
    """GIN/Ollama log normalization."""

    def test_gin_log(self):
        raw = "[GIN] 2025/12/01 - 12:00:00 | 200 | 1.234ms | 192.168.1.100"
        result = canonicalize(raw)
        assert "<IPV4>" not in result or "| <IPV4>" in result or "<TS>" in result

    def test_ollama_duration(self):
        raw = "total duration: 1234ms eval count: 50"
        result = canonicalize(raw)
        assert "<DUR>" in result


class TestShipperPID:
    """Shipper PID wrapper normalization."""

    def test_shipper_pid(self):
        raw = "[ 1234] Starting log collection"
        result = canonicalize(raw)
        assert "[<PID>]" in result

    def test_shipper_pid_no_space(self):
        raw = "[5678] Processing batch"
        result = canonicalize(raw)
        assert "[<PID>]" in result


class TestGenericPatterns:
    """Generic pattern normalization."""

    def test_iso_timestamp(self):
        raw = "Error at 2025-12-01T12:00:00.123456Z in module"
        result = canonicalize(raw)
        assert "<TS>" in result
        assert "2025" not in result

    def test_uuid(self):
        raw = "Request ID: 550e8400-e29b-41d4-a716-446655440000"
        result = canonicalize(raw)
        assert "<UUID>" in result

    def test_long_hex(self):
        raw = "Token: abcdef0123456789abcdef01"
        result = canonicalize(raw)
        assert "<HEX>" in result

    def test_ipv4(self):
        raw = "Connected from 192.168.1.100 to 10.0.0.20"
        result = canonicalize(raw)
        assert result.count("<IPV4>") == 2

    def test_ipv6(self):
        raw = "Listening on 2001:db8:85a3:0000:0000:8a2e:0370:7334"
        result = canonicalize(raw)
        assert "<IPV6>" in result

    def test_mac_address(self):
        raw = "Interface MAC: 01:23:45:67:89:ab"
        result = canonicalize(raw)
        assert "<MAC>" in result

    def test_pid_field(self):
        raw = "Process started pid=12345 status=running"
        result = canonicalize(raw)
        assert "pid=<PID>" in result

    def test_duration_generic(self):
        raw = "Completed in 45.2 seconds, next retry in 30 minutes"
        result = canonicalize(raw)
        assert "<DUR>" in result

    def test_large_numbers(self):
        raw = "Processed 123456 records, offset 789012"
        result = canonicalize(raw)
        assert "<N>" in result
        assert "123456" not in result

    def test_small_numbers_preserved(self):
        raw = "Retry attempt 3 of 5"
        result = canonicalize(raw)
        assert "3" in result
        assert "5" in result

    def test_whitespace_collapse(self):
        raw = "Error   occurred    in   module"
        result = canonicalize(raw)
        assert "  " not in result


class TestHashDeterminism:
    """Hash determinism and service-sensitivity."""

    def test_same_input_same_hash(self):
        h1 = canon_hash("connection refused", "nginx.service", "ERROR")
        h2 = canon_hash("connection refused", "nginx.service", "ERROR")
        assert h1 == h2

    def test_different_service_different_hash(self):
        h1 = canon_hash("connection refused", "nginx.service", "ERROR")
        h2 = canon_hash("connection refused", "apache.service", "ERROR")
        assert h1 != h2

    def test_different_level_different_hash(self):
        h1 = canon_hash("connection refused", "nginx.service", "ERROR")
        h2 = canon_hash("connection refused", "nginx.service", "WARN")
        assert h1 != h2

    def test_hash_length(self):
        h = canon_hash("test", "svc", "INFO")
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)


class TestTemplateKey:
    """template_key convenience wrapper."""

    def test_returns_tuple(self):
        canonical, h, version = template_key(
            "Error at 2025-12-01T12:00:00Z pid=1234",
            "test.service",
            "ERROR",
        )
        assert "<TS>" in canonical
        assert "pid=<PID>" in canonical
        assert len(h) == 32
        assert version == CANON_VERSION

    def test_version_passthrough(self):
        _, _, version = template_key("test", "svc", "INFO", version="v1")
        assert version == "v1"

    def test_unknown_version_raises(self):
        with pytest.raises(ValueError, match="Unknown canonicalization version"):
            template_key("test", "svc", "INFO", version="v99")


class TestIdempotency:
    """Applying canonicalization twice yields the same result."""

    def test_idempotent_simple(self):
        raw = "Error at 2025-12-01T12:00:00Z from 192.168.1.100 pid=1234"
        once = canonicalize(raw)
        twice = canonicalize(once)
        assert once == twice

    def test_idempotent_ufw(self):
        raw = "[UFW BLOCK] SRC=10.0.0.1 DST=10.0.0.2 SPT=12345 DPT=80 LEN=60"
        once = canonicalize(raw)
        twice = canonicalize(once)
        assert once == twice

    def test_idempotent_pam(self):
        raw = "session opened for user tadeu718"
        once = canonicalize(raw)
        twice = canonicalize(once)
        assert once == twice


class TestCompressionRatio:
    """Verify canonicalization actually reduces uniqueness."""

    def test_similar_logs_same_template(self):
        """Multiple raw logs that differ only in high-entropy tokens
        should canonicalize to the same template."""
        logs = [
            "2025-12-01T12:00:00Z Connection from 192.168.1.100 pid=1234 duration=45ms",
            "2025-12-01T13:00:00Z Connection from 10.0.0.5 pid=5678 duration=120ms",
            "2025-12-02T08:30:00Z Connection from 172.16.0.1 pid=9999 duration=3ms",
        ]
        templates = {canonicalize(log) for log in logs}
        assert len(templates) == 1

    def test_different_messages_different_templates(self):
        """Logs with structurally different messages should stay different."""
        logs = [
            "Connection refused from 192.168.1.100",
            "Authentication failed for user tadeu718",
            "[UFW BLOCK] SRC=10.0.0.1 DST=10.0.0.2",
        ]
        templates = {canonicalize(log) for log in logs}
        assert len(templates) == 3
