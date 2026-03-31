import json
import random
import uuid
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from utils.version import DB_SCHEMA_VERSION, DEVICE_CREATOR_VERSION

@dataclass
class HiddenParameters:
    silicon_quality: float
    degradation: float
    thermal_resistance: float
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'HiddenParameters':
        return cls(**data)


@dataclass
class HardwareLimits:
    min_frequency: float
    max_frequency: float
    min_voltage: float
    max_voltage: float
    max_safe_temperature: float
    min_fan_speed: float
    max_fan_speed: float
    min_power: float
    max_power: float
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'HardwareLimits':
        return cls(**data)


@dataclass
class ASICModelSpecification:
    name: str
    manufacturer: str
    nominal_hashrate: float
    nominal_power: float
    hashrate_per_mhz: float
    optimal_voltage: float
    base_thermal_resistance: float
    manufacturer_frequency: float
    efficiency: float
    C: float
    hardware_limits: HardwareLimits
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        data['hardware_limits'] = self.hardware_limits.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ASICModelSpecification':
        hl = data['hardware_limits']
        nominal = data['nominal_power']
        if 'min_power' not in hl:
            hl = {**hl, 'min_power': nominal - 500.0}
        if 'max_power' not in hl:
            hl = {**hl, 'max_power': nominal + 500.0}
        data['hardware_limits'] = HardwareLimits.from_dict(hl)
        
        if 'manufacturer_frequency' not in data:
            data['manufacturer_frequency'] = data['hardware_limits'].max_frequency

        # P_wall = P_chip / efficiency, P_chip = C * V^2 * f
        if 'efficiency' not in data:
            data['efficiency'] = 0.92
        if 'C' not in data:
            eff = float(data['efficiency'])
            v = float(data['optimal_voltage'])
            f = float(data['manufacturer_frequency'])
            # Choose C so that P_wall at nominal equals nominal_power
            # nominal_power = (C * v^2 * f) / eff  =>  C = nominal_power * eff / (v^2 * f)
            data['C'] = float(data['nominal_power']) * eff / (v * v * f)
        
        return cls(**data)


@dataclass
class VirtualDevice:
    """
    A virtual ASIC device with unique hidden parameters.
    
    The optimization target for this device is always maximizing actual profit,
    which is calculated as: profit = (hashrate * btc_price) - (power * electricity_price)
    
    Attributes:
        device_id: Unique identifier for the device
        asic_model: ASIC model name
        hidden_parameters: Device-specific hidden parameters
        base_specification: Base ASIC model specification
        electricity_price: Electricity price in USD/kWh (used for profit calculation)
        created_at: Creation timestamp
    """
    device_id: str
    asic_model: str
    hidden_parameters: HiddenParameters
    base_specification: ASICModelSpecification
    electricity_price: float
    created_at: str
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        data['hidden_parameters'] = self.hidden_parameters.to_dict()
        data['base_specification'] = self.base_specification.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'VirtualDevice':
        data['hidden_parameters'] = HiddenParameters.from_dict(data['hidden_parameters'])
        data['base_specification'] = ASICModelSpecification.from_dict(data['base_specification'])
        return cls(**data)


