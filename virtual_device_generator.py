"""
Virtual Device Generator Module for TensorClock

This module creates virtual ASIC devices with hidden parameters based on provided
research data. It supports loading ASIC specifications from external files or
variables, and enables persistence of virtual devices for reproducible testing.

Author: Senior Python Developer
Date: 2026-03-10
Version: 1.0
"""

import json
import random
import uuid
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from version import DB_SCHEMA_VERSION, DEVICE_CREATOR_VERSION

@dataclass
class HiddenParameters:
    """
    Hidden parameters that represent individual device characteristics.
    These parameters are never exposed to miners during optimization.
    
    Attributes:
        silicon_quality: Multiplier for efficiency (typically 0.92 - 1.08, 5-8% variance)
        degradation: Efficiency loss due to age (0.0 - 0.2, 0-20% loss)
        thermal_resistance: Thermal resistance in °C/W
    """
    silicon_quality: float
    degradation: float
    thermal_resistance: float
    
    def to_dict(self) -> Dict:
        """Convert hidden parameters to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'HiddenParameters':
        """Create hidden parameters from dictionary."""
        return cls(**data)


@dataclass
class HardwareLimits:
    """
    Hardware operational limits for the ASIC device.
    
    Attributes:
        min_frequency: Minimum operating frequency in MHz
        max_frequency: Maximum operating frequency in MHz
        min_voltage: Minimum operating voltage in Volts
        max_voltage: Maximum operating voltage in Volts
        max_safe_temperature: Maximum safe operating temperature in Celsius
        min_fan_speed: Minimum fan speed percentage
        max_fan_speed: Maximum fan speed percentage
        min_power: Minimum power consumption in Watts (nominal_power - 500)
        max_power: Maximum power consumption in Watts (nominal_power + 500)
    """
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
        """Convert hardware limits to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'HardwareLimits':
        """Create hardware limits from dictionary."""
        return cls(**data)


@dataclass
class ASICModelSpecification:
    """
    Base specifications for an ASIC miner model.
    
    Attributes:
        name: Model name (e.g., "Antminer S19 Pro")
        manufacturer: Manufacturer name
        nominal_hashrate: Nominal hashrate in TH/s
        nominal_power: Nominal power consumption in Watts
        hashrate_per_mhz: Hashrate per MHz coefficient
        optimal_voltage: Optimal operating voltage in Volts
        base_thermal_resistance: Base thermal resistance in °C/W
        manufacturer_frequency: Manufacturer specified frequency in MHz (e.g., 600.0 for S19 Pro)
        efficiency: PSU/wall efficiency multiplier (P_wall = P_chip / efficiency)
        C: Switching/dynamic power constant for P_chip = C * V^2 * frequency
        hardware_limits: Hardware operational limits
    """
    name: str
    manufacturer: str
    nominal_hashrate: float  # TH/s
    nominal_power: float  # Watts
    hashrate_per_mhz: float
    optimal_voltage: float  # Volts
    base_thermal_resistance: float  # °C/W
    manufacturer_frequency: float  # MHz
    efficiency: float
    C: float
    hardware_limits: HardwareLimits
    
    def to_dict(self) -> Dict:
        """Convert ASIC specification to dictionary."""
        data = asdict(self)
        data['hardware_limits'] = self.hardware_limits.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ASICModelSpecification':
        """Create ASIC specification from dictionary."""
        hl = data['hardware_limits']
        nominal = data['nominal_power']
        # Backward compatibility: derive min_power/max_power from nominal_power if missing
        if 'min_power' not in hl:
            hl = {**hl, 'min_power': nominal - 500.0}
        if 'max_power' not in hl:
            hl = {**hl, 'max_power': nominal + 500.0}
        data['hardware_limits'] = HardwareLimits.from_dict(hl)
        
        # Handle missing manufacturer_frequency field for backward compatibility
        if 'manufacturer_frequency' not in data:
            # Use max_frequency from hardware limits as a reasonable default
            data['manufacturer_frequency'] = data['hardware_limits'].max_frequency

        # Backward compatibility: derive efficiency and C if missing
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
    electricity_price: float  # USD/kWh
    created_at: str
    
    def to_dict(self) -> Dict:
        """Convert virtual device to dictionary."""
        data = asdict(self)
        data['hidden_parameters'] = self.hidden_parameters.to_dict()
        data['base_specification'] = self.base_specification.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'VirtualDevice':
        """Create virtual device from dictionary."""
        data['hidden_parameters'] = HiddenParameters.from_dict(data['hidden_parameters'])
        data['base_specification'] = ASICModelSpecification.from_dict(data['base_specification'])
        return cls(**data)


