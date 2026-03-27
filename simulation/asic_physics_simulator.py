import numpy as np
import matplotlib.pyplot as plt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import warnings
import hashlib
from pathlib import Path

from simulation.virtual_device_generator import (
    VirtualDevice,
    HiddenParameters,
    HardwareLimits,
    ASICModelSpecification,
    VirtualDeviceGenerator
)

from utils.init_db import init_db, connect, default_db_path


class AmbientTemperatureLevel(Enum):
    LEVEL_1 = 15.0
    LEVEL_2 = 20.0
    LEVEL_3 = 25.0
    LEVEL_4 = 30.0
    LEVEL_5 = 35.0


@dataclass
class OptimizationParameters:
    frequency: float
    voltage: float
    fan_speed: float = 100.0


@dataclass
class SimulationOutcome:
    temperature: float
    power: float
    hashrate: float
    efficiency: float
    valid: bool = True
    warning: Optional[str] = None


@dataclass
class FrequencyVoltagePoint:
    frequency: float
    voltage: float
    is_stable: bool = True


class ASICPhysicsSimulator:
    def __init__(self):
        self.device: Optional[VirtualDevice] = None
        self.device_generator = VirtualDeviceGenerator()
        self._ambient_temperatures = {
            AmbientTemperatureLevel.LEVEL_1: 15.0,
            AmbientTemperatureLevel.LEVEL_2: 20.0,
            AmbientTemperatureLevel.LEVEL_3: 25.0,
            AmbientTemperatureLevel.LEVEL_4: 30.0,
            AmbientTemperatureLevel.LEVEL_5: 35.0
        }
        
        self._noise_magnitude = 0.0001
        self._noise_frequency = 0.01
        self._noise_smoothing_runs = 5

        self._fv_coef_below = 0.4
        self._fv_coef_above = 2.5

    def load_device(self, device_id: str, db_path: str = str(default_db_path())) -> None:
        init_db(db_path)
        with connect(db_path) as conn:
            self.device = self.device_generator.load_device_from_db(device_id, conn)
    
    def load_device_from_object(self, device: VirtualDevice) -> None:
        self.device = device
    
    def get_ambient_temperature(self, level: AmbientTemperatureLevel) -> float:
        return self._ambient_temperatures[level]
    
    def calculate_base_voltage(
        self,
        frequency: float,
        silicon_quality: float
    ) -> float:
        if not self.device:
            raise ValueError("No device loaded")
        
        base_freq = self.device.base_specification.manufacturer_frequency
        base_voltage = self.device.base_specification.optimal_voltage
        
        freq_ratio = float(frequency) / base_freq
        
        if freq_ratio < 1.0:
            voltage = base_voltage * (1.0 + 0.05 * self._fv_coef_below * (freq_ratio - 1.0))
        else:
            quadratic_factor = 1.0 + (0.5 * self._fv_coef_above) * ((freq_ratio - 1.0) ** 2)
            voltage = base_voltage * quadratic_factor
        
        return float(voltage)
    
    def apply_undervolting(
        self,
        voltage: float,
        silicon_quality: float,
        frequency: float
    ) -> Tuple[float, bool]:
        if not self.device:
            raise ValueError("No device loaded")

        # Base undervolting range 7–10%, scaled by silicon_quality², then reduced by 1/3
        quality_normalized = (silicon_quality - 0.92) / (1.08 - 0.92)
        base_pct = 0.07 + 0.03 * quality_normalized
        undervolt_percentage = min(0.15, base_pct * (silicon_quality ** 2)) * (2.0 / 3.0)
        
        undervolted_voltage = voltage * (1.0 - undervolt_percentage)
        
        max_freq = self.device.base_specification.hardware_limits.max_frequency
        freq_ratio = frequency / max_freq
        
        stability_threshold = 0.95 - 0.1 * freq_ratio
        is_stable = undervolt_percentage < stability_threshold
        
        return undervolted_voltage, is_stable
    
    def add_voltage_noise(
        self,
        voltage: float,
        frequency: float,
        seed: Optional[int] = None
    ) -> float:
        rng = np.random.RandomState(seed)
        
        base_noise = rng.normal(0, self._noise_magnitude * voltage)
        
        if rng.random() < self._noise_frequency:
            spike_magnitude = rng.uniform(0.01, 0.03) * voltage
            spike_direction = 1 if rng.random() > 0.5 else -1
            spike = spike_magnitude * spike_direction
        else:
            spike = 0.0
        
        nominal_freq = getattr(
            self.device.base_specification, 'manufacturer_frequency', 600.0
        ) if self.device else 600.0
        freq_factor = float(frequency) / nominal_freq
        freq_noise = rng.normal(0, 0.005 * voltage * freq_factor)
        
        noisy_voltage = voltage + base_noise + spike + freq_noise
        
        return noisy_voltage
    
    def generate_frequency_voltage_curve(
        self,
        ambient_level: AmbientTemperatureLevel = AmbientTemperatureLevel.LEVEL_3,
        add_noise: bool = False,
        apply_undervolting_opt: bool = True,
        num_points: int = 50,
        seed: Optional[int] = None
    ) -> List[FrequencyVoltagePoint]:
        if not self.device:
            raise ValueError("No device loaded")
        
        hidden = self.device.hidden_parameters
        limits = self.device.base_specification.hardware_limits
        max_freq = limits.max_frequency

        # Undervolting: base 7–10% × silicon_quality², reduced by 1/3
        quality_normalized = (hidden.silicon_quality - 0.92) / (1.08 - 0.92)
        base_pct = 0.07 + 0.03 * quality_normalized
        undervolt_percentage = min(0.15, base_pct * (hidden.silicon_quality ** 2)) * (2.0 / 3.0)

        frequencies = np.linspace(limits.min_frequency, limits.max_frequency, num_points)
        curve = []

        for i, freq in enumerate(frequencies):
            freq_scalar = float(freq)
            point_seed = seed + i if seed is not None else None

            base_voltage = self.calculate_base_voltage(freq_scalar, hidden.silicon_quality)

            if apply_undervolting_opt:
                voltage = base_voltage * (1.0 - undervolt_percentage)
                freq_ratio = freq_scalar / max_freq
                stability_threshold = 0.95 - 0.1 * freq_ratio
                is_stable = undervolt_percentage < stability_threshold
            else:
                voltage = base_voltage
                is_stable = True

            if add_noise:
                if seed is None:
                    voltage = self._smoothed_noisy_voltage(
                        voltage=voltage,
                        frequency=freq_scalar,
                    )
                else:
                    voltage = self.add_voltage_noise(voltage, freq_scalar, point_seed)

            voltage = float(np.clip(voltage, limits.min_voltage, limits.max_voltage))

            curve.append(FrequencyVoltagePoint(
                frequency=freq_scalar,
                voltage=voltage,
                is_stable=is_stable
            ))

        return curve

    def _deterministic_voltage_noise_seed(self, frequency: float) -> int:
        if not self.device:
            raise ValueError("No device loaded")
        key = f"{self.device.device_id}|{self.device.asic_model}|{frequency:.6f}"
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % (2**32)

    def _smoothed_noisy_voltage(self, *, voltage: float, frequency: float, runs: Optional[int] = None) -> float:
        n = int(runs if runs is not None else self._noise_smoothing_runs)
        if n <= 1:
            seed = self._deterministic_voltage_noise_seed(frequency)
            return float(self.add_voltage_noise(voltage, frequency, seed=seed))
        base_seed = self._deterministic_voltage_noise_seed(frequency)
        vals: List[float] = []
        for i in range(n):
            vals.append(float(self.add_voltage_noise(voltage, frequency, seed=base_seed + i)))
        return float(np.mean(vals))

    def _required_min_voltage_at_frequency(
        self,
        *,
        frequency: float,
        hidden: HiddenParameters,
        limits: HardwareLimits,
    ) -> float:
        base_voltage = self.calculate_base_voltage(frequency, hidden.silicon_quality)
        undervolted_voltage, _is_stable = self.apply_undervolting(
            base_voltage,
            hidden.silicon_quality,
            frequency,
        )
        noisy_voltage = self._smoothed_noisy_voltage(
            voltage=undervolted_voltage,
            frequency=frequency,
        )
        return float(np.clip(noisy_voltage, limits.min_voltage, limits.max_voltage))
    
    def plot_frequency_voltage_curve(
        self,
        curve: List[FrequencyVoltagePoint],
        save_path: str,
        title: str = "Frequency/Voltage Curve"
    ) -> None:
        frequencies = [point.frequency for point in curve]
        voltages = [point.voltage for point in curve]
        
        plt.figure(figsize=(10, 6))
        plt.plot(frequencies, voltages, 'b-', linewidth=2, label='F/V Curve')
        
        unstable_freqs = [p.frequency for p in curve if not p.is_stable]
        unstable_volts = [p.voltage for p in curve if not p.is_stable]
        if unstable_freqs:
            plt.scatter(unstable_freqs, unstable_volts, c='red', s=50, 
                       label='Unstable (Undervolting)', zorder=5)
        
        plt.xlabel('Frequency (MHz)', fontsize=12)
        plt.ylabel('Voltage (V)', fontsize=12)
        plt.title(title, fontsize=14, fontweight='bold')
        plt.grid(True, alpha=0.3)
        plt.legend(fontsize=10)
        
        if self.device:
            info_text = f"Device: {self.device.device_id}\n"
            info_text += f"Silicon Quality: {self.device.hidden_parameters.silicon_quality:.3f}\n"
            info_text += f"Degradation: {self.device.hidden_parameters.degradation:.3f}"
            plt.text(0.02, 0.98, info_text, transform=plt.gca().transAxes,
                    fontsize=9, verticalalignment='top',
                    bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
        
        plt.tight_layout()
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        plt.close()
    
    def simulate(
        self,
        ambient_level: AmbientTemperatureLevel,
        params: OptimizationParameters
    ) -> SimulationOutcome:
        if not self.device:
            raise ValueError("No device loaded")
        
        hidden = self.device.hidden_parameters
        spec = self.device.base_specification
        limits = spec.hardware_limits
        
        ambient_temp = self.get_ambient_temperature(ambient_level)
        
        if params.frequency < limits.min_frequency or params.frequency > limits.max_frequency:
            return SimulationOutcome(
                temperature=ambient_temp,
                power=0,
                hashrate=0,
                efficiency=float('inf'),
                valid=False,
                warning=f"Frequency {params.frequency} MHz outside limits [{limits.min_frequency}, {limits.max_frequency}]"
            )
        
        if params.voltage < limits.min_voltage or params.voltage > limits.max_voltage:
            return SimulationOutcome(
                temperature=ambient_temp,
                power=0,
                hashrate=0,
                efficiency=float('inf'),
                valid=False,
                warning=f"Voltage {params.voltage} V outside limits [{limits.min_voltage}, {limits.max_voltage}]"
            )

        min_required_voltage = self._required_min_voltage_at_frequency(
            frequency=float(params.frequency),
            hidden=hidden,
            limits=limits,
        )
        if float(params.voltage) < float(min_required_voltage):
            return SimulationOutcome(
                temperature=ambient_temp,
                power=0,
                hashrate=0,
                efficiency=float('inf'),
                valid=False,
                warning=(
                    f"Voltage {params.voltage:.4f} V is below required minimum "
                    f"{min_required_voltage:.4f} V at frequency {params.frequency:.2f} MHz"
                ),
            )
        
        temperature = self._calculate_temperature(params, ambient_temp, hidden, limits, spec)
        
        warning = None
        if temperature > limits.max_safe_temperature:
            warning = (f"CRITICAL TEMPERATURE WARNING: {temperature:.1f}°C exceeds "
                      f"safe limit of {limits.max_safe_temperature}°C. "
                      f"Simulation continues but results may be unreliable.")
        
        power = self._calculate_power(params, temperature, hidden, spec)
        
        if power < limits.min_power or power > limits.max_power:
            return SimulationOutcome(
                temperature=temperature,
                power=power,
                hashrate=0,
                efficiency=float('inf'),
                valid=False,
                warning=(f"Power {power:.1f} W outside limits "
                         f"[{limits.min_power:.0f}, {limits.max_power:.0f}]")
            )
        
        hashrate = self._calculate_hashrate(params, temperature, hidden, spec)
        
        efficiency = power / hashrate if hashrate > 0 else float('inf')
        
        return SimulationOutcome(
            temperature=temperature,
            power=power,
            hashrate=hashrate,
            efficiency=efficiency,
            valid=(warning is None),
            warning=warning
        )
    
    def _calculate_temperature(
        self,
        params: OptimizationParameters,
        ambient_temp: float,
        hidden: HiddenParameters,
        limits: HardwareLimits,
        spec: ASICModelSpecification
    ) -> float:
        """
        Temperature Model:
        T = T_ambient + (P × R_thermal) / (1 + 0.5 × fan_speed/100)
        """

        # P_chip = C * V^2 * frequency
        p_chip = spec.C * (params.voltage ** 2) * params.frequency
        effective_power = p_chip

        temp_rise = effective_power * hidden.thermal_resistance

        cooling_efficiency = params.fan_speed / 100.0
        temp_rise /= (1 + (0.97 * cooling_efficiency))

        temperature = ambient_temp + temp_rise

        return temperature
    
    def _calculate_power(
        self,
        params: OptimizationParameters,
        temperature: float,
        hidden: HiddenParameters,
        spec: ASICModelSpecification
    ) -> float:
        """
        Power Model:
        P_wall = P_chip / efficiency
        P_chip = C * V^2 * frequency
        """
        p_chip = spec.C * (params.voltage ** 2) * params.frequency
        power_wall = p_chip / spec.efficiency
        return float(power_wall)
    
    def _calculate_hashrate(
        self,
        params: OptimizationParameters,
        temperature: float,
        hidden: HiddenParameters,
        spec: ASICModelSpecification
    ) -> float:
        """
        Hashrate Model:
        HR = f × HR_per_mhz × silicon_quality × (1 - degradation) × 
            temp_penalty × voltage_efficiency
        """
        base_hashrate = params.frequency * spec.hashrate_per_mhz

        hashrate = base_hashrate * hidden.silicon_quality

        hashrate *= (1 - hidden.degradation)

        return hashrate
