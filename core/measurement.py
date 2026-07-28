from __future__ import annotations
from dataclasses import dataclass, field
from statistics import mean
from typing import List, Optional

# Upload Speed grading thresholds (kB/s)
SPEED_GRADES = [(100, "Excellent"), (80, "Good"), (50, "Moderate"), (0, "Poor")]
MAX_MEASUREMENTS  = 5
MAX_DURATION_SECS = 150   # 2 min 30 s


def grade_speed(v: Optional[float]) -> str:
    if v is None:
        return "--"
    for threshold, label in SPEED_GRADES:
        if v >= threshold:
            return label
    return "Poor"


def grade_rsrp(v: Optional[int]) -> str:
    if v is None: return "--"
    if v >= -80:  return "Excellent"
    if v >= -95:  return "Good"
    if v >= -110: return "Moderate"
    return "Poor"


def grade_rsrq(v: Optional[int]) -> str:
    if v is None: return "--"
    if v >= -10:  return "Excellent"
    if v >= -15:  return "Good"
    if v >= -20:  return "Moderate"
    return "Poor"


@dataclass
class Sample:
    timestamp: str
    elapsed:   str
    connected: bool
    rssi:  Optional[int]   = None
    rsrp:  Optional[int]   = None
    rsrq:  Optional[int]   = None
    sinr:  Optional[int]   = None
    speed: Optional[float] = None
    lac:              Optional[str] = None
    cellid:           Optional[str] = None
    access_technology:Optional[str] = None
    sm_2g:            Optional[str] = None
    mccmnc:           Optional[str] = None
    band:             Optional[str] = None
    channel_type:     Optional[str] = None
    grade: str = "--"


@dataclass
class ConnectionEvent:
    kind:      str   # "Disconnected" or "Reconnected"
    timestamp: str
    elapsed:   str


@dataclass
class SensorStore:
    samples: List[Sample]         = field(default_factory=list)
    events:  List[ConnectionEvent]= field(default_factory=list)

    def add_sample(self, s: Sample) -> None:
        self.samples.append(s)

    def add_event(self, kind: str, ts: str, elapsed: str) -> None:
        self.events.append(ConnectionEvent(kind, ts, elapsed))

    def clear(self) -> None:
        self.samples.clear()
        self.events.clear()

    @property
    def count(self) -> int:
        return len(self.samples)

    def _speeds(self) -> List[float]:
        return [s.speed for s in self.samples if s.speed is not None]

    def final_grade(self) -> tuple[str, Optional[float]]:
        """Mean of top-3 upload speeds."""
        top = sorted(self._speeds(), reverse=True)[:3]
        if not top:
            return "--", None
        m = round(mean(top), 2)
        return grade_speed(m), m

    def overall_grade(self) -> tuple[str, Optional[float]]:
        """Mean of all upload speeds."""
        speeds = self._speeds()
        if not speeds:
            return "--", None
        m = round(mean(speeds), 2)
        return grade_speed(m), m