class VirtualDeviceGenerator:
    """
    Generator for creating virtual ASIC devices with hidden parameters.
    
    This module creates unique virtual devices based on provided ASIC specifications.
    It does NOT generate parameter values independently - all values must come from
    the provided research data or specifications.
    
    Usage (DB-only):
        generator = VirtualDeviceGenerator()
        generator.load_builtin_specifications()
        # Generate and persist a device to SQLite via save_device_to_db(...)
    """
    
    def __init__(self):
        """Initialize the Virtual Device Generator."""
        self._asic_models: Dict[str, ASICModelSpecification] = {}
        self._devices: Dict[str, VirtualDevice] = {}

    # ------------------------------------------------------------------------
    # SQLite persistence helpers (DB is source of truth)
    # ------------------------------------------------------------------------

    def save_device_to_db(self, device: VirtualDevice, conn: Any) -> None:
        """
        Persist a VirtualDevice into the `devices` table (see init_db.py schema).
        """
        row = flatten_virtual_device_for_db(device)
        # Ensure version tags are always set to current values
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
        """
        Load a VirtualDevice from `devices` by device_id.
        Uses device_json as canonical payload and also stores it in memory cache.
        """
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
        """
        Ensure there are at least `count` valid devices for `asic_model` stored in DB.

        - Reuses existing DB devices if possible.
        - Validates each candidate by parsing device_json into VirtualDevice.
        - If a row is malformed, deletes it and replaces with a newly generated device.

        Returns: list of device_ids (newest-first) of length `count`.
        """
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

        # Delete invalid rows we encountered (best-effort cleanup)
        for device_id in invalid_ids:
            conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))

        # Generate missing devices
        missing = max(0, count - len(valid_ids))
        for _ in range(missing):
            base_tr = self._asic_models[asic_model].base_thermal_resistance
            device = self.generate_device(
                model_name=asic_model,
                hidden_params={
                    "silicon_quality": 1.0,
                    "degradation": 0.0,
                    "thermal_resistance": base_tr,
                },
                electricity_price=electricity_price,
                apply_thermal_resistance_spread=True,
            )
            self.save_device_to_db(device, conn)
            valid_ids.append(device.device_id)

        conn.commit()
        return valid_ids[:count]
    
    def load_specifications_from_dict(self, specifications: Dict) -> None:
        """
        Load ASIC model specifications from a Python dictionary.
        
        Args:
            specifications: Dictionary containing ASIC model specifications.
                          Expected format: {'model_name': {...specification...}}
        
        Raises:
            ValueError: If specification format is invalid
        """
        for model_name, spec_data in specifications.items():
            try:
                spec = ASICModelSpecification.from_dict(spec_data)
                self._asic_models[model_name] = spec
            except Exception as e:
                raise ValueError(f"Invalid specification for model '{model_name}': {e}")
    
    # File-based specification loading removed (DB-only design).
    
    def add_model(self, model: ASICModelSpecification) -> None:
        """
        Add an ASIC model specification directly.
        
        Args:
            model: ASICModelSpecification object to add
        """
        self._asic_models[model.name] = model
    
    def get_available_models(self) -> List[str]:
        """
        Get list of available ASIC model names.
        
        Returns:
            List of model names
        """
        return list(self._asic_models.keys())
    
    def load_builtin_specifications(self) -> None:
        """
        Load ASIC model specifications from the built-in configuration storage.
        Call get_builtin_asic_configurations() for the underlying data.
        """
        self.load_specifications_from_dict(get_builtin_asic_configurations())
    
    def generate_device(
        self,
        model_name: str,
        hidden_params: Dict[str, float],
        electricity_price: float = 0.05,
        device_id: Optional[str] = None,
        apply_thermal_resistance_spread: bool = True
    ) -> VirtualDevice:
        """
        Generate a virtual device with specified hidden parameters.
        
        The optimization target for this device is always maximizing actual profit,
        which is calculated as: profit = (hashrate * btc_price) - (power * electricity_price)
        
        This method creates a virtual device using the provided hidden parameters.
        It does NOT generate any parameter values - all values must be provided.

        Args:
            model_name: Name of the ASIC model to base device on
            hidden_params: Dictionary containing hidden parameter values:
                - silicon_quality: float (typically 0.92 - 1.08)
                - degradation: float (0.0 - 0.2)
                - thermal_resistance: float (°C/W); ignored if apply_thermal_resistance_spread=True
            electricity_price: Electricity price in USD/kWh (default: 0.05)
            device_id: Optional custom device ID. If not provided, generates unique ID.
            apply_thermal_resistance_spread: If True, thermal_resistance is sampled from
                base_specification.base_thermal_resistance with ±5% uniform spread.
        
        Returns:
            VirtualDevice object with specified hidden parameters
        
        Raises:
            ValueError: If model not found or hidden parameters invalid
        """
        # Validate model exists
        if model_name not in self._asic_models:
            available = ', '.join(self.get_available_models())
            raise ValueError(f"Model '{model_name}' not found. Available models: {available}")
        
        # Validate hidden parameters
        required_params = ['silicon_quality', 'degradation', 'thermal_resistance']
        for param in required_params:
            if param not in hidden_params:
                raise ValueError(f"Missing required hidden parameter: {param}")
        
        base_spec = self._asic_models[model_name]
        thermal_resistance = float(hidden_params['thermal_resistance'])
        if apply_thermal_resistance_spread:
            # ±5% uniform spread around base_thermal_resistance
            thermal_resistance = base_spec.base_thermal_resistance * random.uniform(0.95, 1.05)
        
        # Create hidden parameters object
        hidden = HiddenParameters(
            silicon_quality=float(hidden_params['silicon_quality']),
            degradation=float(hidden_params['degradation']),
            thermal_resistance=thermal_resistance
        )
        
        # Generate device ID if not provided
        if device_id is None:
            device_id = self._generate_device_id(model_name)
        
        # Create virtual device
        from datetime import datetime, timezone
        device = VirtualDevice(
            device_id=device_id,
            asic_model=model_name,
            hidden_parameters=hidden,
            base_specification=self._asic_models[model_name],
            electricity_price=electricity_price,
            created_at=datetime.now(timezone.utc).isoformat()
        )
        
        # Store device
        self._devices[device_id] = device
        
        return device
    
    def generate_device_from_dict(self, device_data: Dict) -> VirtualDevice:
        """
        Generate a virtual device from a complete device dictionary.
        
        Args:
            device_data: Dictionary containing complete device specification including:
                - device_id: str
                - asic_model: str
                - hidden_parameters: Dict with all hidden params
                - base_specification: Dict with ASIC model spec
                - created_at: str (optional)
        
        Returns:
            VirtualDevice object
        """
        device = VirtualDevice.from_dict(device_data)
        self._devices[device.device_id] = device
        return device
    
    def get_device(self, device_id: str) -> VirtualDevice:
        """
        Retrieve a stored virtual device by ID.
        
        Args:
            device_id: Device identifier
        
        Returns:
            VirtualDevice object
        
        Raises:
            KeyError: If device not found
        """
        if device_id not in self._devices:
            raise KeyError(f"Device '{device_id}' not found")
        return self._devices[device_id]
    
    def list_devices(self) -> List[str]:
        """
        List all generated device IDs.
        
        Returns:
            List of device IDs
        """
        return list(self._devices.keys())
    
    # File-based device loading removed (DB-only design).
    
    def _generate_device_id(self, model_name: str) -> str:
        """
        Generate a unique device ID.
        
        Args:
            model_name: ASIC model name
        
        Returns:
            Unique device identifier
        """
        timestamp = str(uuid.uuid4())[:8]
        model_short = model_name.lower().replace(' ', '_').replace('-', '_')
        return f"{model_short}_{timestamp}"
    
    
    def clear_devices(self) -> None:
        """Clear all stored virtual devices."""
        self._devices.clear()
    
    def clear_models(self) -> None:
        """Clear all loaded ASIC model specifications."""
        self._asic_models.clear()
    
    def __repr__(self) -> str:
        """String representation of the generator."""
        return (f"VirtualDeviceGenerator(models={len(self._asic_models)}, "
                f"devices={len(self._devices)})")


