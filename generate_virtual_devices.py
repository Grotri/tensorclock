"""
Utility script for generating virtual ASIC devices from built-in templates.

This script uses VirtualDeviceGenerator and the built-in ASIC configurations
to create virtual devices with randomized hidden parameters, including
the ±5% spread for thermal_resistance.
"""

from pathlib import Path
import random

from virtual_device_generator import VirtualDeviceGenerator


def generate_hidden_parameters(base_thermal_resistance: float) -> dict:
    """
    Generate a random hidden-parameters set for a virtual device.

    - silicon_quality: uniform in [0.92, 1.08]
    - degradation: uniform in [0.0, 0.2]
    - thermal_resistance: seed value; actual value will be randomized
      in generate_device via apply_thermal_resistance_spread=True.
    """
    silicon_quality = random.uniform(0.92, 1.08)
    degradation = random.uniform(0.0, 0.2)

    return {
        "silicon_quality": silicon_quality,
        "degradation": degradation,
        "thermal_resistance": base_thermal_resistance,
    }


def generate_virtual_devices_from_templates(
    output_dir: Path | str = Path("virtual_devices"),
    devices_per_model: int = 5,
) -> None:
    """
    Generate virtual devices for all built-in ASIC templates and save them as JSON.

    All randomness configured in the generator is preserved:
    - Hidden parameters use random silicon_quality and degradation.
    - thermal_resistance uses the ±5% spread around base_thermal_resistance
      via apply_thermal_resistance_spread=True.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    generator = VirtualDeviceGenerator()
    generator.load_builtin_specifications()

    print(f"[OK] Loaded built-in models: {', '.join(generator.get_available_models())}")

    for model_name in generator.get_available_models():
        spec = generator._asic_models[model_name]
        base_tr = spec.base_thermal_resistance

        print(f"\n[Model] {model_name} ({spec.nominal_hashrate} TH/s @ {spec.nominal_power} W)")

        for i in range(devices_per_model):
            hidden_params = generate_hidden_parameters(base_tr)
            device = generator.generate_device(
                model_name=model_name,
                hidden_params=hidden_params,
                electricity_price=0.05,
                apply_thermal_resistance_spread=True,
            )

            json_path = output_dir / f"{device.device_id}.json"
            generator.save_device(device, json_path, format="json")

            print(
                f"  - Device {i + 1}: {device.device_id} | "
                f"SQ={device.hidden_parameters.silicon_quality:.4f}, "
                f"deg={device.hidden_parameters.degradation:.4f}, "
                f"TR={device.hidden_parameters.thermal_resistance:.5f} °C/W"
            )

    print(f"\n[OK] Virtual devices saved to: {output_dir}")


def main() -> None:
    generate_virtual_devices_from_templates()


if __name__ == "__main__":
    main()

