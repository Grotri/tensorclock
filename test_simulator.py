"""
Test and Demo Script for ASIC Physics Simulator

This script demonstrates all features of the ASIC Physics Simulator including:
- Loading virtual devices
- Generating Frequency/Voltage curves with noise
- Running simulations with different ambient temperatures
- Critical temperature warnings
- Undervolting optimization
- Graph generation

Author: Senior Python Developer
Date: 2026-03-11
Version: 1.0
"""

import sys
from pathlib import Path
import numpy as np

# Import simulator and device generator
from asic_physics_simulator import (
    ASICPhysicsSimulator,
    AmbientTemperatureLevel,
    OptimizationParameters,
    SimulationOutcome
)
from virtual_device_generator import VirtualDeviceGenerator, HiddenParameters


def print_section(title: str):
    """Print a formatted section header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_device_info(device):
    """Print device information."""
    print(f"\nDevice ID: {device.device_id}")
    print(f"ASIC Model: {device.asic_model}")
    print(f"Electricity Price: ${device.electricity_price}/kWh")
    print(f"\nHidden Parameters:")
    print(f"  Silicon Quality: {device.hidden_parameters.silicon_quality:.4f}")
    print(f"  Degradation: {device.hidden_parameters.degradation:.4f}")
    print(f"  Thermal Resistance: {device.hidden_parameters.thermal_resistance:.4f} °C/W")
    print(f"  Voltage Tolerance: {device.hidden_parameters.voltage_tolerance:.4f}")
    print(f"  Frequency Response: {device.hidden_parameters.frequency_response:.4f}")
    print(f"\nBase Specification:")
    print(f"  Nominal Hashrate: {device.base_specification.nominal_hashrate} TH/s")
    print(f"  Nominal Power: {device.base_specification.nominal_power} W")
    print(f"  Optimal Voltage: {device.base_specification.optimal_voltage} V")
    print(f"  Hashrate per MHz: {device.base_specification.hashrate_per_mhz} TH/(s·MHz)")


def test_frequency_voltage_curves(simulator: ASICPhysicsSimulator):
    """Test Frequency/Voltage curve generation with and without noise."""
    print_section("Frequency/Voltage Curve Generation")
    
    # Generate curve without noise
    print("\nGenerating F/V curve WITHOUT noise...")
    curve_clean = simulator.generate_frequency_voltage_curve(
        ambient_level=AmbientTemperatureLevel.LEVEL_3,
        add_noise=False,
        apply_undervolting_opt=True,
        num_points=200,
        seed=42
    )
    
    # Save clean curve plot
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    clean_plot_path = output_dir / "fv_curve_clean.png"
    simulator.plot_frequency_voltage_curve(
        curve_clean,
        str(clean_plot_path),
        title="Frequency/Voltage Curve (Clean - No Noise)"
    )
    print(f"  Saved clean curve to: {clean_plot_path}")
    
    # Print some sample points
    print("\n  Sample points from clean curve:")
    for i in [0, 10, 25, 40, 49]:
        point = curve_clean[i]
        print(f"    Freq: {point.frequency:6.1f} MHz | Voltage: {point.voltage:6.3f} V | Stable: {point.is_stable}")
    
    # Generate curve with noise
    print("\nGenerating F/V curve WITH noise...")
    curve_noisy = simulator.generate_frequency_voltage_curve(
        ambient_level=AmbientTemperatureLevel.LEVEL_3,
        add_noise=True,
        apply_undervolting_opt=True,
        num_points=200,
        seed=42
    )
    
    # Save noisy curve plot
    noisy_plot_path = output_dir / "fv_curve_noisy.png"
    simulator.plot_frequency_voltage_curve(
        curve_noisy,
        str(noisy_plot_path),
        title="Frequency/Voltage Curve (With Noise)"
    )
    print(f"  Saved noisy curve to: {noisy_plot_path}")
    
    # Print some sample points
    print("\n  Sample points from noisy curve:")
    for i in [0, 10, 25, 40, 49]:
        point = curve_noisy[i]
        print(f"    Freq: {point.frequency:6.1f} MHz | Voltage: {point.voltage:6.3f} V | Stable: {point.is_stable}")
    
    # Compare curves
    print("\n  Comparison (Clean vs Noisy):")
    for i in [0, 10, 25, 40, 49]:
        clean_v = curve_clean[i].voltage
        noisy_v = curve_noisy[i].voltage
        diff = noisy_v - clean_v
        print(f"    Freq {curve_clean[i].frequency:6.1f} MHz: {clean_v:6.3f} V -> {noisy_v:6.3f} V (diff: {diff:+6.3f} V)")


def test_ambient_temperature_levels(simulator: ASICPhysicsSimulator):
    """Test simulation with different ambient temperature levels."""
    print_section("Ambient Temperature Levels Test")
    
    # Test parameters
    params = OptimizationParameters(
        frequency=600.0,
        voltage=13.2,
        fan_speed=100.0
    )
    
    print(f"\nTesting with parameters: Freq={params.frequency} MHz, Voltage={params.voltage} V, Fan={params.fan_speed}%")
    print("\nResults across different ambient temperature levels:")
    print("-" * 80)
    print(f"{'Level':<8} {'Ambient (°C)':<15} {'Temperature (°C)':<18} {'Power (W)':<12} {'Hashrate (TH/s)':<18} {'Efficiency (J/TH)':<18}")
    print("-" * 80)
    
    for level in AmbientTemperatureLevel:
        outcome = simulator.simulate(level, params)
        ambient = simulator.get_ambient_temperature(level)
        
        status = "[OK]" if outcome.valid else "[!]"
        print(f"{status} {level.name:<8} {ambient:<15.1f} {outcome.temperature:<18.1f} {outcome.power:<12.1f} {outcome.hashrate:<18.2f} {outcome.efficiency:<18.2f}")
        
        if outcome.warning:
            print(f"  WARNING: {outcome.warning}")
    
    print("-" * 80)


def test_critical_temperature_warning(simulator: ASICPhysicsSimulator):
    """Test critical temperature warning system."""
    print_section("Critical Temperature Warning Test")
    
    print("\nTesting parameters that may trigger critical temperature warnings...")
    print("Note: Simulator continues with warning (non-fatal) as requested.\n")
    
    # Test with high frequency and low fan speed
    test_cases = [
        ("High Freq, Low Fan", 650.0, 14.0, 30.0),
        ("Max Freq, Min Fan", 650.0, 14.5, 0.0),
        ("High Freq, Normal Fan", 640.0, 13.8, 100.0),
        ("Normal Freq, Low Fan", 600.0, 13.2, 20.0),
    ]
    
    print("-" * 100)
    print(f"{'Test Case':<25} {'Freq (MHz)':<12} {'Voltage (V)':<12} {'Fan (%)':<10} {'Temp (°C)':<12} {'Valid':<8} {'Warning'}")
    print("-" * 100)
    
    for name, freq, volt, fan in test_cases:
        params = OptimizationParameters(frequency=freq, voltage=volt, fan_speed=fan)
        outcome = simulator.simulate(AmbientTemperatureLevel.LEVEL_5, params)  # Hot environment
        
        warning_short = "Yes" if outcome.warning else "No"
        print(f"{name:<25} {freq:<12.1f} {volt:<12.2f} {fan:<10.1f} {outcome.temperature:<12.1f} {str(outcome.valid):<8} {warning_short}")
        
        if outcome.warning:
            print(f"  -> {outcome.warning[:80]}...")
    
    print("-" * 100)


def test_undervolting_optimization(simulator: ASICPhysicsSimulator):
    """Test undervolting optimization based on silicon lottery."""
    print_section("Undervolting Optimization Test")
    
    device = simulator.device
    silicon_quality = device.hidden_parameters.silicon_quality
    
    print(f"\nDevice Silicon Quality: {silicon_quality:.4f}")
    
    # Calculate undervolting percentage
    quality_normalized = (silicon_quality - 0.92) / (1.08 - 0.92)
    undervolt_percentage = 0.07 + 0.03 * quality_normalized
    
    print(f"Undervolting Optimization: {undervolt_percentage*100:.2f}%")
    
    # Test at different frequencies
    print("\nTesting undervolting at different frequencies:")
    print("-" * 80)
    print(f"{'Frequency (MHz)':<18} {'Base Voltage (V)':<20} {'Undervolted (V)':<20} {'Stable':<10}")
    print("-" * 80)
    
    for freq in [500, 550, 600, 625, 650]:
        base_voltage = simulator.calculate_base_voltage(freq, silicon_quality)
        undervolted_voltage, is_stable = simulator.apply_undervolting(base_voltage, silicon_quality, freq)
        
        stable_str = "Yes" if is_stable else "No"
        print(f"{freq:<18.1f} {base_voltage:<20.3f} {undervolted_voltage:<20.3f} {stable_str:<10}")
    
    print("-" * 80)
    
    # Generate curves with and without undervolting
    print("\nGenerating comparison curves (with/without undervolting)...")
    
    curve_with_uv = simulator.generate_frequency_voltage_curve(
        ambient_level=AmbientTemperatureLevel.LEVEL_3,
        add_noise=False,
        apply_undervolting_opt=True,
        num_points=200
    )
    
    curve_without_uv = simulator.generate_frequency_voltage_curve(
        ambient_level=AmbientTemperatureLevel.LEVEL_3,
        add_noise=False,
        apply_undervolting_opt=False,
        num_points=200
    )
    
    # Save comparison plot
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    
    import matplotlib.pyplot as plt
    
    plt.figure(figsize=(12, 6))
    
    freqs_with = [p.frequency for p in curve_with_uv]
    volts_with = [p.voltage for p in curve_with_uv]
    freqs_without = [p.frequency for p in curve_without_uv]
    volts_without = [p.voltage for p in curve_without_uv]
    
    plt.plot(freqs_with, volts_with, 'b-', linewidth=2, label='With Undervolting')
    plt.plot(freqs_without, volts_without, 'r--', linewidth=2, label='Without Undervolting')
    
    plt.xlabel('Frequency (MHz)', fontsize=12)
    plt.ylabel('Voltage (V)', fontsize=12)
    plt.title(f'Undervolting Optimization ({undervolt_percentage*100:.1f}% reduction)', 
              fontsize=14, fontweight='bold')
    plt.grid(True, alpha=0.3)
    plt.legend(fontsize=12)
    
    uv_plot_path = output_dir / "undervolting_comparison.png"
    plt.savefig(uv_plot_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"  Saved comparison plot to: {uv_plot_path}")


def test_parameter_sweep(simulator: ASICPhysicsSimulator):
    """Test parameter sweep to find optimal settings."""
    print_section("Parameter Sweep and Optimization")
    
    # Run parameter sweep
    print("\nRunning parameter sweep across frequency and voltage ranges...")
    print("This may take a moment...\n")
    
    results = simulator.run_parameter_sweep(
        ambient_level=AmbientTemperatureLevel.LEVEL_3,
        frequency_range=(550, 650),
        voltage_range=(12.0, 14.0),
        fan_speed=100.0,
        num_points=15
    )
    
    # Find best results for different targets
    valid_results = [(p, o) for p, o in results if o.valid]
    
    if valid_results:
        # Best efficiency
        best_eff = min(valid_results, key=lambda x: x[1].efficiency)
        # Best hashrate
        best_hr = max(valid_results, key=lambda x: x[1].hashrate)
        # Best balanced
        best_bal = max(valid_results, key=lambda x: x[1].hashrate / x[1].efficiency)
        
        print("Optimal Parameters Found:")
        print("-" * 100)
        print(f"{'Target':<15} {'Freq (MHz)':<12} {'Voltage (V)':<12} {'Power (W)':<12} {'Hashrate (TH/s)':<18} {'Efficiency (J/TH)':<18}")
        print("-" * 100)
        
        for name, (params, outcome) in [("Efficiency", best_eff), ("Hashrate", best_hr), ("Balanced", best_bal)]:
            print(f"{name:<15} {params.frequency:<12.1f} {params.voltage:<12.2f} {outcome.power:<12.1f} {outcome.hashrate:<18.2f} {outcome.efficiency:<18.2f}")
        
        print("-" * 100)
    else:
        print("No valid results found in parameter sweep.")


def test_noise_characteristics(simulator: ASICPhysicsSimulator):
    """Test and visualize noise characteristics."""
    print_section("Noise Characteristics Analysis")
    
    print("\nAnalyzing noise impact on F/V curve...")
    
    # Generate multiple noisy curves with same seed to show noise pattern
    curves = []
    for i in range(5):
        curve = simulator.generate_frequency_voltage_curve(
            ambient_level=AmbientTemperatureLevel.LEVEL_3,
            add_noise=True,
            apply_undervolting_opt=True,
            num_points=50,
            seed=42 + i
        )
        curves.append(curve)
    
    # Calculate statistics
    freqs = [curves[0][i].frequency for i in range(len(curves[0]))]
    voltages_by_freq = []
    
    for i in range(len(curves[0])):
        voltages = [curve[i].voltage for curve in curves]
        voltages_by_freq.append(voltages)
    
    means = [np.mean(v) for v in voltages_by_freq]
    stds = [np.std(v) for v in voltages_by_freq]
    
    print(f"\nNoise Statistics (across 5 runs):")
    print("-" * 70)
    print(f"{'Frequency (MHz)':<18} {'Mean Voltage (V)':<20} {'Std Dev (V)':<20} {'CV (%)':<15}")
    print("-" * 70)
    
    for i in [0, 10, 25, 40, 49]:
        cv = (stds[i] / means[i]) * 100 if means[i] > 0 else 0
        print(f"{freqs[i]:<18.1f} {means[i]:<20.3f} {stds[i]:<20.4f} {cv:<15.2f}")
    
    print("-" * 70)
    
    # Plot noise visualization
    import matplotlib.pyplot as plt
    
    plt.figure(figsize=(12, 6))
    
    # Plot all curves
    for i, curve in enumerate(curves):
        freqs = [p.frequency for p in curve]
        volts = [p.voltage for p in curve]
        plt.plot(freqs, volts, alpha=0.3, linewidth=1, label=f'Run {i+1}')
    
    # Plot mean
    plt.plot(freqs, means, 'k-', linewidth=3, label='Mean')
    
    # Plot confidence interval
    plt.fill_between(freqs, 
                     [m - s for m, s in zip(means, stds)],
                     [m + s for m, s in zip(means, stds)],
                     alpha=0.2, color='gray', label='±1 Std Dev')
    
    plt.xlabel('Frequency (MHz)', fontsize=12)
    plt.ylabel('Voltage (V)', fontsize=12)
    plt.title('Noise Characteristics in F/V Curve', fontsize=14, fontweight='bold')
    plt.grid(True, alpha=0.3)
    plt.legend(fontsize=10)
    
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    noise_plot_path = output_dir / "noise_characteristics.png"
    plt.savefig(noise_plot_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"\n  Saved noise visualization to: {noise_plot_path}")


def main():
    """Main test function."""
    print("\n" + "=" * 70)
    print("  ASIC Physics Simulator - Test and Demo")
    print("=" * 70)
    
    # Initialize simulator
    simulator = ASICPhysicsSimulator()
    
    # Load a device
    print_section("Loading Virtual Device")

    # Try to load an existing device (use JSON format to avoid pickle serialization issues)
    device_path = Path("virtual_devices/antminer_s19_pro_1ba2f4b9.json")

    try:
        # Extract device_id from filename (remove extension and directory)
        device_id = device_path.stem
        simulator.load_device(device_id=device_id, device_path=str(device_path))
        print(f"\n[OK] Successfully loaded device from: {device_path}")
    except FileNotFoundError:
        print(f"\n[X] Device not found at: {device_path}")
        print("Creating a new device...")

        # Create a new device
        generator = VirtualDeviceGenerator()
        generator.load_specifications_from_file("virtual_devices/asic_specifications.json")

        # Generate device with specific hidden parameters
        hidden_params = {
            'silicon_quality': 1.03,
            'degradation': 0.05,
            'thermal_resistance': 0.15,
            'voltage_tolerance': 1.0,
            'frequency_response': 1.0
        }

        device = generator.generate_device(
            model_name="Antminer S19 Pro",
            hidden_params=hidden_params,
            electricity_price=0.05
        )

        simulator.load_device_from_object(device)
        print(f"[OK] Created and loaded new device: {device.device_id}")
    
    # Print device info
    print_device_info(simulator.device)
    
    # Run all tests
    test_frequency_voltage_curves(simulator)
    test_ambient_temperature_levels(simulator)
    test_critical_temperature_warning(simulator)
    test_undervolting_optimization(simulator)
    test_noise_characteristics(simulator)
    test_parameter_sweep(simulator)
    
    # Final summary
    print_section("Test Summary")
    print("\n[OK] All tests completed successfully!")
    print("\nGenerated files in 'output/' directory:")
    print("  - fv_curve_clean.png: F/V curve without noise")
    print("  - fv_curve_noisy.png: F/V curve with noise")
    print("  - undervolting_comparison.png: Comparison with/without undervolting")
    print("  - noise_characteristics.png: Noise analysis visualization")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