def flatten_virtual_device_for_db(device: VirtualDevice) -> Dict[str, object]:
    """
    Flatten a VirtualDevice into a DB-ready dict matching init_db.py `devices` table.
    Includes a full JSON snapshot for forward compatibility.
    """
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
    """
    Return built-in ASIC model configurations.
    Use load_builtin_specifications() on VirtualDeviceGenerator to load these.
    silicon_quality and degradation are not stored here; use example ranges
    (e.g. 0.92–1.08, 0.0–0.2) when generating devices.
    """
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


# ============================================================================
# Example Usage and Test Data
# ============================================================================

# def create_example_asic_specifications() -> Dict:
#     """
#     Create example ASIC model specifications based on research data.
    
#     These specifications are based on publicly available data for popular
#     ASIC miners. All values are from actual manufacturer specifications.
    
#     Returns:
#         Dictionary of ASIC model specifications
#     """
#     specifications = {
#         "Antminer S19 Pro": {
#             "name": "Antminer S19 Pro",
#             "manufacturer": "Bitmain",
#             "nominal_hashrate": 110.0,
#             "nominal_power": 3250.0,
#             "hashrate_per_mhz": 0.183,
#             "optimal_voltage": 13.2,
#             "base_thermal_resistance": 0.15,
#             "hardware_limits": {
#                 "min_frequency": 500.0,
#                 "max_frequency": 650.0,
#                 "min_voltage": 11.5,
#                 "max_voltage": 14.5,
#                 "max_safe_temperature": 85.0,
#                 "min_fan_speed": 0.0,
#                 "max_fan_speed": 100.0,
#                 "min_power": 2750.0,
#                 "max_power": 3750.0
#             }
#         },
#         "Antminer S19 XP": {
#             "name": "Antminer S19 XP",
#             "manufacturer": "Bitmain",
#             "nominal_hashrate": 140.0,
#             "nominal_power": 3010.0,
#             "hashrate_per_mhz": 0.233,
#             "optimal_voltage": 13.0,
#             "base_thermal_resistance": 0.14,
#             "hardware_limits": {
#                 "min_frequency": 520.0,
#                 "max_frequency": 680.0,
#                 "min_voltage": 11.8,
#                 "max_voltage": 14.2,
#                 "max_safe_temperature": 85.0,
#                 "min_fan_speed": 0.0,
#                 "max_fan_speed": 100.0,
#                 "min_power": 2510.0,
#                 "max_power": 3510.0
#             }
#         },
#         "Whatsminer M30S++": {
#             "name": "Whatsminer M30S++",
#             "manufacturer": "MicroBT",
#             "nominal_hashrate": 112.0,
#             "nominal_power": 3472.0,
#             "hashrate_per_mhz": 0.187,
#             "optimal_voltage": 13.5,
#             "base_thermal_resistance": 0.16,
#             "hardware_limits": {
#                 "min_frequency": 510.0,
#                 "max_frequency": 660.0,
#                 "min_voltage": 12.0,
#                 "max_voltage": 14.8,
#                 "max_safe_temperature": 85.0,
#                 "min_fan_speed": 0.0,
#                 "max_fan_speed": 100.0,
#                 "min_power": 2972.0,
#                 "max_power": 3972.0
#             }
#         }
#     }
#     return specifications


