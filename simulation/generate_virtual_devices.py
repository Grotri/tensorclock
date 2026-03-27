from utils.init_db import init_db, connect, default_db_path
from simulation.virtual_device_generator import VirtualDeviceGenerator
from utils.version import DEVICE_CREATOR_VERSION


def generate_virtual_devices_from_templates(
    devices_per_model: int = 5,
    db_path: str = str(default_db_path()),
) -> None:
    init_db(db_path)

    generator = VirtualDeviceGenerator()
    generator.load_builtin_specifications()

    print(f"[OK] Loaded built-in models: {', '.join(generator.get_available_models())}")

    with connect(db_path) as conn:
        for model_name in generator.get_available_models():
            spec = generator._asic_models[model_name]

            print(f"\n[Model] {model_name} ({spec.nominal_hashrate} TH/s @ {spec.nominal_power} W)")

            existing_before = generator.list_device_ids_from_db(model_name, conn, devices_per_model)
            for device_id in existing_before:
                print(f"  - Reuse: {device_id}")

            ids = generator.ensure_devices_in_db(model_name, conn, count=devices_per_model)
            newly_created = [i for i in ids if i not in set(existing_before)]
            for device_id in newly_created:
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

