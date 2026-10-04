"""
Policy Manager and Configuration Loader for DL Streamer Face Anonymization.

Provides an extensible abstraction layer over privacy policies (STANDARD, HIGH_PRIVACY, ZERO_TRUST)
mapping high-level privacy specifications to DL Streamer pipeline configurations.
"""

import os
import json
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class PolicyConfig:
    name: str
    description: str
    anonymization: str
    confidence: float = 0.5
    inference_interval: int = 2
    tracking_type: str = "short-term-imageless"
    roi_padding: str = "0%"
    target_labels: List[str] = field(default_factory=lambda: ["face"])
    enabled: bool = True
    notes: Optional[str] = None

    @property
    def padding_factor(self) -> float:
        """Returns the numerical padding percentage as a float (e.g. 0.25 for 25%)."""
        if self.roi_padding.endswith("%"):
            try:
                return float(self.roi_padding[:-1]) / 100.0
            except ValueError:
                return 0.0
        return 0.0


class PolicyManager:
    """
    Manages discovery, loading, and resolution of privacy policies.
    """

    def __init__(self, config_path: Optional[str] = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not config_path:
            yaml_path = os.path.join(base_dir, "config", "privacy_policies.yaml")
            json_path = os.path.join(base_dir, "config", "privacy_policies.json")
            if os.path.exists(yaml_path) and HAS_YAML:
                config_path = yaml_path
            elif os.path.exists(json_path):
                config_path = json_path
            else:
                config_path = yaml_path
        
        self.config_path = config_path
        self.data: Dict[str, Any] = self._load_raw()
        self.policies: Dict[str, PolicyConfig] = self._parse_policies()
        self.default_policy_name: str = self.data.get("default_policy", "STANDARD")

    def _load_raw(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Policy configuration file not found at: {self.config_path}")
        
        with open(self.config_path, "r", encoding="utf-8") as f:
            if self.config_path.endswith((".yaml", ".yml")) and HAS_YAML:
                return yaml.safe_load(f)
            return json.load(f)

    def _parse_policies(self) -> Dict[str, PolicyConfig]:
        policies_dict = {}
        raw_policies = self.data.get("policies", {})
        for name, cfg in raw_policies.items():
            policies_dict[name.upper()] = PolicyConfig(
                name=name.upper(),
                description=cfg.get("description", ""),
                anonymization=cfg.get("anonymization", "blur"),
                confidence=float(cfg.get("confidence", 0.5)),
                inference_interval=int(cfg.get("inference_interval", 2)),
                tracking_type=cfg.get("tracking_type", "short-term-imageless"),
                roi_padding=str(cfg.get("roi_padding", "0%")),
                target_labels=cfg.get("target_labels", ["face"]),
                enabled=bool(cfg.get("enabled", True)),
                notes=cfg.get("notes")
            )
        return policies_dict

    def get_policy(self, name: Optional[str] = None) -> PolicyConfig:
        policy_name = (name or self.default_policy_name).upper()
        if policy_name not in self.policies:
            available = ", ".join(self.policies.keys())
            raise KeyError(f"Policy '{policy_name}' not recognized. Available: {available}")
        return self.policies[policy_name]

    def list_policies(self) -> Dict[str, PolicyConfig]:
        return self.policies

    def validate_anonymization_support(self, policy: PolicyConfig) -> bool:
        methods = self.data.get("anonymization_methods", {})
        method_meta = methods.get(policy.anonymization, {})
        return method_meta.get("supported_in_pipeline", False)


if __name__ == "__main__":
    import sys
    mgr = PolicyManager()
    target = sys.argv[1] if len(sys.argv) > 1 else None
    policy = mgr.get_policy(target)
    print(json.dumps({
        "policy": policy.name,
        "anonymization": policy.anonymization,
        "confidence": policy.confidence,
        "inference_interval": policy.inference_interval,
        "roi_padding": policy.roi_padding,
        "padding_factor": policy.padding_factor,
        "tracking_type": policy.tracking_type,
        "enabled": policy.enabled,
        "supported_in_pipeline": mgr.validate_anonymization_support(policy),
        "notes": policy.notes
    }, indent=2))