class VirtualDeviceGenerator:
    def __init__(self):
        """Initialize the Virtual Device Generator."""
        self._asic_models: Dict[str, ASICModelSpecification] = {}
        self._devices: Dict[str, VirtualDevice] = {}

    # ------------------------------------------------------------------------
    # SQLite persistence helpers
    # ------------------------------------------------------------------------

    def save_device_to_db(self, device: VirtualDevice, conn: Any) -> None:
        """
        Persist a VirtualDevice into the `devices` table (see init_db.py schema).
        """
        row = flatten_virtual_device_for_db(device)
        row["creator_version"] = DEVICE_CREATOR_VERSION
        row["schema_version"] = DB_SCHEMA_VERSION
        row["is_active"] = 1
        values = (
            row["device_id"],
            row["asic_model"],
            row["electricity_price"],
            row["created_at"],
            row["creator_version"],
            row["schema_version"],
            row["is_active"],
            row["silicon_quality"],
            row["degradation"],
            row["thermal_resistance"],
            row["spec_name"],
            row["spec_manufacturer"],
            row["nominal_hashrate"],
            row["nominal_power"],
            row["hashrate_per_mhz"],
            row["optimal_voltage"],
            row["base_thermal_resistance"],
            row["manufacturer_frequency"],
            row["efficiency"],
            row["C"],
            row["min_frequency"],
            row["max_frequency"],
            row["min_voltage"],
            row["max_voltage"],
            row["max_safe_temperature"],
            row["min_fan_speed"],
            row["max_fan_speed"],
            row["min_power"],
            row["max_power"],
            row["device_json"],
        )
        conn.execute(
            """
            INSERT INTO devices (
                device_id, asic_model, electricity_price, created_at,
                creator_version, schema_version, is_active,
                silicon_quality, degradation, thermal_resistance,
                spec_name, spec_manufacturer, nominal_hashrate, nominal_power, hashrate_per_mhz,
                optimal_voltage, base_thermal_resistance, manufacturer_frequency, efficiency, C,
                min_frequency, max_frequency, min_voltage, max_voltage, max_safe_temperature,
                min_fan_speed, max_fan_speed, min_power, max_power,
                device_json
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?
            )
            ON CONFLICT(device_id) DO UPDATE SET
                asic_model=excluded.asic_model,
                electricity_price=excluded.electricity_price,
                created_at=excluded.created_at,
                creator_version=excluded.creator_version,
                schema_version=excluded.schema_version,
                is_active=excluded.is_active,
                silicon_quality=excluded.silicon_quality,
                degradation=excluded.degradation,
                thermal_resistance=excluded.thermal_resistance,
                spec_name=excluded.spec_name,
                spec_manufacturer=excluded.spec_manufacturer,
                nominal_hashrate=excluded.nominal_hashrate,
                nominal_power=excluded.nominal_power,
                hashrate_per_mhz=excluded.hashrate_per_mhz,
                optimal_voltage=excluded.optimal_voltage,
                base_thermal_resistance=excluded.base_thermal_resistance,
                manufacturer_frequency=excluded.manufacturer_frequency,
                efficiency=excluded.efficiency,
                C=excluded.C,
                min_frequency=excluded.min_frequency,
                max_frequency=excluded.max_frequency,
                min_voltage=excluded.min_voltage,
                max_voltage=excluded.max_voltage,
                max_safe_temperature=excluded.max_safe_temperature,
                min_fan_speed=excluded.min_fan_speed,
                max_fan_speed=excluded.max_fan_speed,
                min_power=excluded.min_power,
                max_power=excluded.max_power,
                device_json=excluded.device_json
            """,
            values,
        )

    def load_device_from_db(self, device_id: str, conn: Any) -> VirtualDevice:
        row = conn.execute(
            "SELECT device_json FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"Device '{device_id}' not found in DB")
        payload = json.loads(row["device_json"])
        device = VirtualDevice.from_dict(payload)
        self._devices[device.device_id] = device
        return device

    def list_device_ids_from_db(self, asic_model: str, conn: Any, limit: int) -> List[str]:
        rows = conn.execute(
            "SELECT device_id FROM devices WHERE asic_model = ? AND is_active = 1 ORDER BY created_at DESC LIMIT ?",
            (asic_model, limit),
        ).fetchall()
        return [r["device_id"] for r in rows]

    def ensure_devices_in_db(
        self,
        asic_model: str,
        conn: Any,
        count: int = 5,
        electricity_price: float = 0.05,
    ) -> List[str]:
        if asic_model not in self._asic_models:
            available = ", ".join(self.get_available_models())
            raise ValueError(f"ASIC model '{asic_model}' not loaded. Available: {available}")

        def is_valid(device_json: str) -> bool:
            try:
                payload = json.loads(device_json)
                device = VirtualDevice.from_dict(payload)
                return device.asic_model == asic_model and bool(device.device_id)
            except Exception:
                return False

        # Start with newest candidates
        # Invalidate devices from older creator_version (soft delete)
        conn.execute(
            "UPDATE devices SET is_active = 0 WHERE asic_model = ? AND creator_version <> ?",
            (asic_model, DEVICE_CREATOR_VERSION),
        )

        candidates = conn.execute(
            "SELECT device_id, device_json FROM devices WHERE asic_model = ? AND is_active = 1 ORDER BY created_at DESC",
            (asic_model,),
        ).fetchall()

        valid_ids: List[str] = []
        invalid_ids: List[str] = []
        for r in candidates:
            if len(valid_ids) >= count:
                break
            if is_valid(r["device_json"]):
                valid_ids.append(r["device_id"])
            else:
                invalid_ids.append(r["device_id"])

        for device_id in invalid_ids:
            conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))

        missing = max(0, count - len(valid_ids))
        for _ in range(missing):
            device = self.generate_device(
                model_name=asic_model,
                hidden_params=self.sample_random_hidden_parameters(asic_model),
                electricity_price=electricity_price,
                apply_thermal_resistance_spread=True,
            )
            self.save_device_to_db(device, conn)
            valid_ids.append(device.device_id)

        conn.commit()
        return valid_ids[:count]
    
    def load_specifications_from_dict(self, specifications: Dict) -> None:
        for model_name, spec_data in specifications.items():
            try:
                spec = ASICModelSpecification.from_dict(spec_data)
                self._asic_models[model_name] = spec
            except Exception as e:
                raise ValueError(f"Invalid specification for model '{model_name}': {e}")
    
    def add_model(self, model: ASICModelSpecification) -> None:
        self._asic_models[model.name] = model
    
    def get_available_models(self) -> List[str]:
        return list(self._asic_models.keys())
    
    def load_builtin_specifications(self) -> None:
        self.load_specifications_from_dict(get_builtin_asic_configurations())

    def sample_random_hidden_parameters(self, model_name: str) -> Dict[str, float]:
        if model_name not in self._asic_models:
            available = ", ".join(self.get_available_models())
            raise ValueError(f"Model '{model_name}' not found. Available: {available}")
        base_tr = self._asic_models[model_name].base_thermal_resistance
        return {
            "silicon_quality": random.uniform(0.92, 1.08),
            "degradation": random.uniform(0.0, 0.05),
            "thermal_resistance": base_tr,
        }

    def generate_device(
        self,
        model_name: str,
        hidden_params: Dict[str, float],
        electricity_price: float = 0.05,
        device_id: Optional[str] = None,
        apply_thermal_resistance_spread: bool = True
    ) -> VirtualDevice:

        if model_name not in self._asic_models:
            available = ', '.join(self.get_available_models())
            raise ValueError(f"Model '{model_name}' not found. Available models: {available}")
        
        required_params = ['silicon_quality', 'degradation', 'thermal_resistance']
        for param in required_params:
            if param not in hidden_params:
                raise ValueError(f"Missing required hidden parameter: {param}")
        
        base_spec = self._asic_models[model_name]
        thermal_resistance = float(hidden_params['thermal_resistance'])
        if apply_thermal_resistance_spread:
            # ±5% uniform spread around base_thermal_resistance
            thermal_resistance = base_spec.base_thermal_resistance * random.uniform(0.95, 1.05)
        
        hidden = HiddenParameters(
            silicon_quality=float(hidden_params['silicon_quality']),
            degradation=float(hidden_params['degradation']),
            thermal_resistance=thermal_resistance
        )
        
        if device_id is None:
            device_id = self._generate_device_id(model_name)
        
        from datetime import datetime, timezone
        device = VirtualDevice(
            device_id=device_id,
            asic_model=model_name,
            hidden_parameters=hidden,
            base_specification=self._asic_models[model_name],
            electricity_price=electricity_price,
            created_at=datetime.now(timezone.utc).isoformat()
        )
        
        self._devices[device_id] = device
        
        return device
    
    def generate_device_from_dict(self, device_data: Dict) -> VirtualDevice:
        device = VirtualDevice.from_dict(device_data)
        self._devices[device.device_id] = device
        return device
    
    def get_device(self, device_id: str) -> VirtualDevice:
        if device_id not in self._devices:
            raise KeyError(f"Device '{device_id}' not found")
        return self._devices[device_id]
    
    def list_devices(self) -> List[str]:
        return list(self._devices.keys())
    
    def _generate_device_id(self, model_name: str) -> str:
        timestamp = str(uuid.uuid4())[:8]
        model_short = model_name.lower().replace(' ', '_').replace('-', '_')
        return f"{model_short}_{timestamp}"
    
    
    def clear_devices(self) -> None:
        self._devices.clear()
    
    def clear_models(self) -> None:
        self._asic_models.clear()
    
    def __repr__(self) -> str:
        return (f"VirtualDeviceGenerator(models={len(self._asic_models)}, "
                f"devices={len(self._devices)})")


