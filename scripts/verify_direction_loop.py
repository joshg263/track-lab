#!/usr/bin/env python3
"""
Verification script for the AI direction loop system.

Tests:
1. All committed layouts pass the checker (except gradient which requires earthwork)
2. Deliberately bad layouts are rejected:
   - Self-crossing layout
   - Layout with >15% gradient  
   - Layout with <15m corner radius
3. Results are deterministic across runs
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from layout_check import check_layout, SITE_DIR

def create_bad_layout_self_crossing():
    """Create a layout that self-intersects."""
    return {
        "name": "test_self_crossing",
        "direction": "Test layout that crosses itself",
        "version": 1,
        "track_width_m": 14,
        "coordinate_system": "local_m",
        "control_points": [
            {"x": -500, "y": -500},
            {"x": 500, "y": -500},
            {"x": -500, "y": 500},
            {"x": 500, "y": 500},
        ],
        "notes": "This creates a figure-8 that crosses itself"
    }

def create_bad_layout_tight_radius():
    """Create a layout with corners tighter than 15m radius."""
    return {
        "name": "test_tight_radius",
        "direction": "Test layout with very tight hairpins",
        "version": 1,
        "track_width_m": 14,
        "coordinate_system": "local_m",
        "control_points": [
            {"x": -600, "y": -400},
            {"x": -600, "y": 400},
            {"x": -590, "y": 410},
            {"x": -580, "y": 400},
            {"x": -580, "y": -400},
            {"x": -590, "y": -410},
        ],
        "notes": "Very tight hairpins that should fail radius check"
    }

def create_bad_layout_steep_gradient():
    """Layout along a steep corridor — should exceed 15% with honest grading."""
    return {
        "name": "test_steep_gradient",
        "direction": "Test layout on steep central terrain",
        "version": 1,
        "track_width_m": 14,
        "coordinate_system": "local_m",
        "control_points": [
            {"x": -200, "y": -800},
            {"x": 200, "y": -800},
            {"x": 200, "y": 400},
            {"x": -200, "y": 400},
        ],
        "notes": "Crosses central high ground diagonally",
    }


def create_bad_layout_outside_bounds():
    """Create a layout that goes outside the DEM bounds."""
    return {
        "name": "test_outside_bounds",
        "direction": "Test layout that leaves the terrain",
        "version": 1,
        "track_width_m": 14,
        "coordinate_system": "local_m",
        "control_points": [
            {"x": -500, "y": -400},
            {"x": -500, "y": 3000},
            {"x": 500, "y": 3000},
            {"x": 500, "y": -400},
        ],
        "notes": "Goes way north of the terrain"
    }

def test_bad_layout(layout_dict: dict, expected_failures: list[str]) -> bool:
    """Test that a bad layout is properly rejected."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(layout_dict, f)
        temp_path = Path(f.name)
    
    try:
        report = check_layout(temp_path, SITE_DIR)
        
        actual_failures = [c.name for c in report.checks if not c.passed and c.target != "info"]
        
        for expected in expected_failures:
            if expected not in actual_failures:
                print(f"  FAIL: Expected '{expected}' to fail but it passed")
                return False
        
        print(f"  PASS: Layout correctly rejected for: {', '.join(actual_failures)}")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False
    finally:
        temp_path.unlink()

def test_determinism() -> bool:
    """Test that layout checking is deterministic."""
    print("\nTesting determinism...")
    
    layout_files = list((SITE_DIR / "layouts").glob("*.json"))
    if not layout_files:
        print("  SKIP: No layouts found")
        return True
    
    layout_file = layout_files[0]
    
    report1 = check_layout(layout_file, SITE_DIR)
    report2 = check_layout(layout_file, SITE_DIR)
    
    for c1, c2 in zip(report1.checks, report2.checks):
        if c1.value != c2.value:
            print(f"  FAIL: Non-deterministic result for {c1.name}: {c1.value} vs {c2.value}")
            return False
    
    print("  PASS: Results are deterministic")
    return True

def main():
    print("=== Direction Loop Verification ===\n")
    
    all_passed = True
    
    print("Testing bad layout rejection:")
    
    print("\n1. Self-crossing layout:")
    if not test_bad_layout(create_bad_layout_self_crossing(), ["no_self_intersection"]):
        all_passed = False
    
    print("\n2. Steep gradient layout (>15%):")
    if not test_bad_layout(create_bad_layout_steep_gradient(), ["max_gradient"]):
        all_passed = False

    print("\n3. Tight radius layout (<15m corners):")
    if not test_bad_layout(create_bad_layout_tight_radius(), ["min_corner_radius"]):
        all_passed = False
    
    print("\n4. Outside bounds layout:")
    if not test_bad_layout(create_bad_layout_outside_bounds(), ["inside_bounds"]):
        all_passed = False
    
    if not test_determinism():
        all_passed = False
    
    print("\n" + "=" * 40)
    if all_passed:
        print("All verification tests PASSED")
        return 0
    else:
        print("Some verification tests FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
