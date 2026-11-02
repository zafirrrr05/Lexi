"""Caller voices (C11 step 3, assumption V4): Timbre voices x speaking rate x pitch. S3 NEW_SPEAKERS uses a disjoint set."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    name: str
    rate: float  # speaking rate multiplier, 0.85-1.25
    pitch_st: float  # semitones, +-2


_TRAIN_NAMES = ("Nalini", "Deepak")
_UNSEEN_NAMES = ("Kavya", "Arjun")  # never used in Train or Calib


def train_voices() -> list[Voice]:
    return [Voice(n, r, p) for n in _TRAIN_NAMES for r in (0.85, 1.0, 1.15) for p in (-2.0, 0.0, 2.0)]


def unseen_voices() -> list[Voice]:
    return [Voice(n, r, p) for n in _UNSEEN_NAMES for r in (0.92, 1.08, 1.22) for p in (-1.0, 1.0)]