def flatten_virtual_device_for_db(device: VirtualDevice) -> Dict[str, object]:
    spec = device.base_specification
    limits = spec.hardware_limits
    hidden = device.hidden_parameters
    payload = device.to_dict()
    return {
        "device_id": device.device_id,
        "asic_model": device.asic_model,
        "electricity_price": float(device.electricity_price),
        "created_at": device.created_at,
        "creator_version": DEVICE_CREATOR_VERSION,
        "schema_version": DB_SCHEMA_VERSION,
        "is_active": 1,
        "silicon_quality": float(hidden.silicon_quality),
        "degradation": float(hidden.degradation),
        "thermal_resistance": float(hidden.thermal_resistance),
        "spec_name": spec.name,
        "spec_manufacturer": spec.manufacturer,
        "nominal_hashrate": float(spec.nominal_hashrate),
        "nominal_power": float(spec.nominal_power),
        "hashrate_per_mhz": float(spec.hashrate_per_mhz),
        "optimal_voltage": float(spec.optimal_voltage),
        "base_thermal_resistance": float(spec.base_thermal_resistance),
        "manufacturer_frequency": float(spec.manufacturer_frequency),
        "efficiency": float(spec.efficiency),
        "C": float(spec.C),
        "min_frequency": float(limits.min_frequency),
        "max_frequency": float(limits.max_frequency),
        "min_voltage": float(limits.min_voltage),
        "max_voltage": float(limits.max_voltage),
        "max_safe_temperature": float(limits.max_safe_temperature),
        "min_fan_speed": float(limits.min_fan_speed),
        "max_fan_speed": float(limits.max_fan_speed),
        "min_power": float(limits.min_power),
        "max_power": float(limits.max_power),
        "device_json": json.dumps(payload, indent=2),
    }


