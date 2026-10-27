"""
Focused Test Suite for Task 4: Advanced Privacy-Preserving Analytics.
Tests:
1. Configurable Crowd Density Thresholds (LOW / NORMAL / HIGH) & Congestion Alerts.
2. Polygon Restricted Zone Intrusion & Detection.
3. Bidirectional Tripwire Crossings (Entry vs Exit) & Net Occupancy.
4. Duplicate Event Prevention (zone stay & line lingering).
5. Privacy Compliance (Zero facial or biometric identity data in emitted JSON events).
"""

import os
import sys
import unittest

# Add scripts directory to path
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TEST_DIR)
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from analytics_engine import AnalyticsEngine


class TestAdvancedPrivacyAnalytics(unittest.TestCase):

    def setUp(self):
        self.sample_config = {
            "crowd_monitoring": {
                "enabled": True,
                "thresholds": {"LOW": 2, "NORMAL": 5, "HIGH": 8},
                "alert_cooldown_seconds": 2.0
            },
            "restricted_zones": [
                {
                    "zone_id": "zone_test_restricted",
                    "name": "Restricted Test Platform",
                    "polygon": [
                        [100, 100],
                        [300, 100],
                        [300, 300],
                        [100, 300]
                    ],
                    "alert_type": "ZONE_INTRUSION",
                    "enabled": True
                }
            ],
            "virtual_tripwires": [
                {
                    "tripwire_id": "tripwire_test_gate",
                    "name": "Gate Line",
                    "p1": [50, 200],
                    "p2": [400, 200],
                    "direction_labels": {
                        "A_TO_B": "ENTRY",
                        "B_TO_A": "EXIT"
                    },
                    "enabled": True
                }
            ],
            "bidirectional_counting": {
                "enabled": True,
                "initial_occupancy": 0,
                "crossing_debounce_frames": 5
            }
        }
        self.engine = AnalyticsEngine(config_dict=self.sample_config)

    def test_01_crowd_thresholds_and_congestion_alert(self):
        """Test crowd density transitions between LOW, NORMAL, HIGH and congestion alert emission."""
        # 1 person -> LOW
        events = self.engine.update([{"id": 1, "box": (10, 10, 30, 30)}], timestamp=100.0)
        self.assertEqual(self.engine.current_crowd_level, "LOW")
        self.assertEqual(len([e for e in events if e["event_type"] == "CROWD_CONGESTION"]), 0)

        # 4 persons -> NORMAL
        tracks = [{"id": i, "box": (10 * i, 10, 10 * i + 20, 30)} for i in range(1, 5)]
        events = self.engine.update(tracks, timestamp=100.1)
        self.assertEqual(self.engine.current_crowd_level, "NORMAL")
        self.assertEqual(len([e for e in events if e["event_type"] == "CROWD_CONGESTION"]), 0)

        # 9 persons -> HIGH, triggers CROWD_CONGESTION event
        tracks = [{"id": i, "box": (10 * i, 10, 10 * i + 20, 30)} for i in range(1, 10)]
        events = self.engine.update(tracks, timestamp=100.2)
        self.assertEqual(self.engine.current_crowd_level, "HIGH")
        congestion_events = [e for e in events if e["event_type"] == "CROWD_CONGESTION"]
        self.assertEqual(len(congestion_events), 1)
        self.assertEqual(congestion_events[0]["crowd_level"], "HIGH")
        self.assertEqual(congestion_events[0]["crowd_count"], 9)

        # Cooldown check: consecutive frame with 9 persons should NOT duplicate congestion event within 2.0s
        events_next = self.engine.update(tracks, timestamp=100.5)
        self.assertEqual(len([e for e in events_next if e["event_type"] == "CROWD_CONGESTION"]), 0)

        # After cooldown (timestamp 102.5s), new congestion alert can be emitted
        events_after = self.engine.update(tracks, timestamp=102.5)
        self.assertEqual(len([e for e in events_after if e["event_type"] == "CROWD_CONGESTION"]), 1)

    def test_02_polygon_restricted_zone_entry(self):
        """Test polygon zone intrusion alert on entry."""
        # Frame 1: Person outside polygon ([100, 100] to [300, 300]) -> (feet at y2=50)
        p_outside = [{"id": 10, "box": (150, 20, 170, 50)}]
        events = self.engine.update(p_outside, timestamp=200.0)
        zone_events = [e for e in events if e["event_type"] == "ZONE_INTRUSION"]
        self.assertEqual(len(zone_events), 0)

        # Frame 2: Person enters polygon -> (feet at x=200, y2=200) inside zone
        p_inside = [{"id": 10, "box": (190, 150, 210, 200)}]
        events = self.engine.update(p_inside, timestamp=200.1)
        zone_events = [e for e in events if e["event_type"] == "ZONE_INTRUSION"]
        self.assertEqual(len(zone_events), 1)
        self.assertEqual(zone_events[0]["zone_id"], "zone_test_restricted")

    def test_03_zone_duplicate_event_prevention(self):
        """Test that a person lingering inside a restricted zone does NOT emit duplicate alerts."""
        # Frame 1: Enter zone
        p_inside_1 = [{"id": 11, "box": (190, 150, 210, 200)}]
        events_1 = self.engine.update(p_inside_1, timestamp=300.0)
        self.assertEqual(len([e for e in events_1 if e["event_type"] == "ZONE_INTRUSION"]), 1)

        # Frames 2, 3, 4: Remaining inside the zone
        for i in range(1, 4):
            p_inside_stay = [{"id": 11, "box": (192, 150, 212, 205 + i)}]
            events_stay = self.engine.update(p_inside_stay, timestamp=300.0 + i * 0.1)
            # Must NOT duplicate
            self.assertEqual(len([e for e in events_stay if e["event_type"] == "ZONE_INTRUSION"]), 0)

    def test_04_bidirectional_line_crossing(self):
        """Test crossing in both directions: ENTRY (A->B) and EXIT (B->A)."""
        # Tripwire line: from (50, 200) to (400, 200) -> horizontal line at y=200
        # Side A: y < 200 (e.g. y2=150)
        # Side B: y > 200 (e.g. y2=250)

        # --- Test ENTRY (Crossing from Side A to Side B) ---
        # Frame 1: Person at y2=150 (above tripwire)
        p_entry_f1 = [{"id": 20, "box": (180, 100, 220, 150)}]
        self.engine.update(p_entry_f1, timestamp=400.0)

        # Frame 2: Person moves to y2=250 (below tripwire) -> crosses line
        p_entry_f2 = [{"id": 20, "box": (180, 200, 220, 250)}]
        events = self.engine.update(p_entry_f2, timestamp=400.1)
        crossings = [e for e in events if e["event_type"] == "TRIPWIRE_CROSSING"]
        self.assertEqual(len(crossings), 1)
        self.assertEqual(crossings[0]["direction"], "ENTRY")
        self.assertEqual(self.engine.entries, 1)
        self.assertEqual(self.engine.occupancy, 1)

        # --- Test EXIT (Crossing from Side B to Side A) ---
        # Frame 3: Person 21 starts at y2=260 (below tripwire)
        p_exit_f1 = [{"id": 21, "box": (280, 200, 320, 260)}]
        self.engine.update(p_exit_f1, timestamp=401.0)

        # Frame 4: Person 21 moves to y2=140 (above tripwire) -> crosses back
        p_exit_f2 = [{"id": 21, "box": (280, 80, 320, 140)}]
        events_exit = self.engine.update(p_exit_f2, timestamp=401.1)
        crossings_exit = [e for e in events_exit if e["event_type"] == "TRIPWIRE_CROSSING"]
        self.assertEqual(len(crossings_exit), 1)
        self.assertEqual(crossings_exit[0]["direction"], "EXIT")
        self.assertEqual(self.engine.exits, 1)
        self.assertEqual(self.engine.occupancy, 0)  # 1 entry - 1 exit = 0

    def test_05_crossing_duplicate_prevention(self):
        """Test that lingering on the tripwire does not emit multiple crossing events."""
        # Frame 1: Above line
        p1 = [{"id": 30, "box": (150, 100, 190, 170)}]
        self.engine.update(p1, timestamp=500.0)

        # Frame 2: Crosses line
        p2 = [{"id": 30, "box": (150, 160, 190, 220)}]
        ev1 = self.engine.update(p2, timestamp=500.1)
        self.assertEqual(len([e for e in ev1 if e["event_type"] == "TRIPWIRE_CROSSING"]), 1)
        self.assertEqual(self.engine.entries, 1)

        # Frame 3 & 4: Small oscillations near boundary during debounce window
        p3 = [{"id": 30, "box": (150, 165, 190, 218)}]
        ev2 = self.engine.update(p3, timestamp=500.2)
        self.assertEqual(len([e for e in ev2 if e["event_type"] == "TRIPWIRE_CROSSING"]), 0)
        self.assertEqual(self.engine.entries, 1)  # Remains 1

    def test_06_privacy_compliance_zero_facial_identity(self):
        """Verify emitted events strictly exclude any facial, biometric, or identity data."""
        # Generate an intrusion and crossing event
        p1 = [{"id": 40, "box": (180, 50, 220, 100)}]
        self.engine.update(p1, timestamp=600.0)
        p2 = [{"id": 40, "box": (180, 160, 220, 240)}]  # Crosses line and enters zone
        events = self.engine.update(p2, timestamp=600.1)

        self.assertGreater(len(events), 0)

        forbidden_keys = {
            "face", "face_id", "embedding", "biometric", "name_person",
            "identity", "image", "crop", "features", "gender", "age"
        }

        for ev in events:
            # Check root keys
            for k in ev.keys():
                self.assertNotIn(k.lower(), forbidden_keys, f"Found forbidden privacy key: {k}")
            # Ensure required non-biometric telemetry fields exist
            self.assertIn("timestamp", ev)
            self.assertIn("event_type", ev)
            self.assertIn("crowd_level", ev)
            self.assertIn("crowd_count", ev)
            self.assertIn("occupancy", ev)


if __name__ == "__main__":
    unittest.main()
