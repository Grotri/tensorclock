"""
ASIC Physics Simulator Module for TensorClock

This module simulates ASIC mining behavior using device-specific hidden parameters.
It implements realistic physical models for temperature, power, hashrate, and
Frequency/Voltage relationships with noise and undervolting optimization.

Author: Senior Python Developer
Date: 2026-03-11
Version: 1.0
"""

import numpy as np
import matplotlib.pyplot as plt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import warnings
from pathlib import Path

# Import from virtual_device_generator
from virtual_device_generator import (
    VirtualDevice,
    HiddenParameters,
    HardwareLimits,
    ASICModelSpecification,
    VirtualDeviceGenerator
)


class AmbientTemperatureLevel(Enum):
    """Ambient temperature levels for simulation."""
    LEVEL_1 = 15.0  # Cold environment
    LEVEL_2 = 20.0  # Cool environment
    LEVEL_3 = 25.0  # Standard room temperature
    LEVEL_4 = 30.0  # Warm environment
    LEVEL_5 = 35.0  # Hot environment


@dataclass
class OptimizationParameters:
    """
    Parameters for ASIC optimization.
    
    Attributes:
        frequency: Operating frequency in MHz
        voltage: Operating voltage in Volts
        fan_speed: Fan speed percentage (0-100)
    """
    frequency: float
    voltage: float
    fan_speed: float = 100.0


@dataclass
class SimulationOutcome:
    """
    Result of a simulation run.
    
    Attributes:
        temperature: Chip temperature in Celsius
        power: Power consumption in Watts
        hashrate: Hashrate in TH/s
        efficiency: Efficiency in J/TH
        valid: Whether parameters are valid (not critical temperature)
        warning: Warning message if any issues occurred
    """
    temperature: float
    power: float
    hashrate: float
    efficiency: float
    valid: bool = True
    warning: Optional[str] = None


@dataclass
class FrequencyVoltagePoint:
    """
    A point on the Frequency/Voltage curve.
    
    Attributes:
        frequency: Frequency in MHz
        voltage: Voltage in Volts
        is_stable: Whether the point is stable (no undervolting issues)
    """
    frequency: float
    voltage: float
    is_stable: bool = True


