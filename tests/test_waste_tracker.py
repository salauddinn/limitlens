#!/usr/bin/env python3
"""Tests for limitlens waste tracker."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from limitlens import waste_tracker as wt


class TestWasteTracker(unittest.TestCase):
    def test_reset_event_on_large_pct_refill(self):
        prev = {"pct_left": 20.0, "reset_at": 1000}
        curr = {"pct_left": 80.0, "reset_at": 1000}
        self.assertTrue(wt._is_reset_event(prev, curr))

    def test_reset_event_when_deadline_passed_and_reset_time_moves_forward(self):
        prev = {"pct_left": 100.0, "reset_at": 1000}
        curr = {"pct_left": 100.0, "reset_at": 2000, "ts": 1100}
        self.assertTrue(wt._is_reset_event(prev, curr))

    def test_not_reset_event_for_small_reset_time_shift_before_deadline(self):
        prev = {"pct_left": 80.0, "reset_at": 1000}
        curr = {"pct_left": 70.0, "reset_at": 1120, "ts": 900}
        self.assertFalse(wt._is_reset_event(prev, curr))


if __name__ == "__main__":
    unittest.main()
