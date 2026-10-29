"""
Unit Test Suite for Zone Alerts, Repeat-Alert Logic (5s reminders), and Tripwire Crossings.
Verifies:
1. Initial Red-Zone Intrusion Alert generation with Face/Track ID and Zone Name.
2. 5-second reminder logic when track remains inside.
3. Exit detection after track leaves polygon.
4. Immediate Re-entry alert when track re-enters.
5. Per-track, per-zone cooldowns preventing spurious duplicate alerts.
6. Blue-Tripwire crossings with direction labels and tripwire names.
"""

import os
import sys
import unittest

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TEST_DIR)
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from analytics_engine import AnalyticsEngine


class TestAlertRepeatLogic(unittest.TestCase):

    def setUp(self):
        self.config = {
            "crowd_monitoring": {
                "enabled": True,
                "thresholds": {"LOW": 2, "NORMAL": 5, "HIGH": 8},
                "alert_cooldown_seconds": 5.0
            },
            "restricted_zones": [
                {
                    "zone_id": "zone_platform_restricted",
                    "name": "Platform Edge Restricted Zone",
                    "polygon": [[100, 100], [400, 100], [400, 400], [100, 400]],
                    "alert_type": "ZONE_INTRUSION",
                    "enabled": True
                }
            ],
            "virtual_tripwires": [
                {
                    "tripwire_id": "tripwire_gate_01",
                    "name": "Main Concourse Tripwire",
                    "p1": [50, 300],
                    "p2": [500, 300],
                    "direction_labels": {"A_TO_B": "ENTRY", "B_TO_A": "EXIT"},
                    "enabled": True
                }
            ],
            "bidirectional_counting": {
                "enabled": True,
                "initial_occupancy": 0,
                "crossing_debounce_frames": 5
            }
        }
        self.engine = AnalyticsEngine(config_dict=self.config)

    def test_01_red_zone_entry_alert_format(self):
        """When a tracked person enters the zone, emit an alert with track ID and zone name."""
        # Person #14 enters polygon at (200, 200)
        tracks = [{"id": 14, "box": (180, 150, 220, 200)}]
        events = self.engine.update(tracks, timestamp=100.0)

        entry_events = [e for e in events if e["event_type"] == "ZONE_INTRUSION"]
        self.assertEqual(len(entry_events), 1)
        ev = entry_events[0]
        self.assertEqual(ev["track_id"], 14)
        self.assertEqual(ev["zone_id"], "zone_platform_restricted")
        self.assertEqual(ev["zone_name"], "Platform Edge Restricted Zone")
        self.assertIn("Face #14 entered Platform Edge Restricted Zone", ev["message"])
        self.assertIn("event_id", ev)
        self.assertIn("timestamp_iso", ev)

    def test_02_five_second_reminder_logic(self):
        """If person remains inside, send another reminder every 5 seconds."""
        # 1. First entry at t=100.0s -> generates initial alert
        tracks = [{"id": 14, "box": (180, 150, 220, 200)}]
        events_1 = self.engine.update(tracks, timestamp=100.0)
        self.assertEqual(len([e for e in events_1 if e["event_type"] == "ZONE_INTRUSION"]), 1)

        # 2. At t=102.0s (2s elapsed) -> NO duplicate alert
        events_2 = self.engine.update(tracks, timestamp=102.0)
        self.assertEqual(len([e for e in events_2 if e["event_type"] in ("ZONE_INTRUSION", "ZONE_REMINDER")]), 0)

        # 3. At t=104.9s (< 5.0s elapsed) -> Still NO duplicate alert
        events_3 = self.engine.update(tracks, timestamp=104.9)
        self.assertEqual(len([e for e in events_3 if e["event_type"] in ("ZONE_INTRUSION", "ZONE_REMINDER")]), 0)

        # 4. At t=105.1s (>= 5.0s elapsed) -> 5-SECOND REMINDER EMITTED!
        events_4 = self.engine.update(tracks, timestamp=105.1)
        reminders = [e for e in events_4 if e["event_type"] == "ZONE_REMINDER"]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["track_id"], 14)
        self.assertEqual(reminders[0]["reminder_count"], 1)
        self.assertIn("5s reminder", reminders[0]["message"])
        self.assertAlmostEqual(reminders[0]["dwell_seconds"], 5.1, delta=0.2)

        # 5. At t=108.0s -> NO alert (only 2.9s since last reminder)
        events_5 = self.engine.update(tracks, timestamp=108.0)
        self.assertEqual(len([e for e in events_5 if e["event_type"] == "ZONE_REMINDER"]), 0)

        # 6. At t=110.2s (>= 5.0s since last reminder at 105.1s) -> SECOND 5-SECOND REMINDER!
        events_6 = self.engine.update(tracks, timestamp=110.2)
        reminders_2 = [e for e in events_6 if e["event_type"] == "ZONE_REMINDER"]
        self.assertEqual(len(reminders_2), 1)
        self.assertEqual(reminders_2[0]["reminder_count"], 2)

    def test_03_exit_and_immediate_reentry(self):
        """If a person exits and re-enters, immediately generate a new entry alert."""
        # Entry at t=200.0s
        tracks_in = [{"id": 14, "box": (180, 150, 220, 200)}]
        self.engine.update(tracks_in, timestamp=200.0)

        # Person moves outside polygon ([100, 100] to [400, 400]) -> (feet at y2=50)
        tracks_out = [{"id": 14, "box": (180, 20, 220, 50)}]
        self.engine.update(tracks_out, timestamp=200.1)
        self.engine.update(tracks_out, timestamp=200.2)
        events_exit = self.engine.update(tracks_out, timestamp=200.3)
        exit_evs = [e for e in events_exit if e["event_type"] == "ZONE_EXIT"]
        self.assertEqual(len(exit_evs), 1)
        self.assertEqual(exit_evs[0]["track_id"], 14)

        # Person RE-ENTERS at t=201.0s -> Immediately generates new entry/reentry alert!
        events_reenter = self.engine.update(tracks_in, timestamp=201.0)
        reentry_evs = [e for e in events_reenter if e["event_type"] in ("ZONE_INTRUSION", "ZONE_REENTRY")]
        self.assertEqual(len(reentry_evs), 1)
        self.assertEqual(reentry_evs[0]["track_id"], 14)
        self.assertTrue(reentry_evs[0].get("is_reentry", False))
        self.assertIn("re-entered", reentry_evs[0]["message"])

    def test_04_blue_tripwire_crossing_alert(self):
        """Crossed tripwire emits alert with direction (ENTRY/EXIT) and tripwire name."""
        # Tripwire at y=300 (horizontal line between (50, 300) and (500, 300))
        # Frame 1: Above tripwire (y2=250)
        p_above = [{"id": 14, "box": (200, 200, 240, 250)}]
        self.engine.update(p_above, timestamp=300.0)

        # Frame 2: Moves below tripwire (y2=350) -> Crosses line A->B (ENTRY)
        p_below = [{"id": 14, "box": (200, 300, 240, 350)}]
        events = self.engine.update(p_below, timestamp=300.1)
        crossings = [e for e in events if e["event_type"] == "TRIPWIRE_CROSSING"]
        self.assertEqual(len(crossings), 1)
        ev = crossings[0]
        self.assertEqual(ev["track_id"], 14)
        self.assertEqual(ev["direction"], "ENTRY")
        self.assertEqual(ev["tripwire_name"], "Main Concourse Tripwire")
        self.assertIn("Face #14 crossed Main Concourse Tripwire [ENTRY]", ev["message"])


if __name__ == "__main__":
    unittest.main()
