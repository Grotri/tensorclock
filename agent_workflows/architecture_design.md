# TensorClock - Comprehensive Technical Architecture Design

**Version:** 2.0  
**Date:** 2026-03-10  
**Author:** Senior Systems Architect  
**Status:** Final Design Document

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Architecture Overview](#system-architecture-overview)
3. [Production-Ready Directory Structure](#production-ready-directory-structure)
4. [MVP Development Roadmap](#mvp-development-roadmap)
5. [Technical Specifications](#technical-specifications)
6. [Testing Strategy](#testing-strategy)
7. [Deployment Strategy](#deployment-strategy)
8. [Success Metrics](#success-metrics)

---

## Executive Summary

TensorClock is a Bittensor subnet that creates a decentralized intelligence layer for ASIC mining optimization. This architecture document provides a comprehensive technical design integrating the proposal requirements with the existing subnet boilerplate, defining component interactions, directory structure, and a phased development roadmap.

**Key Design Principles:**
- **Virtual Device Generation**: Validators create unique virtual devices with hidden parameters, ensuring each device has distinct characteristics
- **Query-Based Exploration**: Miners explore device behavior through limited queries to the validator's physics simulator
- **Model-Based Optimization**: Miners build predictive models of devices from observations and use them to find optimal parameters
- **Deterministic Verification**: All miner submissions verified through deterministic simulation
- **Anti-Gaming Protection**: Multiple layers of validation prevent exploitation
- **Query Budget Management**: Miners work within query budget constraints, requiring efficient exploration strategies
- **Scalable Architecture**: Horizontal scaling of validators and miners
- **Production-Ready**: Enterprise-grade security, monitoring, and observability
- **Modular Design**: Clear separation of concerns with well-defined interfaces

---

## System Architecture Overview

### High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         BITTENSOR NETWORK LAYER                             │
│                    (Subtensor Blockchain & Consensus)                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┴─────────────────┐
                    │                                   │
        ┌───────────▼───────────┐           ┌───────────▼───────────┐
        │      VALIDATOR        │           │        MINER          │
        │                       │           │                       │
        │  ┌─────────────────┐  │           │  ┌─────────────────┐  │
        │  │ Virtual Device  │  │           │  │    Explorer     │  │
        │  │    Generator    │  │           │  │                 │  │
        │  └────────┬────────┘  │           │  └────────┬────────┘  │
        │           │           │           │           │           │
        │  ┌────────▼────────┐  │           │  ┌────────▼────────┐  │
        │  │ ASIC Physics    │  │           │  │  Device Model   │  │
        │  │   Simulator     │  │           │  │    Builder      │  │
        │  └────────┬────────┘  │           │  └────────┬────────┘  │
        │           │           │           │           │           │
        │  ┌────────▼────────┐  │           │  ┌────────▼────────┐  │
        │  │  Task Manager   │  │           │  │   Optimizer     │  │
        │  └────────┬────────┘  │           │  └─────────────────┘  │
        │           │           │           └───────────────────────┘
        │  ┌────────▼────────┐  │
        │  │     Scorer      │  │
        │  └─────────────────┘  │
        └───────────┬───────────┘
                    │
        ┌───────────▼───────────┐
        │    CORE SERVICES      │
        │                       │
        │  • Device Registry    │
        │  • Query Budget DB    │
        │  • Model Registry     │
        │  • Metrics Collector  │
        │  • Audit Logger       │
        └───────────┬───────────┘
                    │
        ┌───────────▼───────────┐
        │  EXTERNAL INTERFACES  │
        │                       │
        │  • REST API           │
        │  • Python SDK         │
        │  • Dashboard          │
        └───────────────────────┘
```

### Component Interaction Flow

```
Task Generation Flow (Validator):
1. Virtual Device Generator creates unique virtual device with hidden parameters
2. Task Manager generates task for miners (device_id, query_budget, optimization_target)
3. Task published to miners via Bittensor protocol

Miner Exploration Flow:
1. Miner receives task from validator (device_id, query_budget, target)
2. Explorer selects initial parameters to query
3. Miner queries validator with (device_id, params)
4. ASIC Physics Simulator returns outcome using device's hidden parameters
5. Device Model Builder updates model with new observation
6. Optimizer uses model to predict optimal parameters
7. Steps 2-6 repeated until query budget exhausted or optimal found
8. Miner submits best parameters to validator

Validator Scoring Flow:
1. Validator receives miner submission (device_id, optimized_params)
2. ASIC Physics Simulator verifies final outcome
3. Scorer calculates score based on optimization quality
4. Query budget efficiency considered in scoring
5. Validator scores miner and updates rankings

Data Persistence Flow:
1. All virtual devices stored in Device Registry
2. All queries logged to Query Budget DB
3. Miner models tracked in Model Registry
4. All submissions logged to Audit Logger
5. Metrics collected by Metrics Collector
```

### Detailed Component Specifications

#### 1. Virtual Device Generator

**Purpose**: Create unique virtual ASIC devices with hidden parameters that represent individual device characteristics

**Key Features**:
- **Unique Device Creation**: Each virtual device has unique hidden parameters representing:
  - Silicon quality variance (5-8% efficiency differences)
  - Chip degradation level (0-20% efficiency loss)
  - Thermal resistance variations
  - Voltage tolerance ranges
  - Frequency response characteristics
  
- **Parameter Generation**:
  - Base parameters from real ASIC model specifications
  - Random perturbations within realistic ranges
  - Correlated parameter generation (e.g., better silicon quality → lower thermal resistance)
  - Deterministic seed-based generation for reproducibility

- **Device Lifecycle**:
  - Device creation with unique device_id
  - Hidden parameters never exposed to miners
  - Device persists for task duration
  - Device archival after task completion

**Technical Implementation**:
```python
class VirtualDeviceGenerator:
    def __init__(self, asic_model: str):
        self.model = self._load_base_specifications(asic_model)
        
    def generate_device(self, seed: Optional[int] = None) -> VirtualDevice:
        """Generate a unique virtual device with hidden parameters."""
        rng = np.random.RandomState(seed)
        
        # Generate silicon quality (0.92 - 1.08, 5-8% variance)
        silicon_quality = rng.normal(1.0, 0.02)
        silicon_quality = np.clip(silicon_quality, 0.92, 1.08)
        
        # Generate degradation level (0.0 - 0.2, 0-20% loss)
        degradation = rng.uniform(0.0, 0.2)
        
        # Generate thermal resistance (correlated with silicon quality)
        base_thermal = self.model.base_thermal_resistance
        thermal_resistance = base_thermal / silicon_quality
        
        # Generate voltage tolerance
        voltage_tolerance = rng.uniform(0.95, 1.05)
        
        # Generate frequency response coefficient
        frequency_response = rng.uniform(0.98, 1.02)
        
        # Create device with hidden parameters
        device = VirtualDevice(
            device_id=self._generate_device_id(),
            asic_model=self.model.name,
            hidden_parameters=HiddenParameters(
                silicon_quality=silicon_quality,
                degradation=degradation,
                thermal_resistance=thermal_resistance,
                voltage_tolerance=voltage_tolerance,
                frequency_response=frequency_response
            )
        )
        
        return device
```

**Hidden Parameters**:
- `silicon_quality`: Multiplier for efficiency (0.92 - 1.08)
- `degradation`: Efficiency loss due to age (0.0 - 0.2)
- `thermal_resistance`: Thermal resistance in °C/W
- `voltage_tolerance`: Voltage operating range multiplier (0.95 - 1.05)
- `frequency_response`: Frequency scaling factor (0.98 - 1.02)

#### 2. ASIC Physics Simulator

**Purpose**: Simulate ASIC mining behavior using device-specific hidden parameters

**Key Features**:
- **Device-Specific Simulation**: Uses hidden parameters to calculate realistic outcomes
- **Physical Constraints**: Enforces hardware limits, thermal constraints, efficiency boundaries
- **Deterministic Output**: Same (device_id, params) always produces same outcome
- **Anti-Gaming**: Prevents unrealistic parameter combinations

**Input/Output**:
```python
# Input
query = QueryRequest(
    device_id="device_abc123",
    parameters=OptimizationParameters(
        frequency=600,      # MHz
        voltage=13.0,       # Volts
        fan_speed=100       # Percentage
    )
)

# Output
outcome = SimulationOutcome(
    temperature=65.5,      # Celsius
    power=3200.0,          # Watts
    hashrate=110.5,        # TH/s
    efficiency=28.96,      # J/Th
    valid=True             # Whether parameters are valid
)
```

**Technical Implementation**:
```python
class ASICPhysicsSimulator:
    def __init__(self, device_registry: DeviceRegistry):
        self.device_registry = device_registry
        
    def simulate(self, device_id: str, params: OptimizationParameters) -> SimulationOutcome:
        """Simulate device behavior with given parameters."""
        # Get device with hidden parameters
        device = self.device_registry.get_device(device_id)
        hidden = device.hidden_parameters
        
        # Validate hardware limits
        self._validate_hardware_limits(params, device)
        
        # Calculate temperature using device-specific thermal resistance
        temperature = self._calculate_temperature(params, hidden)
        
        # Check thermal constraints
        if temperature > device.max_safe_temperature:
            return SimulationOutcome(
                temperature=temperature,
                power=0,
                hashrate=0,
                efficiency=float('inf'),
                valid=False
            )
        
        # Calculate power consumption
        power = self._calculate_power(params, temperature, hidden)
        
        # Calculate hashrate using device-specific parameters
        hashrate = self._calculate_hashrate(params, temperature, hidden)
        
        # Calculate efficiency
        efficiency = power / hashrate if hashrate > 0 else float('inf')
        
        return SimulationOutcome(
            temperature=temperature,
            power=power,
            hashrate=hashrate,
            efficiency=efficiency,
            valid=True
        )
    
    def _calculate_temperature(self, params: OptimizationParameters, 
                               hidden: HiddenParameters) -> float:
        """Calculate chip temperature based on parameters and hidden thermal resistance."""
        # Base power estimation
        base_power = params.voltage * params.current * params.frequency / 1000
        
        # Apply silicon quality and degradation
        effective_power = base_power * hidden.silicon_quality * (1 + hidden.degradation)
        
        # Calculate temperature rise
        temp_rise = effective_power * hidden.thermal_resistance
        
        # Apply cooling (fan speed)
        cooling_efficiency = params.fan_speed / 100.0
        temp_rise /= (1 + 0.5 * cooling_efficiency)
        
        # Final temperature
        temperature = self.ambient_temperature + temp_rise
        
        return temperature
    
    def _calculate_power(self, params: OptimizationParameters, temperature: float,
                         hidden: HiddenParameters) -> float:
        """Calculate power consumption."""
        # Base power
        base_power = params.voltage * params.current * params.frequency / 1000
        
        # Temperature effect (power increases with temperature)
        temp_factor = 1 + 0.001 * (temperature - 25)
        
        # Apply device-specific factors
        power = base_power * temp_factor * hidden.silicon_quality * (1 + hidden.degradation)
        
        return power
    
    def _calculate_hashrate(self, params: OptimizationParameters, temperature: float,
                           hidden: HiddenParameters) -> float:
        """Calculate hashrate."""
        # Base hashrate from frequency
        base_hashrate = params.frequency * self.hashrate_per_mhz
        
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
        voltage_efficiency = params.voltage / self.optimal_voltage
        voltage_efficiency = min(1.0, voltage_efficiency)
        hashrate *= voltage_efficiency
        
        return hashrate
```

**Physical Models**:
- **Temperature Model**: T = T_ambient + (P × R_thermal) / (1 + 0.5 × fan_speed/100)
- **Power Model**: P = V × I × f × silicon_quality × (1 + degradation) × (1 + 0.001 × (T - 25))
- **Hashrate Model**: HR = f × HR_per_mhz × silicon_quality × (1 - degradation) × frequency_response × temp_penalty × voltage_efficiency

#### 3. Task Manager

**Purpose**: Generate tasks for miners and manage query budgets

**Key Features**:
- **Task Generation**: Creates optimization tasks with:
  - Unique device_id
  - Query budget (number of allowed queries)
  - Optimization target (efficiency, hashrate, or balanced)
  - Task expiration time
  
- **Query Budget Management**:
  - Tracks queries per miner per task
  - Enforces budget limits
  - Provides query count to miners
  - Penalizes over-budget submissions
  
- **Task Distribution**:
  - Assigns devices to miners
  - Balances workload across miners
  - Handles task timeouts and reassignments

**Technical Implementation**:
```python
class TaskManager:
    def __init__(self, query_budget_db: QueryBudgetDB):
        self.query_budget_db = query_budget_db
        
    def create_task(self, device: VirtualDevice, query_budget: int = 100,
                    target: OptimizationTarget = OptimizationTarget.EFFICIENCY) -> Task:
        """Create a new optimization task."""
        task = Task(
            task_id=self._generate_task_id(),
            device_id=device.device_id,
            asic_model=device.asic_model,
            query_budget=query_budget,
            target=target,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1)
        )
        
        return task
    
    def consume_query(self, task_id: str, miner_id: str) -> bool:
        """Consume one query from budget. Returns True if budget available."""
        budget = self.query_budget_db.get_budget(task_id, miner_id)
        
        if budget.queries_used >= budget.query_budget:
            return False
        
        budget.queries_used += 1
        self.query_budget_db.update_budget(budget)
        
        return True
    
    def get_remaining_queries(self, task_id: str, miner_id: str) -> int:
        """Get remaining query budget for miner."""
        budget = self.query_budget_db.get_budget(task_id, miner_id)
        return budget.query_budget - budget.queries_used
    
    def calculate_efficiency_score(self, task_id: str, miner_id: str, 
                                   outcome: SimulationOutcome) -> float:
        """Calculate score considering query efficiency."""
        budget = self.query_budget_db.get_budget(task_id, miner_id)
        
        # Base score from outcome
        if task.target == OptimizationTarget.EFFICIENCY:
            base_score = 1.0 / outcome.efficiency
        elif task.target == OptimizationTarget.HASHRATE:
            base_score = outcome.hashrate
        else:  # BALANCED
            base_score = 0.6 * (1.0 / outcome.efficiency) + 0.4 * outcome.hashrate
        
        # Query efficiency bonus (fewer queries = better)
        query_efficiency = budget.query_budget / (budget.queries_used + 1)
        
        # Combined score
        score = base_score * (1 + 0.1 * np.log(query_efficiency))
        
        return score
```

**Task Structure**:
```python
@dataclass
class Task:
    task_id: str
    device_id: str
    asic_model: str
    query_budget: int  # Number of allowed queries
    target: OptimizationTarget  # EFFICIENCY, HASHRATE, or BALANCED
    created_at: datetime
    expires_at: datetime
```

#### 4. Scorer

**Purpose**: Evaluate miner solutions and calculate scores

**Key Features**:
- **Outcome Evaluation**: Scores based on simulation outcome quality
- **Query Efficiency**: Rewards efficient use of query budget
- **Multi-Objective Scoring**: Supports efficiency, hashrate, and balanced targets
- **Fair Ranking**: Normalizes scores across different tasks

**Scoring Formulas**:

1. **Efficiency Target**:
```
Score = (1 / efficiency) × (1 + 0.1 × ln(query_budget / queries_used))
```

2. **Hashrate Target**:
```
Score = hashrate × (1 + 0.1 × ln(query_budget / queries_used))
```

3. **Balanced Target**:
```
Score = (0.6 × (1 / efficiency) + 0.4 × hashrate) × (1 + 0.1 × ln(query_budget / queries_used))
```

**Technical Implementation**:
```python
class Scorer:
    def __init__(self):
        pass
        
    def calculate_score(self, task: Task, miner_id: str, 
                        outcome: SimulationOutcome, queries_used: int) -> float:
        """Calculate score for miner submission."""
        # Validate outcome
        if not outcome.valid:
            return 0.0
        
        # Calculate base score based on target
        if task.target == OptimizationTarget.EFFICIENCY:
            base_score = self._efficiency_score(outcome)
        elif task.target == OptimizationTarget.HASHRATE:
            base_score = self._hashrate_score(outcome)
        else:  # BALANCED
            base_score = self._balanced_score(outcome)
        
        # Calculate query efficiency bonus
        query_efficiency = task.query_budget / max(1, queries_used)
        efficiency_bonus = 1 + 0.1 * np.log(query_efficiency)
        
        # Combined score
        score = base_score * efficiency_bonus
        
        return max(0.0, score)
    
    def _efficiency_score(self, outcome: SimulationOutcome) -> float:
        """Calculate efficiency-based score."""
        return 1.0 / outcome.efficiency
    
    def _hashrate_score(self, outcome: SimulationOutcome) -> float:
        """Calculate hashrate-based score."""
        return outcome.hashrate
    
    def _balanced_score(self, outcome: SimulationOutcome) -> float:
        """Calculate balanced score."""
        efficiency_score = 1.0 / outcome.efficiency
        hashrate_score = outcome.hashrate
        return 0.6 * efficiency_score + 0.4 * hashrate_score
```

#### 5. Explorer

**Purpose**: Select parameters to query and manage exploration strategy

**Key Features**:
- **Exploration Strategies**:
  - Random exploration: Uniform random sampling
  - Grid exploration: Systematic grid search
  - Adaptive exploration: Focus on promising regions
  - Bayesian exploration: Use model uncertainty
  
- **Query Budget Management**:
  - Tracks remaining queries
  - Adapts strategy based on budget
  - Prioritizes high-value queries
  
- **Initial Parameter Selection**:
  - Uses device model specifications for starting points
  - Generates diverse initial samples
  - Considers query budget constraints

**Technical Implementation**:
```python
class Explorer:
    def __init__(self, strategy: ExplorationStrategy = ExplorationStrategy.ADAPTIVE):
        self.strategy = strategy
        
    def select_parameters(self, model: DeviceModel, remaining_queries: int,
                          task: Task) -> List[OptimizationParameters]:
        """Select parameters to query."""
        if self.strategy == ExplorationStrategy.RANDOM:
            return self._random_exploration(model, remaining_queries, task)
        elif self.strategy == ExplorationStrategy.GRID:
            return self._grid_exploration(model, remaining_queries, task)
        elif self.strategy == ExplorationStrategy.ADAPTIVE:
            return self._adaptive_exploration(model, remaining_queries, task)
        elif self.strategy == ExplorationStrategy.BAYESIAN:
            return self._bayesian_exploration(model, remaining_queries, task)
    
    def _random_exploration(self, model: DeviceModel, remaining_queries: int,
                            task: Task) -> List[OptimizationParameters]:
        """Random parameter exploration."""
        params_list = []
        
        # Get parameter bounds from model
        bounds = model.get_parameter_bounds()
        
        # Generate random samples
        for _ in range(min(remaining_queries, 10)):
            params = OptimizationParameters(
                frequency=np.random.uniform(bounds['frequency'][0], bounds['frequency'][1]),
                voltage=np.random.uniform(bounds['voltage'][0], bounds['voltage'][1]),
                fan_speed=np.random.uniform(bounds['fan_speed'][0], bounds['fan_speed'][1])
            )
            params_list.append(params)
        
        return params_list
    
    def _adaptive_exploration(self, model: DeviceModel, remaining_queries: int,
                              task: Task) -> List[OptimizationParameters]:
        """Adaptive exploration based on model predictions."""
        params_list = []
        
        if len(model.observations) < 10:
            # Early stage: random exploration
            return self._random_exploration(model, remaining_queries, task)
        
        # Use model to find promising regions
        best_params = model.get_best_parameters()
        
        # Explore around best parameters
        for _ in range(min(remaining_queries, 5)):
            params = OptimizationParameters(
                frequency=best_params.frequency + np.random.normal(0, 20),
                voltage=best_params.voltage + np.random.normal(0, 0.5),
                fan_speed=best_params.fan_speed + np.random.normal(0, 10)
            )
            params_list.append(params)
        
        # Add some random exploration
        params_list.extend(self._random_exploration(model, remaining_queries - len(params_list), task))
        
        return params_list
    
    def _bayesian_exploration(self, model: DeviceModel, remaining_queries: int,
                              task: Task) -> List[OptimizationParameters]:
        """Bayesian exploration using model uncertainty."""
        if not hasattr(model, 'predict_with_uncertainty'):
            # Fallback to adaptive if model doesn't support uncertainty
            return self._adaptive_exploration(model, remaining_queries, task)
        
        params_list = []
        
        for _ in range(min(remaining_queries, 5)):
            # Find point with highest expected improvement
            best_params = self._maximize_expected_improvement(model, task)
            params_list.append(best_params)
        
        return params_list
    
    def _maximize_expected_improvement(self, model: DeviceModel, 
                                       task: Task) -> OptimizationParameters:
        """Find parameters maximizing expected improvement."""
        # Use optimization to find best point
        bounds = model.get_parameter_bounds()
        
        def objective(x):
            params = OptimizationParameters(
                frequency=x[0],
                voltage=x[1],
                fan_speed=x[2]
            )
            pred, uncertainty = model.predict_with_uncertainty(params)
            
            if task.target == OptimizationTarget.EFFICIENCY:
                best_so_far = model.get_best_efficiency()
                improvement = (1.0 / pred.efficiency) - best_so_far
            elif task.target == OptimizationTarget.HASHRATE:
                best_so_far = model.get_best_hashrate()
                improvement = pred.hashrate - best_so_far
            else:
                best_so_far = model.get_best_score()
                improvement = (0.6 * (1.0 / pred.efficiency) + 0.4 * pred.hashrate) - best_so_far
            
            # Expected improvement
            z = improvement / (uncertainty + 1e-6)
            ei = improvement * norm.cdf(z) + uncertainty * norm.pdf(z)
            
            return -ei  # Minimize negative EI
        
        result = minimize(
            objective,
            x0=[(bounds['frequency'][0] + bounds['frequency'][1]) / 2,
                 (bounds['voltage'][0] + bounds['voltage'][1]) / 2,
                 (bounds['fan_speed'][0] + bounds['fan_speed'][1]) / 2],
            bounds=[bounds['frequency'], bounds['voltage'], bounds['fan_speed']],
            method='L-BFGS-B'
        )
        
        return OptimizationParameters(
            frequency=result.x[0],
            voltage=result.x[1],
            fan_speed=result.x[2]
        )
```

#### 6. Device Model Builder

**Purpose**: Build predictive models of devices from observations

**Key Features**:
- **Model Types**:
  - Gaussian Process Regression: Predicts outcomes with uncertainty
  - Neural Network: Deep learning model for complex relationships
  - Ensemble: Combines multiple models for robustness
  
- **Learning from Observations**:
  - Updates model with each query result
  - Adapts to device-specific characteristics
  - Improves predictions over time
  
- **Prediction Capabilities**:
  - Predicts temperature, power, hashrate, efficiency
  - Provides uncertainty estimates
  - Identifies promising parameter regions

**Technical Implementation**:
```python
class DeviceModelBuilder:
    def __init__(self, model_type: ModelType = ModelType.GAUSSIAN_PROCESS):
        self.model_type = model_type
        self.model = None
        
    def build_model(self, observations: List[Observation]) -> DeviceModel:
        """Build model from observations."""
        if self.model_type == ModelType.GAUSSIAN_PROCESS:
            return self._build_gaussian_process(observations)
        elif self.model_type == ModelType.NEURAL_NETWORK:
            return self._build_neural_network(observations)
        elif self.model_type == ModelType.ENSEMBLE:
            return self._build_ensemble(observations)
    
    def _build_gaussian_process(self, observations: List[Observation]) -> DeviceModel:
        """Build Gaussian Process model."""
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import RBF, ConstantKernel
        
        # Prepare data
        X = np.array([[obs.parameters.frequency, obs.parameters.voltage, obs.parameters.fan_speed]
                      for obs in observations])
        y_temp = np.array([obs.outcome.temperature for obs in observations])
        y_power = np.array([obs.outcome.power for obs in observations])
        y_hashrate = np.array([obs.outcome.hashrate for obs in observations])
        
        # Build kernels
        kernel = ConstantKernel(1.0) * RBF(length_scale=[100.0, 2.0, 20.0])
        
        # Train models
        gp_temp = GaussianProcessRegressor(kernel=kernel, alpha=0.1)
        gp_temp.fit(X, y_temp)
        
        gp_power = GaussianProcessRegressor(kernel=kernel, alpha=0.1)
        gp_power.fit(X, y_power)
        
        gp_hashrate = GaussianProcessRegressor(kernel=kernel, alpha=0.1)
        gp_hashrate.fit(X, y_hashrate)
        
        return GaussianProcessDeviceModel(
            observations=observations,
            gp_temp=gp_temp,
            gp_power=gp_power,
            gp_hashrate=gp_hashrate
        )
    
    def _build_neural_network(self, observations: List[Observation]) -> DeviceModel:
        """Build Neural Network model."""
        import tensorflow as tf
        from tensorflow import keras
        
        # Prepare data
        X = np.array([[obs.parameters.frequency, obs.parameters.voltage, obs.parameters.fan_speed]
                      for obs in observations])
        y_temp = np.array([obs.outcome.temperature for obs in observations])
        y_power = np.array([obs.outcome.power for obs in observations])
        y_hashrate = np.array([obs.outcome.hashrate for obs in observations])
        
        # Normalize data
        X_mean, X_std = X.mean(axis=0), X.std(axis=0)
        X_norm = (X - X_mean) / X_std
        
        # Build model
        inputs = keras.Input(shape=(3,))
        x = keras.layers.Dense(64, activation='relu')(inputs)
        x = keras.layers.Dense(64, activation='relu')(x)
        x = keras.layers.Dense(32, activation='relu')(x)
        
        # Multiple outputs
        temp_output = keras.layers.Dense(1, name='temperature')(x)
        power_output = keras.layers.Dense(1, name='power')(x)
        hashrate_output = keras.layers.Dense(1, name='hashrate')(x)
        
        model = keras.Model(inputs=inputs, outputs=[temp_output, power_output, hashrate_output])
        model.compile(optimizer='adam', loss='mse')
        
        # Train
        model.fit(X_norm, [y_temp, y_power, y_hashrate], epochs=100, verbose=0)
        
        return NeuralNetworkDeviceModel(
            observations=observations,
            model=model,
            X_mean=X_mean,
            X_std=X_std
        )
    
    def _build_ensemble(self, observations: List[Observation]) -> DeviceModel:
        """Build ensemble model."""
        models = []
        
        # Build multiple models with different strategies
        models.append(self._build_gaussian_process(observations))
        models.append(self._build_neural_network(observations))
        
        return EnsembleDeviceModel(
            observations=observations,
            models=models
        )
```

**Device Model Interface**:
```python
class DeviceModel(ABC):
    @abstractmethod
    def predict(self, params: OptimizationParameters) -> SimulationOutcome:
        """Predict outcome for given parameters."""
        pass
    
    @abstractmethod
    def predict_with_uncertainty(self, params: OptimizationParameters) -> Tuple[SimulationOutcome, float]:
        """Predict outcome with uncertainty estimate."""
        pass
    
    @abstractmethod
    def update(self, observation: Observation):
        """Update model with new observation."""
        pass
    
    @abstractmethod
    def get_best_parameters(self) -> OptimizationParameters:
        """Get best parameters found so far."""
        pass
    
    @abstractmethod
    def get_parameter_bounds(self) -> Dict[str, Tuple[float, float]]:
        """Get parameter bounds."""
        pass
```

#### 7. Optimizer

**Purpose**: Find optimal parameters using the built device model

**Key Features**:
- **Optimization Strategies**:
  - Gradient-based: Uses model gradients for local optimization
  - Bayesian: Uses model uncertainty for exploration-exploitation
  - Genetic: Population-based global optimization
  - Grid search: Systematic exploration
  
- **Model-Based Optimization**:
  - Uses device model predictions instead of queries
  - Efficiently searches parameter space
  - Respects query budget constraints
  
- **Multi-Objective Optimization**:
  - Supports efficiency, hashrate, and balanced targets
  - Pareto frontier exploration
  - Trade-off analysis

**Technical Implementation**:
```python
class Optimizer:
    def __init__(self, strategy: OptimizationStrategy = OptimizationStrategy.GRADIENT):
        self.strategy = strategy
        
    def optimize(self, model: DeviceModel, task: Task,
                 remaining_queries: int) -> OptimizationResult:
        """Find optimal parameters using model."""
        if self.strategy == OptimizationStrategy.GRADIENT:
            return self._gradient_optimization(model, task)
        elif self.strategy == OptimizationStrategy.BAYESIAN:
            return self._bayesian_optimization(model, task)
        elif self.strategy == OptimizationStrategy.GENETIC:
            return self._genetic_optimization(model, task)
        elif self.strategy == OptimizationStrategy.GRID:
            return self._grid_optimization(model, task)
    
    def _gradient_optimization(self, model: DeviceModel, 
                                task: Task) -> OptimizationResult:
        """Gradient-based optimization using model."""
        bounds = model.get_parameter_bounds()
        
        # Define objective based on target
        def objective(x):
            params = OptimizationParameters(
                frequency=x[0],
                voltage=x[1],
                fan_speed=x[2]
            )
            outcome = model.predict(params)
            
            if task.target == OptimizationTarget.EFFICIENCY:
                return outcome.efficiency  # Minimize efficiency
            elif task.target == OptimizationTarget.HASHRATE:
                return -outcome.hashrate  # Maximize hashrate
            else:  # BALANCED
                return 0.6 * outcome.efficiency - 0.4 * outcome.hashrate
        
        # Initial guess from best parameters
        best_params = model.get_best_parameters()
        x0 = [best_params.frequency, best_params.voltage, best_params.fan_speed]
        
        # Optimize
        result = minimize(
            objective,
            x0=x0,
            bounds=[bounds['frequency'], bounds['voltage'], bounds['fan_speed']],
            method='L-BFGS-B'
        )
        
        # Get final outcome
        optimal_params = OptimizationParameters(
            frequency=result.x[0],
            voltage=result.x[1],
            fan_speed=result.x[2]
        )
        outcome = model.predict(optimal_params)
        
        return OptimizationResult(
            parameters=optimal_params,
            predicted_outcome=outcome,
            optimization_success=result.success
        )
    
    def _bayesian_optimization(self, model: DeviceModel, 
                               task: Task) -> OptimizationResult:
        """Bayesian optimization using model uncertainty."""
        if not hasattr(model, 'predict_with_uncertainty'):
            return self._gradient_optimization(model, task)
        
        bounds = model.get_parameter_bounds()
        
        # Define acquisition function
        def acquisition(x):
            params = OptimizationParameters(
                frequency=x[0],
                voltage=x[1],
                fan_speed=x[2]
            )
            pred, uncertainty = model.predict_with_uncertainty(params)
            
            if task.target == OptimizationTarget.EFFICIENCY:
                best_so_far = model.get_best_efficiency()
                improvement = (1.0 / pred.efficiency) - best_so_far
            elif task.target == OptimizationTarget.HASHRATE:
                best_so_far = model.get_best_hashrate()
                improvement = pred.hashrate - best_so_far
            else:
                best_so_far = model.get_best_score()
                improvement = (0.6 * (1.0 / pred.efficiency) + 0.4 * pred.hashrate) - best_so_far
            
            # Expected Improvement
            z = improvement / (uncertainty + 1e-6)
            ei = improvement * norm.cdf(z) + uncertainty * norm.pdf(z)
            
            return -ei
        
        # Find best point
        best_params = model.get_best_parameters()
        x0 = [best_params.frequency, best_params.voltage, best_params.fan_speed]
        
        result = minimize(
            acquisition,
            x0=x0,
            bounds=[bounds['frequency'], bounds['voltage'], bounds['fan_speed']],
            method='L-BFGS-B'
        )
        
        optimal_params = OptimizationParameters(
            frequency=result.x[0],
            voltage=result.x[1],
            fan_speed=result.x[2]
        )
        outcome = model.predict(optimal_params)
        
        return OptimizationResult(
            parameters=optimal_params,
            predicted_outcome=outcome,
            optimization_success=result.success
        )
    
    def _genetic_optimization(self, model: DeviceModel, 
                               task: Task) -> OptimizationResult:
        """Genetic algorithm optimization."""
        from deap import base, creator, tools, algorithms
        
        bounds = model.get_parameter_bounds()
        
        # Define fitness function
        def evaluate(individual):
            params = OptimizationParameters(
                frequency=individual[0],
                voltage=individual[1],
                fan_speed=individual[2]
            )
            outcome = model.predict(params)
            
            if task.target == OptimizationTarget.EFFICIENCY:
                fitness = 1.0 / outcome.efficiency
            elif task.target == OptimizationTarget.HASHRATE:
                fitness = outcome.hashrate
            else:  # BALANCED
                fitness = 0.6 * (1.0 / outcome.efficiency) + 0.4 * outcome.hashrate
            
            return (fitness,)
        
        # Setup genetic algorithm
        creator.create("FitnessMax", base.Fitness, weights=(1.0,))
        creator.create("Individual", list, fitness=creator.FitnessMax)
        
        toolbox = base.Toolbox()
        toolbox.register("attr_freq", np.random.uniform, 
                        bounds['frequency'][0], bounds['frequency'][1])
        toolbox.register("attr_volt", np.random.uniform,
                        bounds['voltage'][0], bounds['voltage'][1])
        toolbox.register("attr_fan", np.random.uniform,
                        bounds['fan_speed'][0], bounds['fan_speed'][1])
        toolbox.register("individual", tools.initCycle, creator.Individual,
                        (toolbox.attr_freq, toolbox.attr_volt, toolbox.attr_fan), n=1)
        toolbox.register("population", tools.initRepeat, list, toolbox.individual)
        toolbox.register("evaluate", evaluate)
        toolbox.register("mate", tools.cxTwoPoint)
        toolbox.register("mutate", tools.mutGaussian, mu=0, sigma=10, indpb=0.2)
        toolbox.register("select", tools.selTournament, tournsize=3)
        
        # Run genetic algorithm
        pop = toolbox.population(n=50)
        algorithms.eaSimple(pop, toolbox, cxpb=0.7, mutpb=0.2, ngen=20, verbose=False)
        
        # Get best individual
        best_ind = tools.selBest(pop, k=1)[0]
        
        optimal_params = OptimizationParameters(
            frequency=best_ind[0],
            voltage=best_ind[1],
            fan_speed=best_ind[2]
        )
        outcome = model.predict(optimal_params)
        
        return OptimizationResult(
            parameters=optimal_params,
            predicted_outcome=outcome,
            optimization_success=True
        )
```

### Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MINER WORKFLOW                                   │
└─────────────────────────────────────────────────────────────────────────┘

1. Receive Task
   ┌─────────────┐
   │   Miner     │◀─────────────────────────────────────────────────────┐
   │             │                                                      │
   │  Explorer   │                                                      │
   └──────┬──────┘                                                      │
          │                                                             │
          │ 2. Select Parameters                                        │
          ▼                                                             │
   ┌─────────────┐                                                     │
   │   Miner     │                                                     │
   │             │                                                     │
   │  Query:     │─────────────────────────────────────────────────────┤
   │  (device_id,│                                                     │
   │   params)   │                                                     │
   └─────────────┘                                                     │
                                                                     │
┌─────────────────────────────────────────────────────────────────────────┤
│                        VALIDATOR WORKFLOW                              │
└─────────────────────────────────────────────────────────────────────────┘
                                                                     │
   ┌─────────────┐                                                     │
   │   Validator │◀────────────────────────────────────────────────────┘
   │             │
   │  ASIC       │
   │  Physics    │
   │  Simulator  │
   └──────┬──────┘
          │
          │ 3. Simulate with hidden parameters
          ▼
   ┌─────────────┐
   │   Validator │
   │             │
   │  Outcome:   │─────────────────────────────────────────────────────┐
   │  (temp,     │                                                     │
   │   power,    │                                                     │
   │   hashrate) │                                                     │
   └─────────────┘                                                     │
                                                                     │
┌─────────────────────────────────────────────────────────────────────────┤
│                         MINER WORKFLOW (cont.)                         │
└─────────────────────────────────────────────────────────────────────────┘
                                                                     │
   ┌─────────────┐                                                     │
   │   Miner     │◀────────────────────────────────────────────────────┘
   │             │
   │  Device     │
   │  Model      │
   │  Builder    │
   └──────┬──────┘
          │
          │ 4. Update model with observation
          ▼
   ┌─────────────┐
   │   Miner     │
   │             │
   │  Optimizer  │
   └──────┬──────┘
          │
          │ 5. Find optimal parameters using model
          ▼
   ┌─────────────┐
   │   Miner     │
   │             │
   │  Submit:    │─────────────────────────────────────────────────────┐
   │  (device_id,│                                                     │
   │   params)   │                                                     │
   └─────────────┘                                                     │
                                                                     │
┌─────────────────────────────────────────────────────────────────────────┤
│                        VALIDATOR WORKFLOW (cont.)                      │
└─────────────────────────────────────────────────────────────────────────┘
                                                                     │
   ┌─────────────┐                                                     │
   │   Validator │◀────────────────────────────────────────────────────┘
   │             │
   │  ASIC       │
   │  Physics    │
   │  Simulator  │
   └──────┬──────┘
          │
          │ 6. Verify final outcome
          ▼
   ┌─────────────┐
   │   Validator │
   │             │
   │  Scorer     │
   └──────┬──────┘
          │
          │ 7. Calculate score
          ▼
   ┌─────────────┐
   │   Validator │
   │             │
   │  Rank &     │
   │  Reward     │
   └─────────────┘
```

---

## Production-Ready Directory Structure

```
tensorclock/
│
├── src/                                    # Source code
│   ├── __init__.py
│   │
│   ├── protocol/                           # Bittensor protocol implementation
│   │   ├── __init__.py
│   │   ├── protocol.py                     # Main protocol class extending Bittensor
│   │   ├── constants.py                    # Protocol constants and enums
│   │   ├── state.py                        # State management and persistence
│   │   └── types.py                        # Type definitions and dataclasses
│   │
│   ├── validator/                          # Validator node implementation
│   │   ├── __init__.py
│   │   ├── validator.py                    # Main validator class extending Bittensor validator
│   │   │
│   │   ├── virtual_device/                 # Virtual Device Generator
│   │   │   ├── __init__.py
│   │   │   ├── generator.py                # Virtual device generation logic
│   │   │   ├── parameters.py               # Hidden parameter generation
│   │   │   ├── registry.py                 # Device registry management
│   │   │   └── specifications.py           # Base ASIC model specifications
│   │   │
│   │   ├── simulator/                      # ASIC Physics Simulator
│   │   │   ├── __init__.py
│   │   │   ├── core.py                     # Main simulator interface
│   │   │   ├── physical_models.py          # Physical constraint models (power, thermal, efficiency)
│   │   │   ├── validation.py               # Input validation and constraint checking
│   │   │   └── deterministic.py            # Deterministic verification logic
│   │   │
│   │   ├── task_manager/                   # Task Manager
│   │   │   ├── __init__.py
│   │   │   ├── manager.py                  # Task generation and management
│   │   │   ├── budget.py                   # Query budget tracking
│   │   │   └── distributor.py              # Task distribution logic
│   │   │
│   │   ├── scorer/                         # Scorer
│   │   │   ├── __init__.py
│   │   │   ├── engine.py                   # Main scoring engine
│   │   │   ├── efficiency.py               # Efficiency-based scoring
│   │   │   ├── hashrate.py                 # Hashrate-based scoring
│   │   │   └── balanced.py                 # Balanced scoring
│   │   │
│   │   └── consensus/                      # Consensus mechanism
│   │       ├── __init__.py
│   │       ├── mechanism.py                # Consensus algorithm
│   │       ├── aggregator.py               # Score aggregation
│   │       └── outlier_detector.py         # Outlier detection
│   │
│   ├── miner/                              # Miner node implementation
│   │   ├── __init__.py
│   │   ├── miner.py                        # Main miner class extending Bittensor miner
│   │   │
│   │   ├── explorer/                       # Explorer
│   │   │   ├── __init__.py
│   │   │   ├── explorer.py                 # Main exploration logic
│   │   │   ├── strategies/                 # Exploration strategies
│   │   │   │   ├── __init__.py
│   │   │   │   ├── random.py               # Random exploration
│   │   │   │   ├── grid.py                 # Grid exploration
│   │   │   │   ├── adaptive.py             # Adaptive exploration
│   │   │   │   └── bayesian.py             # Bayesian exploration
│   │   │   └── budget.py                   # Query budget management
│   │   │
│   │   ├── device_model/                   # Device Model Builder
│   │   │   ├── __init__.py
│   │   │   ├── builder.py                  # Main model builder
│   │   │   ├── models/                     # Model implementations
│   │   │   │   ├── __init__.py
│   │   │   │   ├── base.py                 # Base model class
│   │   │   │   ├── gaussian_process.py     # Gaussian Process model
│   │   │   │   ├── neural_network.py       # Neural Network model
│   │   │   │   └── ensemble.py             # Ensemble model
│   │   │   └── observations.py             # Observation management
│   │   │
│   │   └── optimizer/                      # Optimizer
│   │       ├── __init__.py
│   │       ├── optimizer.py                # Main optimizer interface
│   │       ├── strategies/                 # Optimization strategies
│   │       │   ├── __init__.py
│   │       │   ├── gradient.py             # Gradient-based optimization
│   │       │   ├── bayesian.py             # Bayesian optimization
│   │       │   ├── genetic.py              # Genetic algorithm optimization
│   │       │   └── grid.py                 # Grid search optimization
│   │       └── multi_objective.py          # Multi-objective optimization
│   │
│   ├── models/                             # ASIC model specifications
│   │   ├── __init__.py
│   │   ├── base.py                         # Base ASIC model class
│   │   ├── bitmain/                        # Bitmain ASIC models
│   │   │   ├── __init__.py
│   │   │   ├── s19.py                      # Antminer S19 specifications
│   │   │   ├── s19_pro.py                  # Antminer S19 Pro specifications
│   │   │   ├── s19j_pro.py                 # Antminer S19j Pro specifications
│   │   │   ├── s19j.py                     # Antminer S19j specifications
│   │   │   ├── s19k.py                     # Antminer S19k specifications
│   │   │   ├── s19xp.py                    # Antminer S19 XP specifications
│   │   │   ├── s21.py                      # Antminer S21 specifications
│   │   │   └── s21_xp.py                   # Antminer S21 XP specifications
│   │   └── whatsminer/                     # Whatsminer ASIC models
│   │       ├── __init__.py
│   │       ├── m30s.py                     # M30S specifications
│   │       ├── m30s_plus.py                # M30S+ specifications
│   │       ├── m30s_plus_plus.py           # M30S++ specifications
│   │       ├── m31s.py                     # M31S specifications
│   │       ├── m31s_plus.py                # M31S+ specifications
│   │       ├── m32.py                      # M32 specifications
│   │       ├── m33s.py                     # M33S specifications
│   │       ├── m50s.py                     # M50S specifications
│   │       ├── m50s_plus.py                # M50S+ specifications
│   │       ├── m53.py                      # M53 specifications
│   │       └── m53s.py                     # M53S specifications
│   │
│   ├── storage/                            # Data persistence layer
│   │   ├── __init__.py
│   │   ├── database.py                     # Database connection and session management
│   │   ├── models/                         # SQLAlchemy ORM models
│   │   │   ├── __init__.py
│   │   │   ├── base.py                     # Base model class
│   │   │   ├── virtual_device.py           # Virtual device storage
│   │   │   ├── task.py                     # Task storage
│   │   │   ├── query_budget.py             # Query budget tracking
│   │   │   ├── observation.py              # Miner observations
│   │   │   ├── submission.py               # Miner submissions
│   │   │   ├── validator_score.py          # Validator scores
│   │   │   └── audit_log.py                # Audit logging
│   │   ├── repositories/                   # Repository pattern for data access
│   │   │   ├── __init__.py
│   │   │   ├── device_repository.py        # Device repository
│   │   │   ├── task_repository.py          # Task repository
│   │   │   ├── budget_repository.py        # Budget repository
│   │   │   ├── observation_repository.py   # Observation repository
│   │   │   ├── submission_repository.py    # Submission repository
│   │   │   └── score_repository.py         # Score repository
│   │   └── cache/                          # Caching layer
│   │       ├── __init__.py
│   │       ├── redis_client.py             # Redis client and utilities
│   │       └── cache_manager.py            # Cache manager with TTL
│   │
│   ├── api/                                # REST API
│   │   ├── __init__.py
│   │   ├── app.py                          # FastAPI application
│   │   ├── main.py                         # Application entry point
│   │   ├── dependencies.py                 # FastAPI dependencies
│   │   ├── middleware/                     # API middleware
│   │   │   ├── __init__.py
│   │   │   ├── auth.py                     # Authentication middleware
│   │   │   ├── logging.py                  # Logging middleware
│   │   │   └── rate_limiting.py            # Rate limiting middleware
│   │   ├── routers/                        # API route handlers
│   │   │   ├── __init__.py
│   │   │   ├── health.py                   # Health check endpoints
│   │   │   ├── tasks.py                    # Task endpoints
│   │   │   ├── devices.py                  # Device endpoints
│   │   │   ├── queries.py                  # Query endpoints
│   │   │   ├── submissions.py              # Submission endpoints
│   │   │   └── scores.py                   # Score endpoints
│   │   ├── schemas/                        # Pydantic schemas for request/response
│   │   │   ├── __init__.py
│   │   │   ├── tasks.py                    # Task schemas
│   │   │   ├── devices.py                  # Device schemas
│   │   │   ├── queries.py                  # Query schemas
│   │   │   ├── submissions.py              # Submission schemas
│   │   │   └── common.py                   # Common schemas
│   │   └── security/                       # API security
│   │       ├── __init__.py
│   │       ├── auth.py                     # Authentication logic
│   │       ├── jwt.py                      # JWT token handling
│   │       └── permissions.py              # Permission checks
│   │
│   └── utils/                              # Utility functions
│       ├── __init__.py
│       ├── logging.py                      # Logging configuration
│       ├── metrics.py                      # Metrics collection
│       ├── config.py                       # Configuration management
│       ├── validators.py                   # Input validators
│       └── helpers.py                      # Helper functions
│
├── tests/                                  # Test suite
│   ├── __init__.py
│   ├── conftest.py                         # Pytest configuration and fixtures
│   ├── unit/                               # Unit tests
│   │   ├── __init__.py
│   │   ├── test_virtual_device/            # Virtual Device Generator tests
│   │   │   ├── __init__.py
│   │   │   ├── test_generator.py           # Generator tests
│   │   │   ├── test_parameters.py          # Parameter generation tests
│   │   │   └── test_registry.py            # Registry tests
│   │   ├── test_simulator/                 # Simulator unit tests
│   │   │   ├── __init__.py
│   │   │   ├── test_core.py                # Core simulator tests
│   │   │   ├── test_physical_models.py     # Physical model tests
│   │   │   └── test_validation.py          # Validation tests
│   │   ├── test_task_manager/              # Task Manager tests
│   │   │   ├── __init__.py
│   │   │   ├── test_manager.py             # Manager tests
│   │   │   └── test_budget.py              # Budget tests
│   │   ├── test_scorer/                    # Scorer tests
│   │   │   ├── __init__.py
│   │   │   ├── test_engine.py              # Scoring engine tests
│   │   │   └── test_efficiency.py          # Efficiency scoring tests
│   │   ├── test_explorer/                  # Explorer tests
│   │   │   ├── __init__.py
│   │   │   ├── test_explorer.py            # Explorer tests
│   │   │   └── test_strategies.py          # Strategy tests
│   │   ├── test_device_model/              # Device Model Builder tests
│   │   │   ├── __init__.py
│   │   │   ├── test_builder.py             # Builder tests
│   │   │   ├── test_gaussian_process.py    # Gaussian Process tests
│   │   │   └── test_neural_network.py      # Neural Network tests
│   │   ├── test_optimizer/                 # Optimizer tests
│   │   │   ├── __init__.py
│   │   │   ├── test_optimizer.py           # Optimizer tests
│   │   │   └── test_strategies.py          # Strategy tests
│   │   └── test_storage/                   # Storage unit tests
│   │       ├── __init__.py
│   │       ├── test_repositories.py        # Repository tests
│   │       └── test_cache.py               # Cache tests
│   ├── integration/                        # Integration tests
│   │   ├── __init__.py
│   │   ├── test_validator/                 # Validator integration tests
│   │   │   ├── __init__.py
│   │   │   ├── test_device_generation.py   # Device generation tests
│   │   │   ├── test_simulation.py          # Simulation tests
│   │   │   └── test_scoring.py             # Scoring tests
│   │   ├── test_miner/                     # Miner integration tests
│   │   │   ├── __init__.py
│   │   │   ├── test_exploration.py         # Exploration tests
│   │   │   ├── test_model_building.py      # Model building tests
│   │   │   └── test_optimization.py        # Optimization tests
│   │   └── test_protocol/                  # Protocol integration tests
│   │       ├── __init__.py
│   │       └── test_miner_validator.py     # Miner-validator integration
│   └── e2e/                                # End-to-end tests
│       ├── __init__.py
│       ├── test_complete_workflow.py       # Complete workflow test
│       └── test_query_budget.py            # Query budget test
│
├── scripts/                                # Utility scripts
│   ├── setup/                              # Setup scripts
│   │   ├── install.sh                      # Installation script
│   │   └── init_db.sh                      # Database initialization
│   ├── deployment/                         # Deployment scripts
│   │   ├── deploy.sh                       # Deployment script
│   │   ├── migrate.sh                      # Migration script
│   │   └── rollback.sh                     # Rollback script
│   ├── maintenance/                        # Maintenance scripts
│   │   ├── cleanup.sh                      # Cleanup script
│   │   ├── backup.sh                       # Backup script
│   │   └── restore.sh                      # Restore script
│   └── benchmark/                          # Benchmarking scripts
│       ├── benchmark_simulator.py          # Simulator benchmark
│       ├── benchmark_explorer.py            # Explorer benchmark
│       └── benchmark_optimizer.py          # Optimizer benchmark
│
├── docs/                                   # Documentation
│   ├── architecture/                       # Architecture documentation
│   │   ├── overview.md                     # Architecture overview
│   │   ├── components.md                   # Component specifications
│   │   ├── virtual_devices.md              # Virtual device generation
│   │   ├── data_flow.md                    # Data flow diagrams
│   │   └── security.md                     # Security architecture
│   ├── api/                                # API documentation
│   │   ├── overview.md                     # API overview
│   │   ├── endpoints.md                    # Endpoint documentation
│   │   ├── authentication.md               # Authentication guide
│   │   └── examples.md                     # API usage examples
│   ├── simulator/                          # Simulator documentation
│   │   ├── overview.md                     # Simulator overview
│   │   ├── models.md                       # ASIC model specifications
│   │   ├── physical_models.md              # Physical model documentation
│   │   └── validation.md                   # Validation rules
│   ├── models/                             # Model documentation
│   │   ├── bitmain.md                      # Bitmain model docs
│   │   └── whatsminer.md                   # Whatsminer model docs
│   ├── deployment/                         # Deployment documentation
│   │   ├── local.md                        # Local deployment guide
│   │   ├── docker.md                       # Docker deployment guide
│   │   ├── kubernetes.md                   # Kubernetes deployment guide
│   │   └── monitoring.md                   # Monitoring setup guide
│   ├── contributing/                       # Contributing guidelines
│   │   ├── overview.md                     # Contributing overview
│   │   ├── development.md                  # Development setup
│   │   ├── testing.md                      # Testing guidelines
│   │   └── style_guide.md                  # Code style guide
│   └── user/                               # User documentation
│       ├── getting_started.md              # Getting started guide
│       ├── miner_guide.md                  # Miner guide
│       ├── validator_guide.md              # Validator guide
│       └── sdk_guide.md                    # SDK guide
│
├── config/                                 # Configuration files
│   ├── development.yaml                    # Development configuration
│   ├── production.yaml                     # Production configuration
│   └── test.yaml                           # Test configuration
│
├── docker/                                 # Docker configuration
│   ├── Dockerfile                          # Main Dockerfile
│   ├── Dockerfile.validator                # Validator Dockerfile
│   ├── Dockerfile.miner                    # Miner Dockerfile
│   ├── docker-compose.yml                  # Docker Compose configuration
│   └── docker-compose.prod.yml             # Production Docker Compose
│
├── k8s/                                    # Kubernetes manifests
│   ├── namespace.yaml                      # Namespace definition
│   ├── configmap.yaml                      # Configuration map
│   ├── secret.yaml                         # Secrets
│   ├── deployments/                        # Deployment manifests
│   │   ├── validator.yaml                  # Validator deployment
│   │   └── miner.yaml                      # Miner deployment
│   ├── services/                           # Service manifests
│   │   ├── validator.yaml                  # Validator service
│   │   ├── miner.yaml                      # Miner service
│   │   └── api.yaml                        # API service
│   ├── ingress/                            # Ingress manifests
│   │   └── api.yaml                        # API ingress
│   └── monitoring/                         # Monitoring manifests
│       ├── prometheus.yaml                 # Prometheus deployment
│       └── grafana.yaml                    # Grafana deployment
│
├── monitoring/                             # Monitoring configuration
│   ├── prometheus/                         # Prometheus configuration
│   │   ├── prometheus.yml                  # Prometheus configuration
│   │   └── alerts.yml                      # Alert rules
│   ├── grafana/                            # Grafana dashboards
│   │   ├── dashboards/                     # Dashboard definitions
│   │   │   ├── overview.json               # Overview dashboard
│   │   │   ├── validators.json             # Validator dashboard
│   │   │   ├── miners.json                 # Miner dashboard
│   │   │   └── api.json                    # API dashboard
│   │   └── provisioning/                   # Grafana provisioning
│   │       ├── datasources.yml             # Datasource configuration
│   │       └── dashboards.yml              # Dashboard provisioning
│   └── loki/                               # Log aggregation
│       ├── loki.yml                        # Loki configuration
│       └── promtail.yml                    # Promtail configuration
│
├── agent_workflows/                        # Agent workflow documentation
│   ├── proposal.md                         # Original project proposal
│   ├── architecture_design.md              # This architecture design document
│   └── roadmap.md                          # Development roadmap
│
├── .gitignore                              # Git ignore rules
├── .env.example                            # Environment variables example
├── .dockerignore                           # Docker ignore rules
├── README.md                               # Project README
├── requirements.txt                        # Python dependencies
├── requirements-dev.txt                    # Development dependencies
├── requirements-test.txt                   # Test dependencies
├── setup.py                                # Package setup
├── pyproject.toml                          # Project configuration
├── pytest.ini                              # Pytest configuration
├── .pre-commit-config.yaml                 # Pre-commit hooks
├── LICENSE                                 # License file
└── CHANGELOG.md                            # Changelog
```

### Directory Structure Explanations

#### Core Source Code (`src/`)
- **`protocol/`**: Bittensor protocol implementation extending the existing [`protocol.py`](protocol.py)
- **`validator/`**: Validator node implementation extending [`validator.py`](validator.py)
  - **`virtual_device/`**: Virtual Device Generator - creates unique devices with hidden parameters
  - **`simulator/`**: ASIC Physics Simulator - simulates device behavior using hidden parameters
  - **`task_manager/`**: Task Manager - generates tasks and manages query budgets
  - **`scorer/`**: Scorer - evaluates miner solutions
  - **`consensus/`**: Consensus mechanism for validator agreement
- **`miner/`**: Miner node implementation extending [`miner.py`](miner.py)
  - **`explorer/`**: Explorer - selects parameters to query
  - **`device_model/`**: Device Model Builder - builds predictive models from observations
  - **`optimizer/`**: Optimizer - finds optimal parameters using models
- **`models/`**: ASIC model specifications organized by manufacturer (Bitmain, Whatsminer)
- **`storage/`**: Data persistence layer with SQLAlchemy ORM and Redis caching
- **`api/`**: REST API for commercial deployment using FastAPI
- **`utils/`**: Shared utility functions for logging, metrics, configuration

#### Test Suite (`tests/`)
- **`unit/`**: Unit tests for individual components (>60% of test coverage)
- **`integration/`**: Integration tests for component interactions (~30% of test coverage)
- **`e2e/`**: End-to-end tests for complete workflows (~10% of test coverage)

#### Utility Scripts (`scripts/`)
- **`setup/`**: Installation and initialization scripts
- **`deployment/`**: Deployment, migration, and rollback scripts
- **`maintenance/`**: Cleanup, backup, and restore scripts
- **`benchmark/`**: Performance benchmarking scripts

#### Documentation (`docs/`)
- **`architecture/`**: System architecture documentation
- **`api/`**: REST API documentation with examples
- **`simulator/`**: ASIC simulator and model documentation
- **`deployment/`**: Deployment guides for different environments
- **`contributing/`**: Development and contribution guidelines
- **`user/`**: User guides for miners, validators, and SDK users

---

## MVP Development Roadmap

### Overview

The MVP development is structured as an 8-phase, 32-week roadmap designed to deliver a production-ready TensorClock subnet with virtual device generation, query-based exploration, model-based optimization, and commercial API/SDK layers.

### Phase 1: Foundation (Weeks 1-4)

**Objective**: Establish project infrastructure and implement core protocol integration

**Milestones**:
- [ ] Week 1: Project setup and tooling
- [ ] Week 2: Protocol implementation
- [ ] Week 3: Virtual Device Generator foundation
- [ ] Week 4: Basic miner and validator

**Deliverables**:
- Development environment with linting, formatting, and pre-commit hooks
- Bittensor protocol integration extending [`protocol.py`](protocol.py)
- Virtual Device Generator with basic parameter generation
- ASIC Physics Simulator foundation with deterministic verification
- Minimal miner and validator implementations

**Tech Stack**:
- Python 3.11+
- Bittensor SDK
- Poetry for dependency management
- Black, Ruff, mypy for code quality
- pytest for testing

**Testing Strategy**:
- Unit tests for protocol integration
- Unit tests for virtual device generation
- Integration tests for miner-validator communication
- E2E test for basic submission workflow

**Success Criteria**:
- Virtual devices generated with unique hidden parameters
- Miner can submit queries to validator
- Validator can simulate outcomes deterministically
- All tests passing with >80% code coverage

---

### Phase 2: Core Components (Weeks 5-8)

**Objective**: Implement core validator and miner components

**Milestones**:
- [ ] Week 5: ASIC Physics Simulator
- [ ] Week 6: Task Manager
- [ ] Week 7: Explorer
- [ ] Week 8: Scorer

**Deliverables**:
- Complete ASIC Physics Simulator with physical models
- Task Manager with query budget tracking
- Explorer with multiple exploration strategies
- Scorer with efficiency, hashrate, and balanced scoring

**Tech Stack**:
- NumPy for numerical computations
- SciPy for physical modeling
- hypothesis for property-based testing

**Testing Strategy**:
- Property-based tests for physical models
- Unit tests for exploration strategies
- Integration tests for task management
- Unit tests for scoring algorithms

**Success Criteria**:
- Simulator produces deterministic results
- Query budgets properly enforced
- Exploration strategies functional
- Scoring algorithms correct

---

### Phase 3: Device Model Builder (Weeks 9-12)

**Objective**: Implement device model building capabilities

**Milestones**:
- [ ] Week 9: Base model interface
- [ ] Week 10: Gaussian Process model
- [ ] Week 11: Neural Network model
- [ ] Week 12: Ensemble model

**Deliverables**:
- Base device model interface
- Gaussian Process regression model with uncertainty
- Neural Network model for complex relationships
- Ensemble model combining multiple approaches
- Observation management system

**Tech Stack**:
- scikit-learn for Gaussian Process
- TensorFlow/Keras for Neural Networks
- NumPy for numerical operations

**Testing Strategy**:
- Unit tests for each model type
- Comparative tests between models
- Integration tests for model updates
- Performance benchmarks

**Success Criteria**:
- All model types functional
- Models improve predictions with more observations
- Uncertainty estimates accurate
- Ensemble model outperforms individual models

---

### Phase 4: Optimizer (Weeks 13-16)

**Objective**: Implement optimization strategies

**Milestones**:
- [ ] Week 13: Gradient-based optimization
- [ ] Week 14: Bayesian optimization
- [ ] Week 15: Genetic algorithms
- [ ] Week 16: Multi-objective optimization

**Deliverables**:
- Gradient-based optimization using model gradients
- Bayesian optimization using model uncertainty
- Genetic algorithm optimization
- Multi-objective optimization for balanced targets
- Strategy selection logic

**Tech Stack**:
- SciPy for gradient-based optimization
- scikit-optimize for Bayesian optimization
- DEAP for genetic algorithms

**Testing Strategy**:
- Unit tests for each optimization strategy
- Comparative tests between strategies
- Integration tests with device models
- Performance benchmarks

**Success Criteria**:
- All optimization strategies functional
- Strategies converge to good solutions
- Multi-objective optimization works
- Strategy selection chooses optimal strategy

---

### Phase 5: Consensus & Royalty (Weeks 17-20)

**Objective**: Implement consensus mechanism and royalty system

**Milestones**:
- [ ] Week 17: Consensus mechanism
- [ ] Week 18: Score aggregation
- [ ] Week 19: Royalty system
- [ ] Week 20: Integration

**Deliverables**:
- Byzantine fault-tolerant consensus mechanism
- Score aggregation with outlier detection
- Royalty system with stability tracking
- Full integration with scoring and consensus

**Tech Stack**:
- NumPy for numerical operations
- SQLAlchemy for data persistence
- Redis for caching

**Testing Strategy**:
- Unit tests for consensus mechanism
- Integration tests for score aggregation
- E2E tests for royalty workflow
- Load tests for consensus calculation

**Success Criteria**:
- Consensus achieves agreement with <1% variance
- Outliers properly detected and removed
- Royalty transitions occur correctly
- No race conditions in state transitions

---

### Phase 6: API & SDK (Weeks 21-24)

**Objective**: Implement commercial API and Python SDK

**Milestones**:
- [ ] Week 21: REST API foundation
- [ ] Week 22: API endpoints
- [ ] Week 23: Python SDK
- [ ] Week 24: Testing

**Deliverables**:
- FastAPI application with authentication
- Task, device, query, submission, and score endpoints
- Python SDK with client libraries
- Comprehensive API documentation

**Tech Stack**:
- FastAPI for REST API
- JWT for authentication
- Pydantic for request/response validation
- httpx for SDK HTTP client
- Sphinx for API documentation

**Testing Strategy**:
- Unit tests for API endpoints
- Integration tests for API workflows
- SDK client tests
- Load tests for API performance

**Success Criteria**:
- All endpoints functional and documented
- API response time <100ms (p95)
- SDK covers all API functionality
- Authentication and authorization working

---

### Phase 7: Testing & QA (Weeks 25-28)

**Objective**: Comprehensive testing and quality assurance

**Milestones**:
- [ ] Week 25: Unit tests
- [ ] Week 26: Integration tests
- [ ] Week 27: E2E tests
- [ ] Week 28: Performance testing

**Deliverables**:
- >90% code coverage with unit tests
- Comprehensive integration test suite
- E2E tests for all major workflows
- Performance benchmarks and optimization
- Load testing with locust
- Security audit and penetration testing
- Test automation and CI/CD integration

**Tech Stack**:
- pytest for testing framework
- hypothesis for property-based testing
- locust for load testing
- testcontainers for integration testing
- coverage.py for coverage reporting

**Testing Strategy**:
- **Unit Tests**: 60% of test coverage, focus on individual components
- **Integration Tests**: 30% of test coverage, focus on component interactions
- **E2E Tests**: 10% of test coverage, focus on complete workflows
- **Performance Tests**: Load testing with 1000+ concurrent requests
- **Security Tests**: Penetration testing and vulnerability scanning

**Success Criteria**:
- >90% code coverage achieved
- All tests passing consistently
- Performance meets requirements (<100ms API response time)
- No critical security vulnerabilities
- CI/CD pipeline fully automated

---

### Phase 8: Deployment & Operations (Weeks 29-32)

**Objective**: Production deployment and operational readiness

**Milestones**:
- [ ] Week 29: Containerization
- [ ] Week 30: Kubernetes
- [ ] Week 31: Monitoring
- [ ] Week 32: Production deployment

**Deliverables**:
- Docker images for all components
- Docker Compose for local development
- Kubernetes manifests for production
- Prometheus monitoring setup
- Grafana dashboards for observability
- Loki log aggregation
- Alerting rules and notifications
- Deployment automation
- Backup and disaster recovery procedures
- Runbooks and operational documentation

**Tech Stack**:
- Docker for containerization
- Kubernetes for orchestration
- Prometheus for metrics collection
- Grafana for visualization
- Loki for log aggregation
- Helm for package management

**Testing Strategy**:
- Integration tests for deployment pipeline
- Chaos engineering tests for resilience
- Disaster recovery drills
- Monitoring and alerting validation

**Success Criteria**:
- All components containerized and deployable
- Kubernetes cluster operational
- Monitoring and alerting functional
- Deployment automated and repeatable
- Disaster recovery tested and validated

---

## Technical Specifications

### Technology Stack

#### Core Technologies
- **Language**: Python 3.11+
- **Blockchain**: Bittensor SDK
- **Web Framework**: FastAPI
- **Database**: PostgreSQL 15+
- **Cache**: Redis 7+
- **Message Queue**: Celery + Redis

#### Machine Learning Libraries
- **Numerical Computing**: NumPy, SciPy
- **Gaussian Process**: scikit-learn
- **Neural Networks**: TensorFlow/Keras
- **Optimization**: scikit-optimize (skopt), DEAP, Optuna

#### Data & Storage
- **ORM**: SQLAlchemy 2.0+
- **Migrations**: Alembic
- **Validation**: Pydantic v2
- **Serialization**: msgpack

#### Testing
- **Testing Framework**: pytest
- **Property-Based Testing**: hypothesis
- **Load Testing**: locust
- **Integration Testing**: testcontainers
- **Coverage**: coverage.py

#### Deployment
- **Containerization**: Docker
- **Orchestration**: Kubernetes
- **Package Management**: Helm
- **CI/CD**: GitHub Actions

#### Monitoring & Observability
- **Metrics**: Prometheus
- **Visualization**: Grafana
- **Logging**: Loki + Promtail
- **Tracing**: OpenTelemetry

#### Documentation
- **API Docs**: FastAPI auto-generated
- **Technical Docs**: Sphinx
- **User Docs**: MkDocs
- **Diagrams**: Mermaid

### System Requirements

#### Minimum Requirements (Development)
- **CPU**: 4 cores
- **RAM**: 8 GB
- **Storage**: 50 GB SSD
- **Network**: 100 Mbps

#### Recommended Requirements (Production Validator)
- **CPU**: 16+ cores
- **RAM**: 32+ GB
- **Storage**: 500+ GB NVMe SSD
- **Network**: 1+ Gbps

#### Recommended Requirements (Production Miner)
- **CPU**: 8+ cores
- **RAM**: 16+ GB
- **Storage**: 100+ GB SSD
- **Network**: 1+ Gbps

### Performance Targets

#### API Performance
- **Response Time (p50)**: <50ms
- **Response Time (p95)**: <100ms
- **Response Time (p99)**: <200ms
- **Throughput**: 1000+ requests/second

#### Simulation Performance
- **Simulation Time**: <10ms per query
- **Verification Time**: <5ms per submission
- **Consensus Time**: <1s per round

#### Exploration Performance
- **Query Time**: <50ms per query
- **Model Update Time**: <100ms per observation
- **Optimization Time**: <5 seconds per task

#### Database Performance
- **Query Time (p95)**: <50ms
- **Write Throughput**: 1000+ writes/second
- **Read Throughput**: 10,000+ reads/second

### Security Requirements

#### Authentication
- JWT-based authentication
- Token expiration: 1 hour
- Refresh token: 30 days
- Multi-factor authentication for admin access

#### Authorization
- Role-based access control (RBAC)
- Permissions: read, write, admin
- API key authentication for SDK

#### Encryption
- TLS 1.3 for all network communication
- AES-256 for data at rest
- Hashed passwords with bcrypt

#### Audit Logging
- All queries logged
- All submissions logged
- All API calls logged
- All administrative actions logged
- Immutable audit trail

---

## Testing Strategy

### Testing Pyramid

```
        /\
       /  \      E2E Tests (10%)
      /____\     - Complete workflows
     /      \    - Query budget management
    /________\   - Integration validation
   /          \  
  /  Integration\  Integration Tests (30%)
 /    Tests     \ - Component interactions
/_______________\ - API endpoints
                 - Database operations
                 
   Unit Tests (60%)
   - Virtual device generation
   - Physical models
   - Exploration strategies
   - Device models
   - Optimization strategies
   - Scoring algorithms
```

### Unit Testing

**Coverage Target**: >90%

**Key Areas**:
- Virtual Device Generator parameter generation
- ASIC Physics Simulator physical models
- Task Manager budget tracking
- Scorer calculation algorithms
- Explorer selection strategies
- Device Model Builder learning
- Optimizer search strategies
- Repository methods
- Utility functions

**Tools**:
- pytest
- pytest-cov
- hypothesis (property-based testing)
- pytest-mock

**Example Test Structure**:
```python
def test_virtual_device_generation():
    """Test virtual device generation with unique parameters."""
    generator = VirtualDeviceGenerator("bitmain_s19")
    device1 = generator.generate_device(seed=42)
    device2 = generator.generate_device(seed=43)
    
    assert device1.device_id != device2.device_id
    assert 0.92 <= device1.hidden_parameters.silicon_quality <= 1.08
    assert 0.0 <= device1.hidden_parameters.degradation <= 0.2

@given(seed=st.integers(min_value=0, max_value=1000))
def test_device_uniqueness(seed):
    """Test that different seeds produce different devices."""
    generator = VirtualDeviceGenerator("bitmain_s19")
    device1 = generator.generate_device(seed=seed)
    device2 = generator.generate_device(seed=seed + 1)
    
    assert device1.hidden_parameters.silicon_quality != device2.hidden_parameters.silicon_quality
```

### Integration Testing

**Coverage Target**: >80%

**Key Areas**:
- Miner-validator communication
- Query workflow (query → simulate → outcome)
- Task generation and distribution
- Model building from observations
- Optimization using models
- API endpoint workflows
- Database operations
- Cache integration

**Tools**:
- pytest
- testcontainers (PostgreSQL, Redis)
- pytest-asyncio
- httpx (for API testing)

**Example Test Structure**:
```python
def test_query_workflow(test_db, test_redis):
    """Test complete query workflow."""
    # Setup
    validator = setup_validator(test_db, test_redis)
    miner = setup_miner(test_db, test_redis)
    
    # Create device and task
    device = validator.virtual_device_generator.generate_device()
    task = validator.task_manager.create_task(device, query_budget=10)
    
    # Execute query
    params = OptimizationParameters(frequency=600, voltage=13.0, fan_speed=100)
    outcome = validator.simulator.simulate(device.device_id, params)
    
    # Verify
    assert outcome.valid == True
    assert outcome.temperature > 0
    assert outcome.power > 0
    assert outcome.hashrate > 0
```

### End-to-End Testing

**Coverage Target**: All major workflows

**Key Workflows**:
- Complete mining workflow: task → exploration → model building → optimization → submission → scoring
- Query budget workflow: budget tracking → consumption → efficiency bonus
- Model building workflow: observations → model → predictions → updates
- Consensus workflow: scoring → aggregation → consensus → ranking

**Tools**:
- pytest
- playwright (for API testing)
- locust (for load testing)

**Example Test Structure**:
```python
def test_complete_mining_workflow():
    """Test complete mining workflow from task to scoring."""
    # Setup
    validator = setup_validator()
    miner = setup_miner()
    
    # Create task
    device = validator.virtual_device_generator.generate_device()
    task = validator.task_manager.create_task(device, query_budget=20)
    
    # Miner exploration
    for _ in range(10):
        params = miner.explorer.select_parameters(miner.model, 10, task)[0]
        outcome = validator.simulator.simulate(device.device_id, params)
        miner.device_model_builder.update(Observation(params, outcome))
    
    # Miner optimization
    result = miner.optimizer.optimize(miner.model, task, 10)
    
    # Miner submission
    submission = miner.submit(task.task_id, result.parameters)
    
    # Validator scoring
    final_outcome = validator.simulator.simulate(device.device_id, result.parameters)
    score = validator.scorer.calculate_score(task, miner.miner_id, final_outcome, 10)
    
    # Verify
    assert final_outcome.valid == True
    assert score > 0
```

### Performance Testing

**Tools**:
- locust (load testing)
- pytest-benchmark (microbenchmarks)
- Apache Bench (ab) for API testing

**Key Metrics**:
- API response time (p50, p95, p99)
- Throughput (queries/second)
- Simulation time
- Model update time
- Optimization time
- Database query time

**Example Load Test**:
```python
class QueryAPIUser(HttpUser):
    wait_time = between(1, 3)
    
    @task
    def submit_query(self):
        response = self.client.post(
            "/api/v1/queries",
            json={
                "device_id": "device_abc123",
                "parameters": {
                    "frequency": 600,
                    "voltage": 13.0,
                    "fan_speed": 100
                }
            }
        )
        assert response.status_code == 200
```

### Security Testing

**Tools**:
- OWASP ZAP
- Bandit (Python security linter)
- Safety (dependency vulnerability scanner)
- pytest-security

**Key Areas**:
- SQL injection
- XSS attacks
- Authentication bypass
- Authorization bypass
- Rate limiting
- Query budget manipulation
- Hidden parameter exposure

---

## Deployment Strategy

### Development Environment

**Setup**:
```bash
# Clone repository
git clone https://github.com/tensorclock/tensorclock.git
cd tensorclock

# Install dependencies
poetry install

# Setup pre-commit hooks
pre-commit install

# Initialize database
python scripts/setup/init_db.sh

# Run tests
pytest

# Start development server
uvicorn src.api.main:app --reload
```

**Components**:
- Local PostgreSQL database
- Local Redis cache
- Development FastAPI server
- Mock Bittensor network

### Docker Deployment

**Build Images**:
```bash
# Build main image
docker build -t tensorclock:latest -f docker/Dockerfile .

# Build validator image
docker build -t tensorclock-validator:latest -f docker/Dockerfile.validator .

# Build miner image
docker build -t tensorclock-miner:latest -f docker/Dockerfile.miner .
```

**Run with Docker Compose**:
```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

**Services**:
- PostgreSQL database
- Redis cache
- Validator node
- Miner node
- API server
- Prometheus
- Grafana

### Kubernetes Deployment

**Prerequisites**:
- Kubernetes cluster (minikube, EKS, GKE, AKS)
- kubectl configured
- Helm installed

**Deploy**:
```bash
# Create namespace
kubectl apply -f k8s/namespace.yaml

# Deploy secrets
kubectl apply -f k8s/secret.yaml

# Deploy configmap
kubectl apply -f k8s/configmap.yaml

# Deploy validator
kubectl apply -f k8s/deployments/validator.yaml

# Deploy miner
kubectl apply -f k8s/deployments/miner.yaml

# Deploy API
kubectl apply -f k8s/deployments/api.yaml

# Deploy monitoring
kubectl apply -f k8s/monitoring/prometheus.yaml
kubectl apply -f k8s/monitoring/grafana.yaml
```

**Scale**:
```bash
# Scale validators
kubectl scale deployment validator --replicas=5

# Scale miners
kubectl scale deployment miner --replicas=10

# Scale API
kubectl scale deployment api --replicas=3
```

### Monitoring Setup

**Prometheus**:
```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'tensorclock'
    static_configs:
      - targets: ['validator:8000', 'miner:8000', 'api:8000']
```

**Grafana Dashboards**:
- Overview dashboard: System-wide metrics
- Validator dashboard: Validator-specific metrics
- Miner dashboard: Miner-specific metrics
- API dashboard: API performance metrics

**Alerting Rules**:
```yaml
# alerts.yml
groups:
  - name: tensorclock
    rules:
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.05
        for: 5m
        annotations:
          summary: "High error rate detected"
      
      - alert: QueryBudgetExhausted
        expr: query_budget_remaining == 0
        for: 1m
        annotations:
          summary: "Query budget exhausted"
```

### Backup Strategy

**Database Backups**:
```bash
# Daily backup
0 2 * * * pg_dump -h postgres -U tensorclock tensorclock > /backup/tensorclock_$(date +\%Y\%m\%d).sql

# Retention policy
find /backup -name "tensorclock_*.sql" -mtime +30 -delete
```

**Redis Backups**:
```bash
# Hourly RDB snapshot
save 3600 1

# AOF persistence
appendonly yes
appendfsync everysec
```

### Disaster Recovery

**Recovery Procedures**:
1. Restore database from latest backup
2. Restore Redis from RDB/AOF file
3. Restart all services
4. Verify data integrity
5. Monitor system health

**RTO (Recovery Time Objective)**: 4 hours  
**RPO (Recovery Point Objective)**: 1 hour

---

## Success Metrics

### Technical Metrics

#### Simulation Accuracy
- **Target**: >95% accuracy compared to real hardware
- **Measurement**: Compare simulation results with real-world data
- **Frequency**: Weekly validation

#### Consensus Agreement
- **Target**: >99% agreement between validators
- **Measurement**: Calculate variance in validator scores
- **Frequency**: Per round

#### API Performance
- **Target**: <100ms response time (p95)
- **Measurement**: Monitor API response times
- **Frequency**: Continuous monitoring

#### Code Coverage
- **Target**: >90% code coverage
- **Measurement**: Run coverage.py
- **Frequency**: Per commit

#### Uptime
- **Target**: >99.9% uptime
- **Measurement**: Monitor service availability
- **Frequency**: Continuous monitoring

### Network Metrics

#### Miner Participation
- **Target**: >50 active miners
- **Measurement**: Count active miner nodes
- **Frequency**: Daily

#### Validator Participation
- **Target**: >10 active validators
- **Measurement**: Count active validator nodes
- **Frequency**: Daily

#### Query Volume
- **Target**: >100,000 daily queries
- **Measurement**: Count queries per day
- **Frequency**: Daily

#### Model Coverage
- **Target**: Support for 5+ ASIC models
- **Measurement**: Count supported ASIC models
- **Frequency**: Per release

### Business Metrics

#### Commercial Deployments
- **Target**: >10 commercial deployments
- **Measurement**: Count active commercial customers
- **Frequency**: Monthly

#### Device Count
- **Target**: >5,000 devices under optimization
- **Measurement**: Sum devices across all deployments
- **Frequency**: Monthly

#### Monthly Recurring Revenue (MRR)
- **Target**: >$25,000 MRR
- **Measurement**: Sum subscription revenue
- **Frequency**: Monthly

#### Customer Satisfaction
- **Target**: >90% customer satisfaction
- **Measurement**: Customer surveys
- **Frequency**: Quarterly

### Quality Metrics

#### Bug Rate
- **Target**: <5 critical bugs per release
- **Measurement**: Count critical bugs in issue tracker
- **Frequency**: Per release

#### Feature Delivery
- **Target**: >80% of planned features delivered on time
- **Measurement**: Track feature completion vs. plan
- **Frequency**: Per sprint

#### Documentation Coverage
- **Target**: 100% API documentation
- **Measurement**: Review API documentation completeness
- **Frequency**: Per release

---

## Conclusion

This comprehensive technical architecture design provides a complete blueprint for developing the TensorClock subnet with the new architecture scheme featuring:

- **Virtual Device Generator**: Creates unique virtual devices with hidden parameters representing individual device characteristics
- **ASIC Physics Simulator**: Simulates device behavior using device-specific hidden parameters
- **Task Manager**: Generates tasks and manages query budgets
- **Scorer**: Evaluates miner solutions considering outcome quality and query efficiency
- **Explorer**: Selects parameters to query with multiple exploration strategies
- **Device Model Builder**: Builds predictive models from observations using Gaussian Processes, Neural Networks, and Ensembles
- **Optimizer**: Finds optimal parameters using gradient-based, Bayesian, and genetic algorithms

The 32-week, 8-phase roadmap provides a clear path from foundation to production launch, with specific milestones, deliverables, and success criteria for each phase.

---

**Document Version**: 2.0  
**Last Updated**: 2026-03-10  
**Next Review**: 2026-04-10
