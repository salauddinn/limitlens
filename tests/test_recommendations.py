#!/usr/bin/env python3
"""Tests for limitlens recommendations engine."""

import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from limitlens.core import parse_to_utc, fmt_reset
from limitlens import recommendations as rec


def _print_c(text, color, no_color):
    print(text)


class TestRecommendations(unittest.TestCase):
    def test_amp_percentage_quota_is_a_candidate(self):
        candidates = rec._amp_candidates({
            "email": "user@example.com",
            "tiers": [{
                "label": "Amp Free",
                "remaining": None,
                "total": None,
                "pct_left": 96.0,
                "pct_used": 4.0,
                "reset": "today (resets daily)",
            }],
        })

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["headroom_pct"], 96.0)
        self.assertEqual(candidates[0]["reset_label"], "today (resets daily)")
        self.assertEqual(candidates[0]["note"], "Amp Free daily quota")

    def test_amp_percentage_quota_is_prepaid_not_free(self):
        candidates = rec._amp_candidates({
            "email": "user@example.com",
            "tiers": [{
                "label": "Amp Free",
                "remaining": None,
                "total": None,
                "pct_left": 96.0,
                "pct_used": 4.0,
                "reset": "today (resets daily)",
            }],
        })
        self.assertEqual(candidates[0]["cost_class"], "prepaid")

    def test_amp_percentage_quota_excluded_when_exhausted(self):
        candidates = rec._amp_candidates({
            "email": "user@example.com",
            "tiers": [{
                "label": "Amp Free",
                "remaining": None,
                "total": None,
                "pct_left": 0.0,
                "pct_used": 100.0,
                "reset": "today (resets daily)",
            }],
        })
        self.assertEqual(candidates, [])

    def test_display_suggestions_answers_which_ai_to_use(self):
        recs = {
            "hard": [{
                "name": "codex (weekly)",
                "headroom_pct": 82.0,
                "reset_label": "2 days left to reset",
                "note": "bottleneck: weekly",
            }],
            "quick": [{
                "name": "antigravity:main → Gemini Flash",
                "headroom_pct": 60.0,
                "reset_label": "tomorrow",
                "note": "model: Gemini Flash",
            }],
            "cli": [{
                "name": "amp (signed in)",
                "headroom_pct": 23.0,
                "reset_label": "replenishing",
                "note": "$1.15 pool, replenishing",
                "stale": True,
            }],
        }

        with unittest.mock.patch("sys.stdout") as mock_stdout:
            rec.display_suggestions(recs, SimpleNamespace(no_color=True, plain=True), _print_c)
            out = "".join(call.args[0] for call in mock_stdout.write.call_args_list if call.args)

        self.assertIn("AI suggestion", out)
        self.assertIn("Hard task", out)
        self.assertIn("Quick edit", out)
        self.assertIn("CLI work", out)
        self.assertIn("82% left", out)
        self.assertIn("2 days left to reset", out)
        self.assertIn("$1.15 pool", out)
        self.assertIn("stale data", out)

    def test_display_suggestions_empty_state(self):
        with unittest.mock.patch("sys.stdout") as mock_stdout:
            rec.display_suggestions({}, SimpleNamespace(no_color=True, plain=True), _print_c)
            out = "".join(call.args[0] for call in mock_stdout.write.call_args_list if call.args)

        self.assertIn("Hard task", out)
        self.assertIn("no usable option", out)

    def test_antigravity_cli_profile_is_cli_candidate(self):
        now = datetime.now(timezone.utc)
        result = {
            "antigravity": {
                "profiles": [
                    {
                        "name": "agy-cli",
                        "source": "cli",
                        "status": "running",
                        "models": [
                            {
                                "label": "Gemini Flash",
                                "pct_left": 95.0,
                                "reset_time": (now + timedelta(hours=2)).isoformat(),
                            },
                        ],
                    }
                ]
            }
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["cli"][0]
        self.assertEqual(top["name"], "antigravity:agy-cli → Gemini Flash")
        self.assertEqual(top["command"], "agy")
        self.assertEqual(top["surface"], "cli")

    def test_codex_recommendation_uses_bottleneck_window(self):
        now = datetime.now(timezone.utc)
        result = {
            "codex": {
                "accounts": [
                    {
                        "name": "acct",
                        "limits": [
                            {
                                "label": "5h window",
                                "left_percent": 12.0,
                                "reset_time": (now + timedelta(hours=1)).isoformat(),
                                "reset_time_fmt": "1 hour left to reset",
                            },
                            {
                                "label": "weekly",
                                "left_percent": 95.0,
                                "reset_time": (now + timedelta(days=5)).isoformat(),
                                "reset_time_fmt": "5 days left to reset",
                            },
                        ],
                    }
                ]
            }
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["hard"][0]
        self.assertEqual(top["name"], "codex-acct (5h window)")
        self.assertEqual(top["headroom_pct"], 12.0)
        self.assertEqual(top["note"], "bottleneck: 5h window")

    def test_custom_tool_recommendation(self):
        result = {
            "custom": {
                "tools": [
                    {
                        "id": "kilo",
                        "name": "Kilo Code",
                        "command": "use Kilo Code",
                        "surface": "ide",
                        "quality": "premium",
                        "cost_class": "prepaid",
                        "tiers": [
                            {
                                "label": "quota",
                                "remaining": 75,
                                "total": 100,
                                "pct_left": 75.0,
                            }
                        ],
                    }
                ]
            }
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["hard"][0]

        self.assertEqual(top["tool"], "custom")
        self.assertEqual(top["name"], "Kilo Code")
        self.assertEqual(top["command"], "use Kilo Code")

    def test_hard_recommendation_ignores_under_five_percent(self):
        now = datetime.now(timezone.utc)
        result = {
            "codex": {
                "accounts": [
                    {
                        "name": "low",
                        "limits": [
                            {
                                "label": "weekly",
                                "left_percent": 4.0,
                                "reset_time": (now + timedelta(hours=2)).isoformat(),
                                "reset_time_fmt": "2 hours left to reset",
                            },
                        ],
                    },
                    {
                        "name": "ok",
                        "limits": [
                            {
                                "label": "weekly",
                                "left_percent": 15.0,
                                "reset_time": (now + timedelta(hours=4)).isoformat(),
                                "reset_time_fmt": "4 hours left to reset",
                            },
                        ],
                    },
                ]
            }
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["hard"][0]
        self.assertEqual(top["name"], "codex-ok (weekly)")

    def test_hard_recommendation_excludes_gpt_oss_120b(self):
        now = datetime.now(timezone.utc)
        result = {
            "antigravity": {
                "profiles": [
                    {
                        "name": "main",
                        "status": "running",
                        "models": [
                            {
                                "label": "GPT-OSS 120B",
                                "pct_left": 95.0,
                                "reset_time": (now + timedelta(hours=2)).isoformat(),
                            },
                            {
                                "label": "Claude Sonnet",
                                "pct_left": 40.0,
                                "reset_time": (now + timedelta(hours=4)).isoformat(),
                            },
                        ],
                    }
                ]
            }
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["hard"][0]
        self.assertEqual(top["name"], "antigravity:main → Claude Sonnet")

    def test_cli_falls_back_to_copilot_when_everything_is_exhausted(self):
        now = datetime.now(timezone.utc)
        result = {
            "codex": {
                "accounts": [
                    {
                        "name": "low",
                        "limits": [
                            {
                                "label": "weekly",
                                "left_percent": 4.0,
                                "reset_time": (now + timedelta(hours=2)).isoformat(),
                                "reset_time_fmt": "2 hours left to reset",
                            },
                        ],
                    },
                ]
            },
            "amp": {
                "email": "user@example.com",
                "tiers": [
                    {
                        "label": "Pool",
                        "remaining": 4.0,
                        "total": 100.0,
                        "pct_left": 4.0,
                        "pct_used": 96.0,
                        "replenish_rate": 5.0,
                    }
                ],
            },
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["cli"][0]
        self.assertEqual(top["name"], "copilot")
        self.assertEqual(top["command"], "use Copilot in editor")

    def test_waste_reduction_prefers_urgent_prepaid_quota(self):
        now = datetime.now(timezone.utc)
        result = {
            "codex": {
                "accounts": [
                    {
                        "name": "acct",
                        "limits": [
                            {
                                "label": "weekly",
                                "left_percent": 60.0,
                                "reset_time": (now + timedelta(hours=2)).isoformat(),
                                "reset_time_fmt": "2 hours left to reset",
                            },
                        ],
                    }
                ]
            },
            "antigravity": {
                "profiles": [
                    {
                        "name": "main",
                        "status": "running",
                        "models": [
                            {
                                "label": "Claude Sonnet",
                                "pct_left": 80.0,
                                "reset_time": (now + timedelta(days=3)).isoformat(),
                            }
                        ],
                    }
                ]
            },
            "amp": {
                "email": "user@example.com",
                "tiers": [
                    {
                        "label": "Pool",
                        "remaining": 80.0,
                        "total": 100.0,
                        "pct_left": 80.0,
                        "pct_used": 20.0,
                        "replenish_rate": 5.0,
                    }
                ],
            },
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["waste_reduction"][0]
        self.assertEqual(top["name"], "codex-acct (weekly)")
        self.assertEqual(top["waste_severity"], "urgent")

    def test_waste_reduction_falls_back_to_soonest_prepaid_reset(self):
        now = datetime.now(timezone.utc)
        result = {
            "codex": {
                "accounts": [
                    {
                        "name": "acct",
                        "limits": [
                            {
                                "label": "weekly",
                                "left_percent": 12.0,
                                "reset_time": (now + timedelta(days=3)).isoformat(),
                                "reset_time_fmt": "3 days left to reset",
                            },
                        ],
                    }
                ]
            },
            "antigravity": {
                "profiles": [
                    {
                        "name": "main",
                        "status": "running",
                        "models": [
                            {
                                "label": "Claude Sonnet",
                                "pct_left": 20.0,
                                "reset_time": (now + timedelta(hours=10)).isoformat(),
                            }
                        ],
                    }
                ]
            },
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["waste_reduction"][0]
        self.assertEqual(top["name"], "antigravity:main → Claude Sonnet")

    def test_waste_reduction_excludes_flash_models(self):
        now = datetime.now(timezone.utc)
        result = {
            "antigravity": {
                "profiles": [
                    {
                        "name": "main",
                        "status": "running",
                        "models": [
                            {
                                "label": "Gemini Flash",
                                "pct_left": 90.0,
                                "reset_time": (now + timedelta(hours=1)).isoformat(),
                            },
                            {
                                "label": "Claude Sonnet",
                                "pct_left": 30.0,
                                "reset_time": (now + timedelta(hours=8)).isoformat(),
                            },
                        ],
                    }
                ]
            }
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["waste_reduction"][0]
        self.assertEqual(top["name"], "antigravity:main → Claude Sonnet")

    def test_waste_reduction_excludes_gpt_oss_120b(self):
        now = datetime.now(timezone.utc)
        result = {
            "antigravity": {
                "profiles": [
                    {
                        "name": "main",
                        "status": "running",
                        "models": [
                            {
                                "label": "GPT-OSS 120B",
                                "pct_left": 90.0,
                                "reset_time": (now + timedelta(hours=1)).isoformat(),
                            },
                            {
                                "label": "Claude Sonnet",
                                "pct_left": 30.0,
                                "reset_time": (now + timedelta(hours=8)).isoformat(),
                            },
                        ],
                    }
                ]
            }
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["waste_reduction"][0]
        self.assertEqual(top["name"], "antigravity:main → Claude Sonnet")

    def test_waste_reduction_ignores_almost_empty_quota(self):
        now = datetime.now(timezone.utc)
        result = {
            "codex": {
                "accounts": [
                    {
                        "name": "low",
                        "limits": [
                            {
                                "label": "weekly",
                                "left_percent": 8.0,
                                "reset_time": (now + timedelta(hours=2)).isoformat(),
                                "reset_time_fmt": "2 hours left to reset",
                            },
                        ],
                    },
                    {
                        "name": "ok",
                        "limits": [
                            {
                                "label": "weekly",
                                "left_percent": 15.0,
                                "reset_time": (now + timedelta(hours=6)).isoformat(),
                                "reset_time_fmt": "6 hours left to reset",
                            },
                        ],
                    },
                ]
            }
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        top = recs["waste_reduction"][0]
        self.assertEqual(top["name"], "codex-ok (weekly)")

    def test_stale_antigravity_cache_is_not_waste_signal(self):
        now = datetime.now(timezone.utc)
        result = {
            "antigravity": {
                "profiles": [
                    {
                        "name": "main",
                        "status": "stale",
                        "models": [
                            {
                                "label": "Claude Sonnet",
                                "pct_left": 100.0,
                                "reset_time": (now - timedelta(hours=1)).isoformat(),
                            },
                        ],
                    }
                ]
            }
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        self.assertEqual(recs["waste_watch"], [])
        self.assertEqual(recs["waste_reduction"], [])

    def test_fresh_candidate_outranks_stale_antigravity_cache(self):
        now = datetime.now(timezone.utc)
        result = {
            "codex": {
                "accounts": [
                    {
                        "name": "fresh",
                        "limits": [
                            {
                                "label": "weekly",
                                "left_percent": 40.0,
                                "reset_time": (now + timedelta(days=2)).isoformat(),
                                "reset_time_fmt": "2 days left to reset",
                            },
                        ],
                    },
                ]
            },
            "antigravity": {
                "profiles": [
                    {
                        "name": "agy-cli",
                        "source": "cli",
                        "status": "stale",
                        "models": [
                            {
                                "label": "Gemini Pro",
                                "pct_left": 100.0,
                                "reset_time": (now + timedelta(hours=1)).isoformat(),
                            },
                        ],
                    }
                ]
            },
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)
        self.assertEqual(recs["quick"][0]["name"], "codex-fresh (weekly)")
        self.assertEqual(recs["cli"][0]["name"], "codex-fresh (weekly)")

    def test_fresh_codex_outranks_stale_codex(self):
        now = datetime.now(timezone.utc)
        result = {
            "codex": {
                "accounts": [
                    {
                        "name": "stale",
                        "limits": [
                            {
                                "label": "weekly",
                                "left_percent": 100.0,
                                "reset_time": (now - timedelta(hours=1)).isoformat(),
                                "reset_time_fmt": "likely reset (stale data)",
                                "is_stale": True,
                            },
                        ],
                    },
                    {
                        "name": "fresh",
                        "limits": [
                            {
                                "label": "weekly",
                                "left_percent": 40.0,
                                "reset_time": (now + timedelta(hours=3)).isoformat(),
                                "reset_time_fmt": "3 hours left to reset",
                                "is_stale": False,
                            },
                        ],
                    },
                ]
            }
        }

        recs = rec.compute_recommendations(result, parse_to_utc, fmt_reset)

        self.assertEqual(recs["hard"][0]["name"], "codex-fresh (weekly)")
        self.assertEqual(recs["hard"][1]["name"], "codex-stale (weekly) (stale)")
        self.assertTrue(recs["hard"][1]["stale"])


if __name__ == "__main__":
    unittest.main()
