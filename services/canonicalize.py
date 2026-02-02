"""
DevMesh Platform - Log Canonicalization Service

Pure-function text normalizer that converts raw log messages into canonical
templates by replacing high-entropy tokens (PIDs, IPs, timestamps, UUIDs, etc.)
with typed placeholders.

Versioned ruleset — when rules change, add CANON_RULES_V2 and bump CANON_VERSION.
Old versions stay callable for comparison and backfill targeting.

No I/O, no DB. Pure functions only.
"""

import re
import hashlib

CANON_VERSION = "v1"

# =============================================================================
# V1 Canonicalization Rules (applied in order, specific patterns first)
# =============================================================================

# 1. UFW BLOCK fields
_UFW_MAC = re.compile(r'\bMAC=([0-9a-fA-F]{2}:){5,}[0-9a-fA-F]{2}\b')
_UFW_SRC = re.compile(r'\bSRC=\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')
_UFW_DST = re.compile(r'\bDST=\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')
_UFW_SPT = re.compile(r'\bSPT=\d+\b')
_UFW_DPT = re.compile(r'\bDPT=\d+\b')
_UFW_LEN = re.compile(r'\bLEN=\d+\b')
_UFW_ID = re.compile(r'\bID=\d+\b')
_UFW_TTL = re.compile(r'\bTTL=\d+\b')

# 2. Loki structured logs
_LOKI_TS = re.compile(r'\bts=\S+')
_LOKI_CALLER = re.compile(r'\bcaller=(\w+\.go):\d+')
_LOKI_DURATION = re.compile(r'\bduration=\S+')

# 3. Batch messages
_BATCH_SENDING = re.compile(r'\[BATCH\] Sending \d+')

# 4. PAM sessions
_PAM_USER = re.compile(r'\bfor user \S+')

# 5. Cron
_CRON_CMD = re.compile(r'\((\w+)\) CMD \((.+?)\)')

# 6. GIN/Ollama patterns
_GIN_LOG = re.compile(
    r'\[GIN\]\s*\d{4}/\d{2}/\d{2}\s*-\s*\d{2}:\d{2}:\d{2}\s*\|\s*(\d+)\s*\|\s*[\d.]+[^\|]*\|\s*\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'
)
_OLLAMA_DURATION = re.compile(r'\b\d+(\.\d+)?(ms|s|m|h|us|ns)\b')

# 7. DevMesh API prefix timestamps (ISO-ish at start of line)
_API_PREFIX_TS = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*[Z]?\s*')

# 8. Shipper PID wrapper
_SHIPPER_PID = re.compile(r'\[\s*\d+\]')

# 9. Generic patterns (broadest — applied last)
_ISO_TIMESTAMP = re.compile(
    r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}([.\d]*)([+-]\d{2}:?\d{2}|Z)?'
)
_UUID = re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b')
_LONG_HEX = re.compile(r'\b[0-9a-fA-F]{16,}\b')
_IPV4 = re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')
_IPV6 = re.compile(r'\b([0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b')
_MAC_ADDR = re.compile(r'\b([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b')
_PID_FIELD = re.compile(r'\bpid=\d+\b')
_DURATION_GENERIC = re.compile(r'\b\d+(\.\d+)?\s*(ms|s|m|h|us|ns|seconds|minutes|hours)\b')
_LARGE_NUMBER = re.compile(r'\b\d{5,}\b')

# 10. Whitespace collapse
_MULTI_SPACE = re.compile(r'  +')


def _apply_v1_rules(text: str) -> str:
    """Apply v1 canonicalization rules in order."""

    # 1. UFW BLOCK fields (specific key=value patterns)
    text = _UFW_MAC.sub('MAC=<MAC>', text)
    text = _UFW_SRC.sub('SRC=<IPV4>', text)
    text = _UFW_DST.sub('DST=<IPV4>', text)
    text = _UFW_SPT.sub('SPT=<PORT>', text)
    text = _UFW_DPT.sub('DPT=<PORT>', text)
    text = _UFW_LEN.sub('LEN=<N>', text)
    text = _UFW_ID.sub('ID=<N>', text)
    text = _UFW_TTL.sub('TTL=<N>', text)

    # 2. Loki structured logs
    text = _LOKI_TS.sub('ts=<TS>', text)
    text = _LOKI_CALLER.sub(r'caller=\1:<LINE>', text)
    text = _LOKI_DURATION.sub('duration=<DUR>', text)

    # 3. Batch messages
    text = _BATCH_SENDING.sub('[BATCH] Sending <N>', text)

    # 4. PAM sessions
    text = _PAM_USER.sub('for user <USER>', text)

    # 5. Cron
    text = _CRON_CMD.sub('(<USER>) CMD (<CMD>)', text)

    # 6. GIN/Ollama
    text = _GIN_LOG.sub('[GIN] <TS> | \\1 | <DUR> | <IPV4>', text)
    # Apply Ollama duration after GIN to catch remaining durations
    text = _OLLAMA_DURATION.sub('<DUR>', text)

    # 7. DevMesh API prefix timestamps
    text = _API_PREFIX_TS.sub('<TS> ', text)

    # 8. Shipper PID wrapper
    text = _SHIPPER_PID.sub('[<PID>]', text)

    # 9. Generic patterns (broadest)
    text = _ISO_TIMESTAMP.sub('<TS>', text)
    text = _UUID.sub('<UUID>', text)
    text = _LONG_HEX.sub('<HEX>', text)
    text = _IPV4.sub('<IPV4>', text)
    text = _MAC_ADDR.sub('<MAC>', text)
    text = _IPV6.sub('<IPV6>', text)
    text = _PID_FIELD.sub('pid=<PID>', text)
    text = _DURATION_GENERIC.sub('<DUR>', text)
    text = _LARGE_NUMBER.sub('<N>', text)

    # 10. Collapse whitespace
    text = _MULTI_SPACE.sub(' ', text)
    text = text.strip()

    return text


# =============================================================================
# Public API
# =============================================================================

def canonicalize(text: str, version: str = "v1") -> str:
    """Canonicalize a raw log message using the specified rule version.

    Args:
        text: Raw log message
        version: Rule version to apply (default "v1")

    Returns:
        Canonicalized text with high-entropy tokens replaced by placeholders.
    """
    if version == "v1":
        return _apply_v1_rules(text)
    raise ValueError(f"Unknown canonicalization version: {version}")


def canon_hash(canonical_text: str, service: str, level: str) -> str:
    """Compute a 32-char SHA256 hash for template deduplication.

    Includes service and level so identical text from different services
    gets separate templates.

    Args:
        canonical_text: Already-canonicalized text
        service: Service name
        level: Log level

    Returns:
        32-character hex digest.
    """
    content = f"{service}|{level}|{canonical_text}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def template_key(raw_message: str, service: str, level: str, version: str = "v1"):
    """Convenience wrapper: canonicalize + hash in one call.

    Args:
        raw_message: Raw log message
        service: Service name
        level: Log level
        version: Canonicalization rule version

    Returns:
        Tuple of (canonical_text, template_hash, canon_version)
    """
    canonical = canonicalize(raw_message, version=version)
    h = canon_hash(canonical, service, level)
    return (canonical, h, version)
