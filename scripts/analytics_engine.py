"""
Advanced Privacy-Preserving Video Analytics Engine.
Provides crowd density monitoring, polygon restricted zones, virtual tripwires,
bidirectional counting, and zero-knowledge JSON event emission.
"""

import os
import json
import time
import uuid
from typing import Dict, List, Tuple, Any, Optional

import cv2
import numpy as np


class AnalyticsEngine:
    """
    Modular analytics engine that processes tracked person detections and emits
    privacy-safe event metadata (no facial identity data).
    """

    def __init__(self, config_path: Optional[str] = None, config_dict: Optional[Dict[str, Any]] = None):
        self.config_path = config_path
        self.config = self._load_config(config_path, config_dict)
        
        # Crowd density configuration
        crowd_cfg = self.config.get("crowd_monitoring", {})
        self.crowd_enabled = crowd_cfg.get("enabled", True)
        self.crowd_thresholds = crowd_cfg.get("thresholds", {"LOW": 2, "NORMAL": 5, "HIGH": 8})
        self.congestion_cooldown = crowd_cfg.get("alert_cooldown_seconds", 5.0)
        self.last_congestion_alert_time = 0.0
        self.current_crowd_level = "LOW"
        self.current_crowd_count = 0

        # Restricted zones
        self.restricted_zones = self.config.get("restricted_zones", [])

        # Virtual tripwires
        self.virtual_tripwires = self.config.get("virtual_tripwires", [])

        # Bidirectional counting state
        count_cfg = self.config.get("bidirectional_counting", {})
        self.counting_enabled = count_cfg.get("enabled", True)
        self.entries = 0
        self.exits = 0
        self.occupancy = count_cfg.get("initial_occupancy", 0)
        self.debounce_frames = count_cfg.get("crossing_debounce_frames", 10)

        # Track history & state machine for duplicate prevention
        self.track_states: Dict[int, Dict[str, Any]] = {}
        self.frame_number = 0

        # State tracking for per-track, per-zone entry, 5s reminders, and re-entry
        self.zone_track_alert_state: Dict[Tuple[str, int], Dict[str, Any]] = {}

        # Event emission storage
        self.emitted_events: List[Dict[str, Any]] = []
        self.event_output_file = self.config.get("event_emission", {}).get("output_file", None)

        # Real-time dashboard indicators & feeds
        self.total_intrusions = 0
        self.is_zone_intruded_now = False
        self.current_zone_occupants = 0
        self.last_intrusion_time: Optional[str] = None
        self.zone_event_history: List[Dict[str, Any]] = []

        # Advanced per-zone breakdown (Capacity, % Occupancy, Dwell Times, Peak Occupancy)
        self.zone_entry_timestamps: Dict[str, Dict[int, float]] = {}
        self.zone_dwell_records: Dict[str, List[float]] = {}
        self.zone_peak_occupants: Dict[str, int] = {}
        self.zone_active_tracks: Dict[str, List[int]] = {}
        self.last_loitering_alert_time: Dict[str, float] = {}

        self.last_crossing_time: Optional[str] = None
        self.last_crossing_direction: Optional[str] = None
        self.tripwire_event_history: List[Dict[str, Any]] = []

        # Advanced Virtual Tripwire Analytics (Flow Rate, Flow Velocity, Directional Split)
        self.crossing_history_timestamps: List[Tuple[float, str]] = []
        self.peak_flow_rate = 0.0

    def save_config(self) -> bool:
        if not self.config_path:
            return False
        try:
            self.config["restricted_zones"] = self.restricted_zones
            self.config["virtual_tripwires"] = self.virtual_tripwires
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            return True
        except Exception as e:
            print(f"[AnalyticsEngine] Error saving config: {e}")
            return False

    def add_zone(self, polygon: List[List[int]], name: Optional[str] = None, capacity: int = 5) -> Dict[str, Any]:
        """Appends an additional restricted polygon zone with capacity tracking."""
        new_idx = len(self.restricted_zones) + 1
        zone_id = f"zone_restricted_{new_idx}"
        zone_name = name or f"Zone #{new_idx}"
        new_zone = {
            "zone_id": zone_id,
            "name": zone_name,
            "polygon": polygon,
            "capacity": capacity,
            "alert_type": "ZONE_INTRUSION",
            "enabled": True
        }
        self.restricted_zones.append(new_zone)
        self.save_config()
        return new_zone

    def set_zone_enabled(self, enabled: bool, index: Optional[int] = None) -> bool:
        if index is None:
            for z in self.restricted_zones:
                z["enabled"] = bool(enabled)
            self.save_config()
            return True
        elif 0 <= index < len(self.restricted_zones):
            self.restricted_zones[index]["enabled"] = bool(enabled)
            self.save_config()
            return True
        return False

    def set_tripwire_enabled(self, enabled: bool, index: int = 0) -> bool:
        if 0 <= index < len(self.virtual_tripwires):
            self.virtual_tripwires[index]["enabled"] = bool(enabled)
            self.save_config()
            return True
        return False

    def update_zone_polygon(self, polygon: List[List[int]], index: int = 0) -> bool:
        if 0 <= index < len(self.restricted_zones):
            self.restricted_zones[index]["polygon"] = polygon
            self.restricted_zones[index]["enabled"] = True
            self.save_config()
            return True
        elif len(self.restricted_zones) == 0:
            self.restricted_zones.append({
                "zone_id": "zone_custom_01",
                "name": "Custom Restricted Zone",
                "polygon": polygon,
                "capacity": 5,
                "alert_type": "ZONE_INTRUSION",
                "enabled": True
            })
            self.save_config()
            return True
        return False

    def update_tripwire(self, p1: List[int], p2: List[int], index: int = 0) -> bool:
        if 0 <= index < len(self.virtual_tripwires):
            self.virtual_tripwires[index]["p1"] = p1
            self.virtual_tripwires[index]["p2"] = p2
            self.virtual_tripwires[index]["enabled"] = True
            self.save_config()
            return True
        elif len(self.virtual_tripwires) == 0:
            self.virtual_tripwires.append({
                "tripwire_id": "tripwire_custom_01",
                "name": "Custom Tripwire",
                "p1": p1,
                "p2": p2,
                "direction_labels": {"A_TO_B": "ENTRY", "B_TO_A": "EXIT"},
                "enabled": True
            })
            self.save_config()
            return True
        return False

    def reset_analytics_defaults(self) -> None:
        self.restricted_zones = [
            {
                "zone_id": "zone_platform_restricted",
                "name": "Platform Edge Restricted Zone",
                "polygon": [[40, 200], [450, 200], [450, 680], [40, 680]],
                "capacity": 5,
                "alert_type": "ZONE_INTRUSION",
                "enabled": True
            }
        ]
        self.virtual_tripwires = [
            {
                "tripwire_id": "tripwire_gate_01",
                "name": "Main Concourse Tripwire",
                "p1": [100, 380],
                "p2": [1180, 380],
                "direction_labels": {"A_TO_B": "ENTRY", "B_TO_A": "EXIT"},
                "enabled": True
            }
        ]
        self.entries = 0
        self.exits = 0
        self.occupancy = 0
        self.total_intrusions = 0
        self.is_zone_intruded_now = False
        self.current_zone_occupants = 0
        self.last_intrusion_time = None
        self.last_crossing_time = None
        self.last_crossing_direction = None
        self.zone_event_history.clear()
        self.tripwire_event_history.clear()
        self.zone_entry_timestamps.clear()
        self.zone_dwell_records.clear()
        self.zone_peak_occupants.clear()
        self.zone_active_tracks.clear()
        self.last_loitering_alert_time.clear()
        self.crossing_history_timestamps.clear()
        self.peak_flow_rate = 0.0
        self.track_states.clear()
        self.zone_track_alert_state.clear()
        self.save_config()

    def _load_config(self, config_path: Optional[str], config_dict: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if config_dict:
            return config_dict
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[AnalyticsEngine] Warning: Could not parse {config_path}: {e}")
        
        # Clean defaults
        return {
            "crowd_monitoring": {
                "enabled": True,
                "thresholds": {"LOW": 2, "NORMAL": 5, "HIGH": 8},
                "alert_cooldown_seconds": 5.0
            },
            "restricted_zones": [],
            "virtual_tripwires": [],
            "bidirectional_counting": {
                "enabled": True,
                "initial_occupancy": 0,
                "crossing_debounce_frames": 10
            }
        }

    def update(self, person_tracks: List[Dict[str, Any]], timestamp: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        Processes active person tracks for the current frame.
        person_tracks: list of dicts with keys: 'id' (int), 'box' (x1, y1, x2, y2).
        Returns a list of events emitted in this frame.
        """
        if timestamp is None:
            timestamp = time.time()
        self.frame_number += 1
        frame_events: List[Dict[str, Any]] = []

        active_track_ids = set()
        self.is_zone_intruded_now = False
        frame_zone_occupants = 0
        current_frame_zone_tracks: Dict[str, List[int]] = {
            z.get("zone_id", f"zone_{i}"): [] for i, z in enumerate(self.restricted_zones)
        }

        # 1. Evaluate Crowd Monitoring
        self.current_crowd_count = len(person_tracks)
        new_crowd_level = self._compute_crowd_level(self.current_crowd_count)
        
        if self.crowd_enabled:
            # Check for congestion alert when HIGH
            if new_crowd_level == "HIGH":
                if (timestamp - self.last_congestion_alert_time) >= self.congestion_cooldown:
                    event = self._create_event(
                        event_type="CROWD_CONGESTION",
                        timestamp=timestamp,
                        extra={
                            "crowd_level": "HIGH",
                            "crowd_count": self.current_crowd_count,
                            "occupancy": self.occupancy,
                            "threshold": self.crowd_thresholds.get("HIGH", 8)
                        }
                    )
                    frame_events.append(event)
                    self.last_congestion_alert_time = timestamp
        
        self.current_crowd_level = new_crowd_level

        # 2. Process each person track
        for p in person_tracks:
            tid = p.get("id", 0)
            x1, y1, x2, y2 = p["box"]
            active_track_ids.add(tid)

            # Centroid at feet / ground level for spatial accuracy
            cx = int((x1 + x2) / 2)
            cy = int(y2)

            # Initialize track state if new
            if tid not in self.track_states:
                self.track_states[tid] = {
                    "history": [],
                    "inside_zones": set(),
                    "last_cross_frame": -999,
                    "last_cross_side": None
                }

            tstate = self.track_states[tid]
            prev_point = tstate["history"][-1] if len(tstate["history"]) > 0 else None
            tstate["history"].append((cx, cy, timestamp))
            if len(tstate["history"]) > 30:
                tstate["history"].pop(0)

            # 3. Check Restricted Zones (Polygon Containment)
            curr_inside_zones = set()
            track_in_any_zone = False
            for zone in self.restricted_zones:
                if not zone.get("enabled", True):
                    continue
                zid = zone.get("zone_id", "unnamed_zone")
                zname = zone.get("name", zid)
                polygon = np.array(zone.get("polygon", []), dtype=np.int32)
                if len(polygon) < 3:
                    continue

                is_inside = cv2.pointPolygonTest(polygon, (cx, cy), False) >= 0

                state_key = (zid, tid)
                if state_key not in self.zone_track_alert_state:
                    self.zone_track_alert_state[state_key] = {
                        "inside": False,
                        "first_entered_time": timestamp,
                        "last_alert_time": 0.0,
                        "frames_outside": 0,
                        "reminder_count": 0,
                        "reentry_count": 0
                    }
                z_alert = self.zone_track_alert_state[state_key]

                if is_inside:
                    track_in_any_zone = True
                    self.is_zone_intruded_now = True
                    curr_inside_zones.add(zid)
                    current_frame_zone_tracks.setdefault(zid, []).append(tid)

                    z_alert["frames_outside"] = 0

                    if zid not in self.zone_entry_timestamps:
                        self.zone_entry_timestamps[zid] = {}
                    if tid not in self.zone_entry_timestamps[zid]:
                        self.zone_entry_timestamps[zid][tid] = timestamp

                    dwell_now = timestamp - self.zone_entry_timestamps[zid][tid]

                    # 1. Entry / Re-entry Alert Logic
                    if not z_alert["inside"]:
                        z_alert["inside"] = True
                        z_alert["first_entered_time"] = timestamp
                        z_alert["last_alert_time"] = timestamp
                        z_alert["reminder_count"] = 0
                        is_reentry = (z_alert["reentry_count"] > 0)
                        z_alert["reentry_count"] += 1

                        self.total_intrusions += 1
                        self.last_intrusion_time = time.strftime("%H:%M:%S", time.localtime(timestamp))
                        ev_type = "ZONE_INTRUSION" if not is_reentry else "ZONE_REENTRY"
                        msg = f"Face #{tid} entered {zname}" if not is_reentry else f"Face #{tid} re-entered {zname}"

                        event = self._create_event(
                            event_type=ev_type,
                            timestamp=timestamp,
                            zone_id=zid,
                            track_id=tid,
                            extra={
                                "zone_name": zname,
                                "target_name": zname,
                                "track_id": tid,
                                "message": msg,
                                "dwell_seconds": 0.0,
                                "reminder_count": 0,
                                "is_reentry": is_reentry,
                                "crowd_count": self.current_crowd_count,
                                "occupancy": self.occupancy
                            }
                        )
                        frame_events.append(event)
                        self.zone_event_history.append({
                            "time": time.strftime("%H:%M:%S", time.localtime(timestamp)),
                            "zone_name": zname,
                            "track_id": tid,
                            "type": "ENTRY" if not is_reentry else "REENTRY",
                            "message": msg
                        })
                        if len(self.zone_event_history) > 20:
                            self.zone_event_history.pop(0)

                    # 2. Repeat-Alert Logic: If remaining inside, send reminder alert every 5.0 seconds
                    elif (timestamp - z_alert["last_alert_time"]) >= 5.0:
                        z_alert["last_alert_time"] = timestamp
                        z_alert["reminder_count"] += 1
                        rem_msg = f"Face #{tid} entered {zname} (5s reminder: lingering {round(dwell_now, 1)}s)"
                        rem_ev = self._create_event(
                            event_type="ZONE_REMINDER",
                            timestamp=timestamp,
                            zone_id=zid,
                            track_id=tid,
                            extra={
                                "zone_name": zname,
                                "target_name": zname,
                                "track_id": tid,
                                "message": rem_msg,
                                "dwell_seconds": round(dwell_now, 1),
                                "reminder_count": z_alert["reminder_count"],
                                "crowd_count": self.current_crowd_count,
                                "occupancy": self.occupancy
                            }
                        )
                        frame_events.append(rem_ev)
                        self.zone_event_history.append({
                            "time": time.strftime("%H:%M:%S", time.localtime(timestamp)),
                            "zone_name": zname,
                            "track_id": tid,
                            "type": "REMINDER",
                            "message": rem_msg
                        })
                        if len(self.zone_event_history) > 20:
                            self.zone_event_history.pop(0)

                    # Also detect long loitering (>8.0s) for loitering telemetry
                    if dwell_now >= 8.0:
                        if (timestamp - self.last_loitering_alert_time.get(zid, 0.0)) >= 6.0:
                            self.last_loitering_alert_time[zid] = timestamp
                            loiter_ev = self._create_event(
                                event_type="ZONE_LOITERING",
                                timestamp=timestamp,
                                zone_id=zid,
                                track_id=tid,
                                extra={
                                    "zone_name": zname,
                                    "target_name": zname,
                                    "track_id": tid,
                                    "message": f"Loiter warning: Face #{tid} lingering in {zname} ({round(dwell_now, 1)}s)",
                                    "dwell_seconds": round(dwell_now, 1),
                                    "occupancy": self.occupancy
                                }
                            )
                            frame_events.append(loiter_ev)

                else:
                    # Track is currently outside this zone
                    if z_alert["inside"]:
                        z_alert["frames_outside"] += 1
                        # De-bounce exit: require 3 consecutive frames outside to confirm exit
                        if z_alert["frames_outside"] >= 3:
                            z_alert["inside"] = False
                            dwell_total = round(timestamp - z_alert["first_entered_time"], 1)
                            exit_msg = f"Face #{tid} exited {zname} (dwell: {dwell_total}s)"
                            exit_ev = self._create_event(
                                event_type="ZONE_EXIT",
                                timestamp=timestamp,
                                zone_id=zid,
                                track_id=tid,
                                extra={
                                    "zone_name": zname,
                                    "target_name": zname,
                                    "track_id": tid,
                                    "message": exit_msg,
                                    "dwell_seconds": dwell_total,
                                    "crowd_count": self.current_crowd_count,
                                    "occupancy": self.occupancy
                                }
                            )
                            frame_events.append(exit_ev)
                            self.zone_event_history.append({
                                "time": time.strftime("%H:%M:%S", time.localtime(timestamp)),
                                "zone_name": zname,
                                "track_id": tid,
                                "type": "EXIT",
                                "message": exit_msg
                            })
                            if len(self.zone_event_history) > 20:
                                self.zone_event_history.pop(0)

            if track_in_any_zone:
                frame_zone_occupants += 1

            tstate["inside_zones"] = curr_inside_zones

            # 4. Check Bidirectional Line Crossings (Virtual Tripwires)
            if self.counting_enabled and prev_point is not None:
                px, py, _ = prev_point
                for tripwire in self.virtual_tripwires:
                    if not tripwire.get("enabled", True):
                        continue
                    tw_id = tripwire.get("tripwire_id", "tripwire_01")
                    p1 = tripwire.get("p1", [0, 0])
                    p2 = tripwire.get("p2", [100, 100])
                    dir_labels = tripwire.get("direction_labels", {"A_TO_B": "ENTRY", "B_TO_A": "EXIT"})

                    # Test line intersection & directional orientation
                    crossing_dir = self._detect_line_crossing((px, py), (cx, cy), p1, p2)
                    
                    if crossing_dir is not None:
                        # Debounce / duplicate prevention check
                        if (self.frame_number - tstate["last_cross_frame"]) >= self.debounce_frames:
                            direction_str = dir_labels.get(crossing_dir, crossing_dir)
                            if direction_str == "ENTRY":
                                self.entries += 1
                                self.occupancy += 1
                            elif direction_str == "EXIT":
                                self.exits += 1
                                self.occupancy = max(0, self.occupancy - 1)

                            self.last_crossing_time = time.strftime("%H:%M:%S", time.localtime(timestamp))
                            self.last_crossing_direction = direction_str
                            tstate["last_cross_frame"] = self.frame_number
                            tstate["last_cross_side"] = crossing_dir
                            tw_name = tripwire.get("name", tw_id)

                            # Record crossing history for flow velocity analytics
                            self.crossing_history_timestamps.append((timestamp, direction_str))

                            cross_msg = f"Face #{tid} crossed {tw_name} [{direction_str}]"
                            event = self._create_event(
                                event_type="TRIPWIRE_CROSSING",
                                timestamp=timestamp,
                                tripwire_id=tw_id,
                                track_id=tid,
                                extra={
                                    "direction": direction_str,
                                    "crossing_dir": crossing_dir,
                                    "tripwire_name": tw_name,
                                    "target_name": tw_name,
                                    "track_id": tid,
                                    "message": cross_msg,
                                    "entries": self.entries,
                                    "exits": self.exits,
                                    "occupancy": self.occupancy,
                                    "crowd_count": self.current_crowd_count
                                }
                            )
                            frame_events.append(event)
                            self.tripwire_event_history.append({
                                "time": time.strftime("%H:%M:%S", time.localtime(timestamp)),
                                "tripwire_name": tw_name,
                                "track_id": tid,
                                "direction": direction_str,
                                "message": cross_msg
                            })
                            if len(self.tripwire_event_history) > 20:
                                self.tripwire_event_history.pop(0)

        # Also check tracks that completely vanished from the frame while inside a zone
        for state_key, z_alert in list(self.zone_track_alert_state.items()):
            zid, tid = state_key
            if tid not in active_track_ids and z_alert["inside"]:
                z_alert["frames_outside"] += 1
                if z_alert["frames_outside"] >= 3:
                    z_alert["inside"] = False
                    dwell_total = round(timestamp - z_alert["first_entered_time"], 1)
                    zone_match = next((z for z in self.restricted_zones if z.get("zone_id") == zid), None)
                    zname = zone_match.get("name", zid) if zone_match else zid
                    exit_msg = f"Face #{tid} exited {zname} (dwell: {dwell_total}s)"
                    exit_ev = self._create_event(
                        event_type="ZONE_EXIT",
                        timestamp=timestamp,
                        zone_id=zid,
                        track_id=tid,
                        extra={
                            "zone_name": zname,
                            "target_name": zname,
                            "track_id": tid,
                            "message": exit_msg,
                            "dwell_seconds": dwell_total,
                            "crowd_count": self.current_crowd_count,
                            "occupancy": self.occupancy
                        }
                    )
                    frame_events.append(exit_ev)
                    self.zone_event_history.append({
                        "time": time.strftime("%H:%M:%S", time.localtime(timestamp)),
                        "zone_name": zname,
                        "track_id": tid,
                        "type": "EXIT",
                        "message": exit_msg
                    })
                    if len(self.zone_event_history) > 20:
                        self.zone_event_history.pop(0)


        self.current_zone_occupants = frame_zone_occupants
        self.zone_active_tracks = current_frame_zone_tracks

        # Record completed dwell times when occupants exit
        for zid, ent_map in list(self.zone_entry_timestamps.items()):
            active_tids_in_zone = set(current_frame_zone_tracks.get(zid, []))
            for tid, enter_t in list(ent_map.items()):
                if tid not in active_tids_in_zone:
                    dwell = timestamp - enter_t
                    self.zone_dwell_records.setdefault(zid, []).append(dwell)
                    if len(self.zone_dwell_records[zid]) > 20:
                        self.zone_dwell_records[zid].pop(0)
                    del ent_map[tid]

        # 5. Clean up stale track states
        stale_tids = [tid for tid in self.track_states if tid not in active_track_ids]
        for tid in stale_tids:
            if len(self.track_states[tid]["history"]) == 0 or (self.frame_number - self.track_states[tid].get("last_cross_frame", 0)) > 60:
                del self.track_states[tid]

        # Trim old crossing history (keep last 10 minutes)
        self.crossing_history_timestamps = [(t, d) for t, d in self.crossing_history_timestamps if (timestamp - t) <= 600.0]

        # Record events
        for ev in frame_events:
            self.emitted_events.append(ev)
            self._write_event_to_disk(ev)

        return frame_events

    def _compute_crowd_level(self, count: int) -> str:
        """Determines crowd density level based on thresholds."""
        low_max = self.crowd_thresholds.get("LOW", 2)
        normal_max = self.crowd_thresholds.get("NORMAL", 5)

        if count <= low_max:
            return "LOW"
        elif count <= normal_max:
            return "NORMAL"
        else:
            return "HIGH"

    @staticmethod
    def _detect_line_crossing(pA: Tuple[int, int], pB: Tuple[int, int],
                               lineP1: List[int], lineP2: List[int]) -> Optional[str]:
        """
        Determines if movement segment pA -> pB intersected tripwire segment lineP1 -> lineP2.
        Uses exact 2D segment intersection and cross-product orientation.
        Returns 'A_TO_B', 'B_TO_A', or None.
        """
        x1, y1 = lineP1
        x2, y2 = lineP2
        ax, ay = pA
        bx, by = pB

        # 2D cross product of vector (q - p) and (r - p)
        def ccw(px, py, qx, qy, rx, ry):
            return (qx - px) * (ry - py) - (qy - py) * (rx - px)

        # d1 and d2 test whether pA and pB are on opposite sides of lineP1-lineP2
        d1 = ccw(x1, y1, x2, y2, ax, ay)
        d2 = ccw(x1, y1, x2, y2, bx, by)

        # d3 and d4 test whether lineP1 and lineP2 are on opposite sides of trajectory pA-pB
        d3 = ccw(ax, ay, bx, by, x1, y1)
        d4 = ccw(ax, ay, bx, by, x2, y2)

        # Segments intersect if endpoints lie on opposite sides
        if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
            if d1 < 0 and d2 > 0:
                return "A_TO_B"
            elif d1 > 0 and d2 < 0:
                return "B_TO_A"

        return None

    def _create_event(self, event_type: str, timestamp: float,
                      zone_id: Optional[str] = None,
                      tripwire_id: Optional[str] = None,
                      track_id: Optional[int] = None,
                      extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Constructs a structured JSON event schema with unique event_id, track_id, and telemetry.
        """
        event_id = f"evt_{int(timestamp * 1000)}_{uuid.uuid4().hex[:8]}"
        iso_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(timestamp)) + f".{int((timestamp % 1) * 1000):03d}Z"
        time_str = time.strftime("%H:%M:%S", time.localtime(timestamp))

        event = {
            "event_id": event_id,
            "timestamp": round(timestamp, 3),
            "timestamp_iso": iso_str,
            "time_str": time_str,
            "event_type": event_type,
            "track_id": track_id if track_id is not None else 0,
            "crowd_level": self.current_crowd_level,
            "crowd_count": self.current_crowd_count,
            "occupancy": self.occupancy,
            "entries": self.entries,
            "exits": self.exits
        }
        if zone_id:
            event["zone_id"] = zone_id
            event["target_id"] = zone_id
        if tripwire_id:
            event["tripwire_id"] = tripwire_id
            event["target_id"] = tripwire_id
        if extra:
            event.update(extra)

        self.emitted_events.append(event)
        if len(self.emitted_events) > 100:
            self.emitted_events.pop(0)

        self._write_event_to_disk(event)
        return event

    def _write_event_to_disk(self, event: Dict[str, Any]) -> None:
        """Appends event record to the JSON events log if configured."""
        if not self.event_output_file:
            return
        try:
            with open(self.event_output_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception:
            pass

    def _draw_badge(self, img: np.ndarray, text: str, pos: Tuple[int, int],
                    bg_color: Tuple[int, int, int] = (20, 20, 20),
                    text_color: Tuple[int, int, int] = (255, 255, 255),
                    border_color: Optional[Tuple[int, int, int]] = (70, 70, 70),
                    font_scale: float = 0.55, thickness: int = 2,
                    padding: int = 6) -> Tuple[int, int, int, int]:
        """Draws a crisp solid background pill with text for maximum legibility."""
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        x, y = pos
        x1 = max(4, x - padding)
        y1 = max(4, y - th - padding)
        x2 = min(img.shape[1] - 4, x + tw + padding)
        y2 = min(img.shape[0] - 4, y + baseline + padding - 2)

        # Draw solid background rectangle
        cv2.rectangle(img, (x1, y1), (x2, y2), bg_color, -1)
        if border_color is not None:
            cv2.rectangle(img, (x1, y1), (x2, y2), border_color, 1, cv2.LINE_AA)

        # Draw sharp text
        cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, text_color, thickness, cv2.LINE_AA)
        return (x1, y1, x2, y2)

    def draw_overlay(self, frame: np.ndarray, show_zones: bool = True) -> np.ndarray:
        """Renders analytics visuals: restricted zones, tripwires, and live telemetry with high legibility."""
        if not show_zones:
            return frame

        h, w = frame.shape[:2]

        # 1. Semi-transparent fills for zones
        overlay = frame.copy()
        has_zones = False
        for zone in self.restricted_zones:
            if not zone.get("enabled", True):
                continue
            poly = np.array(zone.get("polygon", []), dtype=np.int32)
            if len(poly) >= 3:
                has_zones = True
                cv2.fillPoly(overlay, [poly], color=(0, 20, 160))

        if has_zones:
            # Blend the zone tint into the frame (28% tint, 72% original)
            cv2.addWeighted(overlay, 0.28, frame, 0.72, 0, frame)

        # 2. Draw crisp zone boundaries and solid badges (100% OPAQUE - not washed out)
        for zone in self.restricted_zones:
            if not zone.get("enabled", True):
                continue
            poly = np.array(zone.get("polygon", []), dtype=np.int32)
            if len(poly) >= 3:
                # Solid red border
                cv2.polylines(frame, [poly], isClosed=True, color=(30, 45, 235), thickness=2, lineType=cv2.LINE_AA)

                # Find top-left point for badge placement
                min_y_idx = int(np.argmin(poly[:, 1]))
                top_x = int(poly[min_y_idx][0])
                top_y = int(poly[min_y_idx][1])
                name = zone.get("name", "Restricted Area")
                zid = zone.get("zone_id", "")
                occ = len(self.zone_active_tracks.get(zid, []))
                cap = int(zone.get("capacity", 5))
                pct = int(min(100.0, (occ / cap) * 100.0)) if cap > 0 else 0
                badge_text = f"ZONE: {name} [{occ}/{cap} • {pct}% OCC]" if occ > 0 else f"ZONE: {name} [SECURE 0%]"

                self._draw_badge(
                    frame,
                    badge_text,
                    (top_x + 10, max(30, top_y + 24)),
                    bg_color=(15, 15, 25),
                    text_color=(60, 120, 255) if occ == 0 else (50, 70, 255),
                    border_color=(30, 45, 235),
                    font_scale=0.50,
                    thickness=2
                )

        # 3. Draw Virtual Tripwires with high-contrast lines and sharp label badges
        for tw in self.virtual_tripwires:
            if not tw.get("enabled", True):
                continue
            p1 = tuple(tw.get("p1", [0, 0]))
            p2 = tuple(tw.get("p2", [100, 100]))

            # Glow / contrast line underneath
            cv2.line(frame, p1, p2, (0, 0, 0), 4, cv2.LINE_AA)
            # Vibrant cyan tripwire line
            cv2.line(frame, p1, p2, (255, 205, 40), 2, cv2.LINE_AA)

            # Circular terminal points
            cv2.circle(frame, p1, 5, (255, 205, 40), -1, cv2.LINE_AA)
            cv2.circle(frame, p2, 5, (255, 205, 40), -1, cv2.LINE_AA)

            name = tw.get("name", "Tripwire")
            badge_text = f"TRIPWIRE: {name}"
            self._draw_badge(
                frame,
                badge_text,
                (p1[0], max(26, p1[1] - 10)),
                bg_color=(15, 20, 25),
                text_color=(255, 215, 60),
                border_color=(255, 180, 0),
                font_scale=0.48,
                thickness=2
            )

        # 4. Top Analytics HUD Bar: solid, dark, high-contrast banner
        level_colors = {
            "LOW": (60, 220, 100),      # Vivid Green
            "NORMAL": (0, 205, 255),    # Vibrant Amber
            "HIGH": (40, 50, 255)       # Strong Red
        }
        level_col = level_colors.get(self.current_crowd_level, (60, 220, 100))

        hud_title = f"CROWD: {self.current_crowd_level} ({self.current_crowd_count})  |  OCCUPANCY: {self.occupancy}  |  IN: {self.entries}  OUT: {self.exits}"
        (tw, th), _ = cv2.getTextSize(hud_title, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 2)
        hud_x = max(20, w - tw - 24)
        hud_pos = (hud_x, 32)

        self._draw_badge(
            frame,
            hud_title,
            hud_pos,
            bg_color=(12, 12, 16),
            text_color=level_col,
            border_color=(75, 75, 85),
            font_scale=0.48,
            thickness=2,
            padding=6
        )

        return frame

    def get_summary_state(self) -> Dict[str, Any]:
        """Returns the current analytics state for API telemetry with per-zone and flow breakdowns."""
        now_ts = time.time()
        
        # Per-zone detailed occupancy breakdown (matching reference card format)
        zones_detailed = []
        for zone in self.restricted_zones:
            zid = zone.get("zone_id", "unnamed_zone")
            zname = zone.get("name", zid)
            cap = int(zone.get("capacity", 5))
            active_tids = self.zone_active_tracks.get(zid, [])
            occ = len(active_tids)
            pct = int(min(100.0, (occ / cap) * 100.0)) if cap > 0 else 0
            
            ent_map = self.zone_entry_timestamps.get(zid, {})
            current_dwells = [now_ts - ent_map[t] for t in active_tids if t in ent_map]
            avg_dwell = round(sum(current_dwells) / len(current_dwells), 1) if current_dwells else 0.0
            max_dwell = round(max(current_dwells), 1) if current_dwells else 0.0
            
            peak = max(self.zone_peak_occupants.get(zid, 0), occ)
            self.zone_peak_occupants[zid] = peak
            
            if not zone.get("enabled", True):
                status = "DISABLED"
            elif occ == 0:
                status = "SECURE"
            elif max_dwell >= 8.0:
                status = "LOITERING"
            else:
                status = "INTRUDED"

            zones_detailed.append({
                "zone_id": zid,
                "name": zname,
                "enabled": zone.get("enabled", True),
                "capacity": cap,
                "occupants": occ,
                "occupancy_pct": pct,
                "avg_dwell_sec": avg_dwell,
                "max_dwell_sec": max_dwell,
                "peak_occupants": peak,
                "status": status
            })

        # Advanced tripwire flow analytics
        recent_crossings = [t for t, d in self.crossing_history_timestamps if (now_ts - t) <= 60.0]
        flow_rate = len(recent_crossings)  # crossings/min
        if flow_rate > self.peak_flow_rate:
            self.peak_flow_rate = flow_rate
        recent_in = len([d for t, d in self.crossing_history_timestamps if (now_ts - t) <= 60.0 and d == "ENTRY"])
        recent_out = len([d for t, d in self.crossing_history_timestamps if (now_ts - t) <= 60.0 and d == "EXIT"])
        net_rate = recent_in - recent_out

        tot_crossings = self.entries + self.exits
        inflow_pct = round((self.entries / tot_crossings) * 100.0, 1) if tot_crossings > 0 else 50.0
        outflow_pct = round((self.exits / tot_crossings) * 100.0, 1) if tot_crossings > 0 else 50.0

        tripwire_detailed = {
            "flow_rate_per_min": flow_rate,
            "peak_flow_rate": self.peak_flow_rate,
            "net_flow_pace": ("+" if net_rate >= 0 else "") + f"{net_rate}/min",
            "inflow_pct": inflow_pct,
            "outflow_pct": outflow_pct,
            "total_crossings": tot_crossings
        }

        return {
            "crowd_level": self.current_crowd_level,
            "crowd_count": self.current_crowd_count,
            "entries": self.entries,
            "exits": self.exits,
            "occupancy": self.occupancy,
            "zone_active": any(z.get("enabled", True) for z in self.restricted_zones) if self.restricted_zones else False,
            "zones_count": len(self.restricted_zones),
            "zone_intruded_now": getattr(self, "is_zone_intruded_now", False),
            "current_zone_occupants": getattr(self, "current_zone_occupants", 0),
            "total_intrusions": getattr(self, "total_intrusions", 0),
            "last_intrusion_time": getattr(self, "last_intrusion_time", None),
            "zone_history": list(getattr(self, "zone_event_history", []))[-8:],
            "zones_detailed": zones_detailed,
            "tripwire_active": any(tw.get("enabled", True) for tw in self.virtual_tripwires) if self.virtual_tripwires else False,
            "last_crossing_time": getattr(self, "last_crossing_time", None),
            "last_crossing_direction": getattr(self, "last_crossing_direction", None),
            "tripwire_history": list(getattr(self, "tripwire_event_history", []))[-8:],
            "tripwire_detailed": tripwire_detailed,
            "recent_events": self.emitted_events[-10:] if self.emitted_events else []
        }
