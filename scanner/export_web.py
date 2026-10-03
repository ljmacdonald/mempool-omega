"""Export everything the browser app needs into state/web/ (served to the site from the repo).

* model_<style>.json - LightGBM trees in a compact array form the site evaluates in JavaScript
* config.json        - trading speeds, exit rules, universe filters, feature list
* fixture.json       - (optional) inputs + expected outputs used to test the JavaScript port
"""
from __future__ import annotations

import json

from core.config import state_path
from scanner.data import config as universe_config
from scanner.features import FEATURES
from scanner.live import models_dir
from scanner.model import COST, PT, SL, ScannerModel
from scanner.rank import FEE_R, GRADES
from scanner.styles import DEFAULT_STYLE, STYLES

MISSING = {"None": 0, "Zero": 1, "NaN": 2}


def _compact_tree(node: dict, nodes: list) -> int:
    """Flatten a LightGBM tree. Internal node: [feature, threshold, default_left, missing_type, left, right];
    leaf: [-1, value]. Returns this node's index."""
    idx = len(nodes)
    nodes.append(None)
    if "leaf_value" in node:
        nodes[idx] = [-1, round(node["leaf_value"], 9)]
        return idx
    assert node["decision_type"] == "<="
    left = _compact_tree(node["left_child"], nodes)
    right = _compact_tree(node["right_child"], nodes)
    nodes[idx] = [node["split_feature"], node["threshold"], int(node["default_left"]),
                  MISSING[node["missing_type"]], left, right]
    return idx


def export_model(m: ScannerModel) -> dict:
    dump = m.booster.dump_model()
    assert dump["feature_names"] == FEATURES, "feature order changed"
    trees = []
    for t in dump["tree_info"]:
        nodes: list = []
        _compact_tree(t["tree_structure"], nodes)
        trees.append(nodes)
    return {"style": m.style, "features": FEATURES, "trees": trees, "win_r": m.win_r, "loss_r": m.loss_r,
            "base_rate": m.base_rate, "info": m.info}


def export_all() -> list[str]:
    out = []
    for key in STYLES:
        if ScannerModel.exists(models_dir(), key):
            p = state_path("web", f"model_{key}.json")
            p.write_text(json.dumps(export_model(ScannerModel.load(models_dir(), key)), separators=(",", ":")))
            out.append(str(p))
    cfg = {"styles": {k: {**s.__dict__, "label": s.label, "hold_text": s.hold_text} for k, s in STYLES.items()},
           "default_style": DEFAULT_STYLE, "pt": PT, "sl": SL, "cost": COST, "fee_r": FEE_R,
           "grades": GRADES, "universe": universe_config(), "features": FEATURES}
    state_path("web", "config.json").write_text(json.dumps(cfg, indent=1))
    return out


if __name__ == "__main__":
    print(export_all())