# def create_example_hidden_parameters() -> List[Dict[str, float]]:
#     """
#     Create example hidden parameter sets based on research data.
    
#     These parameter sets represent realistic variations observed in
#     ASIC manufacturing and aging studies.
    
#     Returns:
#         List of hidden parameter dictionaries
#     """
#     # Example 1: High-quality, new device
#     params_new = {
#         'silicon_quality': 1.05,
#         'degradation': 0.02,
#         'thermal_resistance': 0.142
#     }
    
#     # Example 2: Average quality, moderately used
#     params_avg = {
#         'silicon_quality': 1.00,
#         'degradation': 0.10,
#         'thermal_resistance': 0.150
#     }
    
#     # Example 3: Lower quality, heavily used
#     params_old = {
#         'silicon_quality': 0.94,
#         'degradation': 0.18,
#         'thermal_resistance': 0.165
#     }
    
#     return [params_new, params_avg, params_old]


# def main():
#     """
#     Example usage of the Virtual Device Generator.
    
#     This demonstrates:
#     1. Loading ASIC specifications from a dictionary
#     2. Generating virtual devices with specific hidden parameters
#     3. Saving devices locally for reproducible testing
#     4. Loading devices from files
#     """
#     print("=" * 80)
#     print("Virtual Device Generator - Example Usage")
#     print("=" * 80)
#     print()
    
#     # Initialize generator
#     generator = VirtualDeviceGenerator()
#     print("[OK] Initialized Virtual Device Generator")
#     print()
    
#     # Load ASIC specifications from dictionary
#     print("Step 1: Loading ASIC specifications from dictionary...")
#     specs = create_example_asic_specifications()
#     generator.load_specifications_from_dict(specs)
#     print(f"[OK] Loaded {len(generator.get_available_models())} ASIC models:")
#     for model in generator.get_available_models():
#         spec = generator._asic_models[model]
#         print(f"  - {model}: {spec.nominal_hashrate} TH/s @ {spec.nominal_power}W")
#     print()
    
#     # Generate virtual devices
#     print("Step 2: Generating virtual devices with hidden parameters...")
#     hidden_params_list = create_example_hidden_parameters()
    
