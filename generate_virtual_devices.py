"""
Utility script for generating virtual ASIC devices from built-in templates.

This script uses VirtualDeviceGenerator and the built-in ASIC configurations
to create virtual devices with randomized hidden parameters, including
the ±5% spread for thermal_resistance.
"""

import random

from init_db import init_db, connect, default_db_path
from virtual_device_generator import VirtualDeviceGenerator
from version import DEVICE_CREATOR_VERSION


def generate_hidden_parameters(base_thermal_resistance: float) -> dict:
    """
    Generate a random hidden-parameters set for a virtual device.

    - silicon_quality: uniform in [0.92, 1.08]
    - degradation: uniform in [0.0, 0.05] (up to 5%)
    - thermal_resistance: seed value; actual value will be randomized
      in generate_device via apply_thermal_resistance_spread=True.
    """
    silicon_quality = random.uniform(0.92, 1.08)
    degradation = random.uniform(0.0, 0.05)

    return {
        "silicon_quality": silicon_quality,
        "degradation": degradation,
        "thermal_resistance": base_thermal_resistance,
    }


def generate_virtual_devices_from_templates(
    devices_per_model: int = 5,
    db_path: str = str(default_db_path()),
) -> None:
    """
    Generate virtual devices for all built-in ASIC templates and store them in DB.

    All randomness configured in the generator is preserved:
    - Hidden parameters use random silicon_quality and degradation.
    - thermal_resistance uses the ±5% spread around base_thermal_resistance
      via apply_thermal_resistance_spread=True.
    """
    init_db(db_path)

    generator = VirtualDeviceGenerator()
    generator.load_builtin_specifications()

    print(f"[OK] Loaded built-in models: {', '.join(generator.get_available_models())}")

    with connect(db_path) as conn:
        for model_name in generator.get_available_models():
            spec = generator._asic_models[model_name]
            base_tr = spec.base_thermal_resistance

            print(f"\n[Model] {model_name} ({spec.nominal_hashrate} TH/s @ {spec.nominal_power} W)")

            # Ensure DB has enough valid devices for current creator_version
            existing_before = generator.list_device_ids_from_db(model_name, conn, devices_per_model)
            for device_id in existing_before:
                print(f"  - Reuse: {device_id}")

            ids = generator.ensure_devices_in_db(model_name, conn, count=devices_per_model)
            newly_created = [i for i in ids if i not in set(existing_before)]
            for device_id in newly_created:
                # Load to display its randomized params
                dev = generator.load_device_from_db(device_id, conn)
                print(
                    f"  - Create: {dev.device_id} | "
                    f"SQ={dev.hidden_parameters.silicon_quality:.4f}, "
                    f"deg={dev.hidden_parameters.degradation:.4f}, "
                    f"TR={dev.hidden_parameters.thermal_resistance:.5f} °C/W"
                )

            if not newly_created:
                print(f"  [OK] Enough devices already present in DB (creator_version={DEVICE_CREATOR_VERSION})")
        conn.commit()

    print(f"\n[OK] Virtual devices stored in DB: {db_path}")


def main() -> None:
    generate_virtual_devices_from_templates()


if __name__ == "__main__":
    main()