class ASICPhysicsSimulator:
    """
    Physics-based simulator for ASIC mining devices.
    
    This simulator implements realistic physical models including:
    - Quadratic Frequency/Voltage relationship
    - Silicon lottery multiplier effects
    - Temperature-dependent performance
    - Noise generation for system instability
    - Undervolting optimization support
    - Critical temperature warnings (non-fatal)
    
    Usage:
        simulator = ASICPhysicsSimulator()
        simulator.load_device('virtual_devices/device_001.pkl')
        
        # Run simulation
        params = OptimizationParameters(frequency=600, voltage=13.0, fan_speed=100)
        outcome = simulator.simulate(
            ambient_level=AmbientTemperatureLevel.LEVEL_3,
            params=params
        )
        
        # Generate F/V curve
        curve = simulator.generate_frequency_voltage_curve(
            ambient_level=AmbientTemperatureLevel.LEVEL_3,
            add_noise=True
        )
    """
    
    def __init__(self):
        """Initialize the ASIC Physics Simulator."""
        self.device: Optional[VirtualDevice] = None
        self.device_generator = VirtualDeviceGenerator()
        self._ambient_temperatures = {
            AmbientTemperatureLevel.LEVEL_1: 15.0,
            AmbientTemperatureLevel.LEVEL_2: 20.0,
            AmbientTemperatureLevel.LEVEL_3: 25.0,
            AmbientTemperatureLevel.LEVEL_4: 30.0,
            AmbientTemperatureLevel.LEVEL_5: 35.0
        }
        
        # Noise parameters for system instability
        self._noise_magnitude = 0.0001  # 2% voltage noise
        self._noise_frequency = 0.01   # Probability of noise spike
        
    def load_device(self, device_id: str, device_path: Optional[str] = None) -> None:
        """
        Load a virtual device into the simulator.
        
        Args:
            device_id: Unique identifier for the device
            device_path: Optional path to device file. If None, searches in virtual_devices/
        """
        if device_path:
            self.device = self.device_generator.load_device(device_path)
        else:
            # Try to load from virtual_devices directory (prioritize JSON for compatibility)
            possible_paths = [
                f"virtual_devices/{device_id}.json",
                f"virtual_devices/{device_id}.pkl",
            ]
            
            for path in possible_paths:
                try:
                    self.device = self.device_generator.load_device(path)
                    break
                except FileNotFoundError:
                    continue
            
            if self.device is None:
                raise FileNotFoundError(f"Device '{device_id}' not found in virtual_devices/")
        
        # Validate device model
        if self.device.asic_model != "Antminer S19 Pro":
            warnings.warn(f"Simulator optimized for S19 Pro, but loaded {self.device.asic_model}")
    
    def load_device_from_object(self, device: VirtualDevice) -> None:
        """
        Load a virtual device directly from a VirtualDevice object.
        
        Args:
            device: VirtualDevice object to load
        """
        self.device = device
    
    def get_ambient_temperature(self, level: AmbientTemperatureLevel) -> float:
        """
        Get ambient temperature for a given level.
        
        Args:
            level: Ambient temperature level
            
        Returns:
            Ambient temperature in Celsius
        """
        return self._ambient_temperatures[level]
    
    def calculate_base_voltage(
        self,
        frequency: float,
        silicon_quality: float
    ) -> float:
        """
        Calculate base voltage for a given frequency using quadratic relationship.
        
        The relationship is roughly quadratic: V ∝ f²
        Silicon lottery multiplier affects the base voltage requirement.
        
        Lower frequencies (below manufacturer frequency) keep voltage around 0.95 of base voltage.
        Higher frequencies (above manufacturer frequency) show quadratic voltage growth.
        
        Args:
            frequency: Frequency in MHz
            silicon_quality: Silicon quality multiplier (0.92 - 1.08)
            
        Returns:
            Required voltage in Volts
        """
        if not self.device:
            raise ValueError("No device loaded")
        
        # Base frequency and voltage from device specification
        base_freq = self.device.base_specification.manufacturer_frequency  # MHz
        base_voltage = self.device.base_specification.optimal_voltage  # Volts
        
        # Silicon lottery: better quality needs less voltage
        # silicon_quality > 1.0 means better silicon, needs less voltage
        silicon_multiplier = 1.0 / silicon_quality
        
        # Calculate frequency ratio relative to base frequency
        freq_ratio = frequency / base_freq
        
        # Piecewise quadratic relationship:
        # - For frequencies below manufacturer frequency: voltage stays around 0.95 of base
        # - For frequencies above manufacturer frequency: voltage increases quadratically
        if freq_ratio < 1.0:
            # Lower frequencies: keep voltage around 0.95 of base (undervolting benefit)
            # Slight linear reduction as frequency decreases
            voltage = base_voltage * (0.95 + 0.05 * freq_ratio)
        else:
            # Higher frequencies: quadratic growth
            # V = V_base * (1 + 0.5 * (f_ratio - 1)²)
            quadratic_factor = 1.0 + 0.5 * ((freq_ratio - 1.0) ** 2)
            voltage = base_voltage * quadratic_factor
        
        # Apply silicon lottery multiplier
        voltage = voltage * silicon_multiplier
        
        return voltage
    
    def apply_undervolting(
        self,
        voltage: float,
        silicon_quality: float,
        frequency: float
    ) -> Tuple[float, bool]:
        """
        Apply undervolting optimization based on silicon lottery.
        
        Better silicon allows more aggressive undervolting (7-10%).
        The undervolting percentage depends on silicon quality.
        
        Args:
            voltage: Base voltage in Volts
            silicon_quality: Silicon quality multiplier (0.92 - 1.08)
            frequency: Frequency in MHz
            
        Returns:
            Tuple of (undervolted_voltage, is_stable)
        """
        if not self.device:
            raise ValueError("No device loaded")
        
        # Calculate undervolting percentage based on silicon quality
        # silicon_quality > 1.0 = better silicon = more undervolting possible
        # silicon_quality < 1.0 = worse silicon = less undervolting possible
        
        # Base undervolting range: 7-10%
        # Map silicon_quality (0.92 - 1.08) to undervolting (7% - 10%)
        quality_normalized = (silicon_quality - 0.92) / (1.08 - 0.92)  # 0.0 to 1.0
        undervolt_percentage = 0.07 + 0.03 * quality_normalized  # 7% to 10%
        
        # Apply undervolting
        undervolted_voltage = voltage * (1.0 - undervolt_percentage)
        
        # Check if undervolting is stable
        # Higher frequency with aggressive undervolting may be unstable
        max_freq = self.device.base_specification.hardware_limits.max_frequency
        freq_ratio = frequency / max_freq
        
        # Stability decreases with higher frequency and more aggressive undervolting
        stability_threshold = 0.95 - 0.1 * freq_ratio
        is_stable = undervolt_percentage < stability_threshold
        
        return undervolted_voltage, is_stable
    
    def add_voltage_noise(
        self,
        voltage: float,
        frequency: float,
        seed: Optional[int] = None
    ) -> float:
        """
        Add noise to voltage to simulate system instability.
        
        Noise characteristics:
        - Small magnitude (2%)
        - Uneven distribution (not uniform)
        - Occasional spikes
        
        Args:
            voltage: Base voltage in Volts
            frequency: Frequency in MHz
            seed: Optional random seed for reproducibility
            
        Returns:
            Voltage with noise in Volts
        """
        rng = np.random.RandomState(seed)
        
        # Base noise: small random variations
        base_noise = rng.normal(0, self._noise_magnitude * voltage)
        
        # Occasional spikes (30% probability)
        if rng.random() < self._noise_frequency:
            # Spike magnitude: 1-3% of voltage
            spike_magnitude = rng.uniform(0.01, 0.03) * voltage
            spike_direction = 1 if rng.random() > 0.5 else -1
            spike = spike_magnitude * spike_direction
        else:
            spike = 0.0
        
        # Frequency-dependent noise (higher frequency = more instability)
        freq_factor = frequency / 600.0  # normalized to nominal frequency
        freq_noise = rng.normal(0, 0.005 * voltage * freq_factor)
        
        # Combine all noise sources
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
        """
        Generate the Frequency/Voltage curve for the device.
        
        Args:
            ambient_level: Ambient temperature level
            add_noise: Whether to add noise to the curve
            apply_undervolting_opt: Whether to apply undervolting optimization
            num_points: Number of points to generate
            seed: Optional random seed for reproducibility
            
        Returns:
            List of FrequencyVoltagePoint objects
        """
        if not self.device:
            raise ValueError("No device loaded")
        
        hidden = self.device.hidden_parameters
        limits = self.device.base_specification.hardware_limits
        
        # Generate frequency points
        frequencies = np.linspace(limits.min_frequency, limits.max_frequency, num_points)
        curve = []
        
        for i, freq in enumerate(frequencies):
            point_seed = seed + i if seed is not None else None
            
            # Calculate base voltage
            voltage = self.calculate_base_voltage(freq, hidden.silicon_quality)
            
            # Apply undervolting if requested
            is_stable = True
            if apply_undervolting_opt:
                voltage, is_stable = self.apply_undervolting(voltage, hidden.silicon_quality, freq)
            
            # Add noise if requested
            if add_noise:
                voltage = self.add_voltage_noise(voltage, freq, point_seed)
            
            # Clamp to hardware limits
            voltage = np.clip(voltage, limits.min_voltage, limits.max_voltage)
            
            curve.append(FrequencyVoltagePoint(
                frequency=freq,
                voltage=voltage,
                is_stable=is_stable
            ))
        
        return curve
    
    def plot_frequency_voltage_curve(
        self,
        curve: List[FrequencyVoltagePoint],
        save_path: str,
        title: str = "Frequency/Voltage Curve"
    ) -> None:
        """
        Plot and save the Frequency/Voltage curve.
        
        Args:
            curve: List of FrequencyVoltagePoint objects
            save_path: Path to save the plot
            title: Plot title
        """
        frequencies = [point.frequency for point in curve]
        voltages = [point.voltage for point in curve]
        
        plt.figure(figsize=(10, 6))
        plt.plot(frequencies, voltages, 'b-', linewidth=2, label='F/V Curve')
        
        # Mark unstable points
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
        
        # Add device info
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
        """
        Simulate device behavior with given parameters.
        
        Args:
            ambient_level: Ambient temperature level (1-5)
            params: Optimization parameters (frequency, voltage, fan_speed)
            
        Returns:
            SimulationOutcome object with results
        """
        if not self.device:
            raise ValueError("No device loaded")
        
        hidden = self.device.hidden_parameters
        spec = self.device.base_specification
        limits = spec.hardware_limits
        
        # Get ambient temperature
        ambient_temp = self.get_ambient_temperature(ambient_level)
        
        # Validate hardware limits
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
        
        # Calculate temperature
        temperature = self._calculate_temperature(params, ambient_temp, hidden, limits, spec)
        
        # Check for critical temperature (non-fatal warning)
        warning = None
        if temperature > limits.max_safe_temperature:
            warning = (f"CRITICAL TEMPERATURE WARNING: {temperature:.1f}°C exceeds "
                      f"safe limit of {limits.max_safe_temperature}°C. "
                      f"Simulation continues but results may be unreliable.")
            # Don't return invalid - continue with warning as requested
        
        # Calculate power consumption
        power = self._calculate_power(params, temperature, hidden, spec)
        
        # Calculate hashrate
        hashrate = self._calculate_hashrate(params, temperature, hidden, spec)
        
        # Calculate efficiency
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
        Calculate chip temperature based on parameters and device characteristics.

        Temperature Model:
        T = T_ambient + (P × R_thermal) / (1 + 0.5 × fan_speed/100)
        """
        # Get nominal frequency from device specification
        nominal_freq = getattr(spec, 'manufacturer_frequency', 600.0)

        # Calculate nominal current from device specification
        nominal_current = spec.nominal_power / spec.optimal_voltage

        # Scale current with voltage and frequency
        current = nominal_current * (params.voltage / spec.optimal_voltage)
        freq_ratio = params.frequency / nominal_freq

        # Base power: P = V × I × (f / f_nominal)
        # Power scales linearly with frequency, not by multiplying by MHz
        base_power = params.voltage * current * freq_ratio

        # Apply silicon quality and degradation
        effective_power = base_power * hidden.silicon_quality * (1 + hidden.degradation)

        # Calculate temperature rise using device-specific thermal resistance
        temp_rise = effective_power * hidden.thermal_resistance
        print(f'Base temp rise: {temp_rise:.2f}°C')

        # Apply cooling (fan speed)
        cooling_efficiency = params.fan_speed / 100.0
        temp_rise /= (1 + (0.97 * cooling_efficiency))
        print(f'Temp rise after cooling: {temp_rise:.2f}°C (Cooling efficiency: {cooling_efficiency:.2f})')

        # Final temperature
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
        Calculate power consumption.

        Power Model:
        P = V × I × (f / f_nominal)
        """
        # Get nominal frequency from device specification
        nominal_freq = getattr(spec, 'manufacturer_frequency', 600.0)

        # Base power (using nominal current scaled by voltage)
        nominal_current = spec.nominal_power / spec.optimal_voltage
        current = nominal_current * (params.voltage / spec.optimal_voltage)
        freq_ratio = params.frequency / nominal_freq

        # Base power: P = V × I × (f / f_nominal)
        # Power scales linearly with frequency ratio, not by multiplying by MHz
        base_power = params.voltage * current * freq_ratio

        # Temperature effect (power increases with temperature)
        # temp_factor = 1 + 0.001 * (temperature - 25)

        # Apply device-specific factors
        power = base_power

        return power
    
    def _calculate_hashrate(
        self,
        params: OptimizationParameters,
        temperature: float,
        hidden: HiddenParameters,
        spec: ASICModelSpecification
    ) -> float:
        """
        Calculate hashrate.
        
        Hashrate Model:
        HR = f × HR_per_mhz × silicon_quality × (1 - degradation) × 
             frequency_response × temp_penalty × voltage_efficiency
        """
        # Base hashrate from frequency
        base_hashrate = params.frequency * spec.hashrate_per_mhz
        
        # Apply silicon quality
        hashrate = base_hashrate * hidden.silicon_quality
        
        # Apply degradation
        hashrate *= (1 - hidden.degradation)
        
        # Apply frequency response
        hashrate *= hidden.frequency_response
        
        # Temperature penalty (efficiency drops at high temperature)
        temp_penalty = 1.0
        if temperature > 70:
            temp_penalty = 1.0 - 0.01 * (temperature - 70)
        hashrate *= max(0.5, temp_penalty)
        
        # Voltage efficiency
        voltage_efficiency = params.voltage / spec.optimal_voltage
        voltage_efficiency = min(1.0, voltage_efficiency)
        hashrate *= voltage_efficiency
        
        return hashrate
    
    def run_parameter_sweep(
        self,
        ambient_level: AmbientTemperatureLevel,
        frequency_range: Tuple[float, float],
        voltage_range: Tuple[float, float],
        fan_speed: float = 100.0,
        num_points: int = 20
    ) -> List[Tuple[OptimizationParameters, SimulationOutcome]]:
        """
        Run a parameter sweep across frequency and voltage ranges.
        
        Args:
            ambient_level: Ambient temperature level
            frequency_range: (min_freq, max_freq) in MHz
            voltage_range: (min_voltage, max_voltage) in Volts
            fan_speed: Fan speed percentage
            num_points: Number of points per dimension
            
        Returns:
            List of (parameters, outcome) tuples
        """
        results = []
        
        frequencies = np.linspace(frequency_range[0], frequency_range[1], num_points)
        voltages = np.linspace(voltage_range[0], voltage_range[1], num_points)
        
        for freq in frequencies:
            for volt in voltages:
                params = OptimizationParameters(
                    frequency=freq,
                    voltage=volt,
                    fan_speed=fan_speed
                )
                
                outcome = self.simulate(ambient_level, params)
                results.append((params, outcome))
        
        return results
    
    def find_optimal_parameters(
        self,
        ambient_level: AmbientTemperatureLevel,
        target: str = 'efficiency',
        frequency_range: Optional[Tuple[float, float]] = None,
        voltage_range: Optional[Tuple[float, float]] = None
    ) -> Tuple[OptimizationParameters, SimulationOutcome]:
        """
        Find optimal parameters for a given target.
        
        Args:
            ambient_level: Ambient temperature level
            target: Optimization target ('efficiency', 'hashrate', 'balanced')
            frequency_range: Optional frequency range to search
            voltage_range: Optional voltage range to search
            
        Returns:
            Tuple of (optimal_parameters, optimal_outcome)
        """
        if not self.device:
            raise ValueError("No device loaded")
        
        limits = self.device.base_specification.hardware_limits
        
        # Use hardware limits if ranges not provided
        if frequency_range is None:
            frequency_range = (limits.min_frequency, limits.max_frequency)
        if voltage_range is None:
            voltage_range = (limits.min_voltage, limits.max_voltage)
        
        # Run parameter sweep
        results = self.run_parameter_sweep(
            ambient_level=ambient_level,
            frequency_range=frequency_range,
            voltage_range=voltage_range,
            num_points=30
        )
        
        # Filter valid results
        valid_results = [(p, o) for p, o in results if o.valid]
        
        if not valid_results:
            # Return first result if all invalid
            return results[0]
        
        # Find optimal based on target
        if target == 'efficiency':
            optimal = min(valid_results, key=lambda x: x[1].efficiency)
        elif target == 'hashrate':
            optimal = max(valid_results, key=lambda x: x[1].hashrate)
        elif target == 'balanced':
            # Balance efficiency and hashrate
            optimal = max(valid_results, key=lambda x: x[1].hashrate / x[1].efficiency)
        else:
            raise ValueError(f"Unknown target: {target}")
        
        return optimal
