"""
Utility script for generating virtual ASIC devices from built-in templates.

This script uses VirtualDeviceGenerator and the built-in ASIC configurations
to create virtual devices with randomized hidden parameters, including
the ±5% spread for thermal_resistance.
"""

import random

from init_db import init_db, connect, default_db_path
from virtual_device_generator import VirtualDeviceGenerator


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
    Generate virtual devices for all built-in ASIC templates and store them in SQLite.

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

            # Prefer reusing existing valid devices; generate missing with randomized hidden params
            existing = generator.list_device_ids_from_db(model_name, conn, devices_per_model)
            created = 0
            for device_id in existing:
                print(f"  - Reuse: {device_id}")

            missing = max(0, devices_per_model - len(existing))
            for _ in range(missing):
                hidden_params = generate_hidden_parameters(base_tr)
                device = generator.generate_device(
                    model_name=model_name,
                    hidden_params=hidden_params,
                    electricity_price=0.05,
                    apply_thermal_resistance_spread=True,
                )
                generator.save_device_to_db(device, conn)
                created += 1
                print(
                    f"  - Create: {device.device_id} | "
                    f"SQ={device.hidden_parameters.silicon_quality:.4f}, "
                    f"deg={device.hidden_parameters.degradation:.4f}, "
                    f"TR={device.hidden_parameters.thermal_resistance:.5f} °C/W"
                )

            if created == 0:
                print("  [OK] Enough devices already present in DB")
        conn.commit()

    print(f"\n[OK] Virtual devices stored in SQLite: {db_path}")


def main() -> None:
    generate_virtual_devices_from_templates()


if __name__ == "__main__":
    main()

