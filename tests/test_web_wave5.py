"""Mastermind feedback scoring (the coin path). NBA-sim settlement moved to test_web_sim.py
when the five per-sport sims were unified into web/sim.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web.g_mastermind as mm  # noqa: E402


def test_mastermind_score():
    assert mm._score(["red", "red", "green", "green"], ["red", "red", "green", "green"]) == (4, 0)
    assert mm._score(["red", "red", "green", "green"], ["green", "green", "red", "red"]) == (0, 4)
    assert mm._score(["red", "blue", "green", "green"], ["red", "red", "red", "red"]) == (1, 0)  # no double-count