# ============================================================================
# Built-in ASIC configurations storage
# ============================================================================

def get_builtin_asic_configurations() -> Dict:
    return {
        "Antminer S19": {
            "name": "Antminer S19",
            "manufacturer": "Bitmain",
            "nominal_hashrate": 95.0,
            "nominal_power": 3250.0,
            "hashrate_per_mhz": 0.158,
            "optimal_voltage": 13.0,
            "base_thermal_resistance": 0.0265,
            "manufacturer_frequency": 600.0,
            "efficiency": 0.92,
            "C": 0.02948,
            "hardware_limits": {
                "min_frequency": 500.0,
                "max_frequency": 750.0,
                "min_voltage": 11.5,
                "max_voltage": 14.5,
                "max_safe_temperature": 85.0,
                "min_fan_speed": 0.0,
                "max_fan_speed": 100.0,
                "min_power": 2750.0,
                "max_power": 3750.0,
            }
        },
        "Antminer S19 Pro": {
            "name": "Antminer S19 Pro",
            "manufacturer": "Bitmain",
            "nominal_hashrate": 110.0,
            "nominal_power": 3250.0,
            "hashrate_per_mhz": 0.1833,
            "optimal_voltage": 13.2,
            "base_thermal_resistance": 0.0255,
            "manufacturer_frequency": 600.0,
            "efficiency": 0.92,
            "C": 0.02860,
            "hardware_limits": {
                "min_frequency": 500.0,
                "max_frequency": 760.0,
                "min_voltage": 11.6,
                "max_voltage": 14.6,
                "max_safe_temperature": 85.0,
                "min_fan_speed": 0.0,
                "max_fan_speed": 100.0,
                "min_power": 2750.0,
                "max_power": 3850.0,
            },
        },
        "Antminer S19j Pro": {
            "name": "Antminer S19j Pro",
            "manufacturer": "Bitmain",
            "nominal_hashrate": 100.0,
            "nominal_power": 2950.0,
            "hashrate_per_mhz": 0.1667,
            "optimal_voltage": 12.9,
            "base_thermal_resistance": 0.0260,
            "manufacturer_frequency": 600.0,
            "efficiency": 0.92,
            "C": 0.02718,
            "hardware_limits": {
                "min_frequency": 500.0,
                "max_frequency": 740.0,
                "min_voltage": 11.4,
                "max_voltage": 14.4,
                "max_safe_temperature": 85.0,
                "min_fan_speed": 0.0,
                "max_fan_speed": 100.0,
                "min_power": 2450.0,
                "max_power": 3450.0,
            },
        },
    }
