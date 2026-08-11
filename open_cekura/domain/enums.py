"""Versioned enum values shared by OpenCekura domain contracts."""

from enum import Enum


class AdapterKind(str, Enum):
    MOCK = "mock"
    HTTP = "http"


class Verbosity(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class CampaignStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


class RunFinalState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class TurnRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class EvaluationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    ERROR = "error"
    SKIPPED = "skipped"


class GateDecision(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


__all__ = [
    "AdapterKind",
    "CampaignStatus",
    "EvaluationStatus",
    "GateDecision",
    "RunFinalState",
    "TurnRole",
    "Verbosity",
]