#     devices = []
#     for i, hidden_params in enumerate(hidden_params_list, 1):
#         device = generator.generate_device(
#             model_name="Antminer S19 Pro",
#             hidden_params=hidden_params,
#             electricity_price=0.05
#         )
#         devices.append(device)
#         print(f"[OK] Generated device {i}: {device.device_id}")
#         print(f"  - Silicon quality: {device.hidden_parameters.silicon_quality:.3f}")
#         print(f"  - Degradation: {device.hidden_parameters.degradation:.3f}")
#         print(f"  - Thermal resistance: {device.hidden_parameters.thermal_resistance:.3f} °C/W")
#         print(f"  - Electricity price: ${device.electricity_price:.3f} / kWh")
#     print()
    
#     # Save devices to files
#     print("Step 3: Saving devices to local files...")
#     # File persistence removed; use SQLite persistence instead.
    
#     # Clear old device files from the directory
#     if output_dir.exists():
#         print("  - Clearing old device files...")
#         for filepath in output_dir.iterdir():
#             if filepath.is_file():
#                 filepath.unlink()
#         print("  - [OK] Old files cleared")
#     for i, device in enumerate(devices, 1):
#         # Save in different formats
#         # File persistence removed; use SQLite persistence instead.
#         print(f"[OK] Saved device {i} to {output_dir}/{device.device_id}.{{pkl,json}}")
#     print()
    
#     # Export specifications
#     print("Step 4: Exporting ASIC specifications...")
#     # File persistence removed; use built-in specs or your own loader.
#     print(f"[OK] Exported specifications to {output_dir}/asic_specifications.json")
#     print()
    
#     # Load devices from files
#     print("Step 5: Loading devices from files...")
#     # File persistence removed; load devices from SQLite instead.
#     print(f"[OK] Loaded {len(loaded_devices)} devices from files")
#     print()
    
#     # Verify reproducibility
#     print("Step 6: Verifying reproducibility...")
    
#     # Create dictionaries mapping device_id to device for both lists
#     original_dict = {device.device_id: device for device in devices}
#     loaded_dict = {device.device_id: device for device in loaded_devices}
    
#     # Verify all devices were loaded correctly
#     assert len(original_dict) == len(loaded_dict), f"Device count mismatch: {len(original_dict)} original vs {len(loaded_dict)} loaded"
    
#     # Compare each device by device_id
#     for device_id in original_dict:
#         original = original_dict[device_id]
#         loaded = loaded_dict[device_id]
        
#         assert original.device_id == loaded.device_id, f"Device ID mismatch for {device_id}"
#         assert abs(original.hidden_parameters.silicon_quality - loaded.hidden_parameters.silicon_quality) < 1e-10, f"Silicon quality mismatch for {device_id}"
#         assert abs(original.hidden_parameters.degradation - loaded.hidden_parameters.degradation) < 1e-10, f"Degradation mismatch for {device_id}"
#         assert abs(original.hidden_parameters.thermal_resistance - loaded.hidden_parameters.thermal_resistance) < 1e-10, f"Thermal resistance mismatch for {device_id}"
#         assert abs(original.electricity_price - loaded.electricity_price) < 1e-10, f"Electricity price mismatch for {device_id}"
    
#     print(f"[OK] Loaded {len(loaded_devices)} devices match original devices - reproducibility verified!")
#     print()
    
#     # Load specifications from file
#     print("Step 7: Loading specifications from file...")
#     generator2 = VirtualDeviceGenerator()
#     # File persistence removed; load built-in specifications or your own loader.
#     print(f"[OK] Loaded {len(generator2.get_available_models())} models from file")
#     print()
    
#     # Generate device with loaded specifications
#     print("Step 8: Generating device from file-loaded specifications...")
#     device_from_file = generator2.generate_device(
#         model_name="Antminer S19 Pro",
#         hidden_params={
#             'silicon_quality': 0.98,
#             'degradation': 0.05,
#             'thermal_resistance': 0.148
#         },
#         electricity_price=0.05
#     )
#     print(f"[OK] Generated device: {device_from_file.device_id}")
#     print()
    
#     print("=" * 80)
#     print("Example completed successfully!")
#     print("=" * 80)
#     print()
#     print("Summary:")
#     print(f"  - ASIC models loaded: {len(generator.get_available_models())}")
#     print(f"  - Virtual devices generated: {len(devices)}")
#     print(f"  - Devices saved to: {output_dir}/")
#     print(f"  - Reproducibility: Verified [OK]")
#     print()
#     print("The Virtual Device Generator is ready for modular testing!")
#     print()


# if __name__ == "__main__":
#     main()
