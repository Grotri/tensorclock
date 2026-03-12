# TensorClock - dynamic AI ASIC overclocking layer

# Summary

---

TensorClock is a Bittensor subnet that creates a decentralized intelligence layer for ASIC mining optimization. The subnet leverages distributed exploration by miners to build superior optimization models that maximize mining profitability while extending equipment lifespan.

**Core Value Proposition:** Transform static/semi-static mining firmware into dynamic, AI-driven optimization that adapts to electricity prices, equipment degradation, and environmental conditions in real-time.

**Market Opportunity:** The global crypto mining optimization market represents a $50-100 million annual opportunity, with over 85% of industrial miners currently operating with suboptimal semi-static configurations that fail to adapt to all possible contextual variables.

**Competitive Advantage:** this is the first decentralized approach to mining optimization, combining crowd-sourced parameter exploration with proprietary model training to produce continuously improving optimization intelligence.

# **Incentive Design**

---

**Miners** explore the parameter space of ASIC optimization through simulated experiments, testing various combinations of frequency, voltage, and fan speed settings across different device contexts and economic conditions.

**Validators** maintain the simulation environment, collect and verify exploration data, evaluate miner contributions.

**The Network** produces continuously improving optimization models that can be deployed commercially to mining operations worldwide.

---

Miners receive rewards based on the quality of their exploration results as evaluated through the simulator. The primary scoring metric is a weighted combination of energy efficiency (J/Th) total mining power of tested machine (Th).

Overall miner reward model - Winner takes all. But each model of ASIC has it’s own winner, so with further expansion of available ASIC models incetive would be splitted to many contributors and their models.

---

The subnet implements a unique royalty system that rewards long-term contribution to model quality.

When the simulator has been thoroughly validated against real-world hardware data and a model achieves stability (defined as no improvement found by any miner for seven consecutive days), that model transitions to royalty mode. In this state, the leading contributor who achieved the final optimized parameters continues receiving a percentage of ongoing emissions as passive income, recognizing their lasting contribution to the network's commercial value.

This royalty percentage gradually decreases as the subnet expands support to new ASIC models, beginning with the most widely deployed devices and progressively covering the complete Bitmain and Whatsminer product lines. This graduated reduction ensures that early contributors are rewarded while maintaining sustainable economics as the network grows.

If any miner subsequently discovers parameters that improve upon a model in royalty mode, that model returns to active development status. The emissions allocated to active development are then distributed equally across all ASIC models currently under active optimization, ensuring balanced incentive regardless of device popularity. Also royalty mode might be disabled due to deployment of new simulator, that means that current optimal parameters were not presicely accurate because of simulation error that was found and fixed according to testing on real machines.

# Anti-cheating mechanisms

---

The ASIC simulator implements strict physical constraints that prevent miners from submitting unrealistic parameter combinations or claiming impossible outcomes.

**Hardware Limits:** The simulator enforces absolute boundaries based on real device specifications. Frequency cannot exceed the maximum rated clock speed for each chip type. Voltage is constrained to the safe operating range specified by the manufacturer. Power consumption follows validated physical models that prevent claiming efficiency beyond theoretical chip limits.

**Thermal Constraints:** The simulator models heat dissipation based on established thermal physics. Chip temperature is calculated from power consumption, ambient conditions, and cooling capacity. Settings that would produce temperatures exceeding safe operating limits are automatically rejected or penalized. The simulator cannot return successful outcomes for configurations that would cause thermal shutdown in real hardware.

**Efficiency Boundaries:** The simulator implements floor and ceiling values for joules-per-terahash based on measured performance from real devices. No parameter combination can achieve efficiency better than the best-documented real-world results for that chip type, preventing miners from discovering "impossible" optimizations that only exist in simulator artifacts.

---

A critical anti-gaming measure prevents models from exploiting cyclical patterns that would damage real equipment.

**Problem:** Without constraints, optimization might converge on strategies that cycle between aggressive overclocking (pushing temperatures to maximum limits) and conservative cooldown periods. While such patterns might achieve good average scores in simulation, they would cause accelerated wear and premature failure in actual devices due to thermal stress from repeated heating and cooling cycles.

**Solution:** The simulator implements pattern detection that identifies oscillating behavior. Submissions are analyzed for frequency and voltage variance over time. Solutions exhibiting high-amplitude periodic patterns in operating parameters receive scoring penalties proportional to the oscillation severity.

---

Every outcome claimed by a miner is independently verified by the validator through simulation. Since the simulator is deterministic, any fabricated or manipulated results are immediately detected. Miners submitting data that fails verification receive zero rewards.

# Miner & Validator tasks

---

Task input format:

- ASIC model (stands for current task)
- Hashvalue ($ per Th)
- Electricity price ($ per h/kW)
- Current ASIC clock speed (per-chip/per-board/per-machine would be defined depending on ASIC model)
- Current ASIC voltage
- Current ASIC temperature
- Current ASIC fan speed

Task output format:

- Optimised ASIC clock speed
- Optimised ASIC voltage
- Optimised ASIC fan speed (there is possibility it would be hard-coded to maximum/left as user-preferred setting)

---

Validators perform several critical functions in the TensorClock network.

**Simulation Environment:** Validators maintain the ASIC simulator that models device behavior under various operating parameters. This simulator must be accurate, consistent across validators, and resistant to manipulation.

**Request Processing:** Validators request optimising of test values in real-time, providing outcomes for proposed parameter combinations. This requires sufficient computational capacity to handle request volume from all active miners.

**Scoring and Ranking:** Validators calculate composite scores for all submissions and rank miners by performance. Consensus mechanisms ensure that validators agree on rankings.

# **Business Logic & Market Rationale**

---

**Problem:** Industrial Bitcoin miners operate with static firmware configurations that fail to adapt to changing conditions.

**Time-Varying Electricity Prices:** Most electricity markets exhibit significant price variation throughout the day, often 2-4x between peak and off-peak periods. Static firmware cannot adjust operating parameters to minimize cost during high-price periods or maximize production during low-price periods.

**Individual Device Variance:** Silicon manufacturing variance creates 5-8% efficiency differences between devices of the same model. Static firmware applies identical settings to all devices regardless of their individual characteristics. This means most devices in any fleet are operating suboptimally, either underperforming their potential or consuming more power than necessary.

**Equipment Degradation:** ASIC chips degrade over their operational lifetime, with efficiency declining 10-20% over typical 2-3 year deployment. The optimal operating parameters shift as devices age, but static firmware cannot adapt. Devices either operate increasingly suboptimally or require manual reconfiguration that is impractical at scale.

**Environmental Conditions:** Ambient temperature significantly affects optimal operating parameters. Seasonal changes, heat waves, and cooling system variations all require different operating profiles. Static firmware cannot respond to these conditions, leading to either underperformance in favorable conditions or thermal stress in challenging conditions.

---

**Solution:** TensorClock produces dynamic, AI-driven optimization models that adapt operating parameters based on current conditions.

The models adjust settings on time intervals as short as 2-3 minutes, responding to electricity price changes, temperature variations, and device health indicators.

Each device receives individualized parameters based on its specific characteristics, including silicon quality, age, and historical performance.

---

**Competing solusions.**

**Braiins OS+** provides autotuning firmware that optimizes device settings during initial installation. However, their DPS system allows targetting per-device optimisation (as well as per-chip tuning), but compatible devices are only from Bitmain, also 2.5% fee. The firmware does not adapt to electricity prices.

**Luxor Firmware** offer optimized semi-dynamic firmware profiles. These represent improvements over manufacturer defaults but share the same fundamental limitation: settings do not change based on all context conditions. Not to mention that there is only Bitmain machines supported.

**VNish** and other overclocking firmware focus on maximizing hashrate, often at the expense of efficiency and equipment longevity. They do not provide economic optimization or dynamic adaptation.

**Monitoring platforms** like Hive OS provide visibility into device performance but do not automatically optimize settings. They show data but require manual intervention to act on it.

# Go-To-Market Strategy

---

**Initial Target Users**

**Primary: Mid-Size Mining Operations**

The ideal early adopters operate between 500 and 3,000 ASIC devices. They have significant electricity spend creating strong motivation to optimize. They are technically sophisticated enough to understand the value proposition but lack resources to develop custom optimization solutions internally.

**Secondary: Hosted Mining and Mining Pools**

Operations that manage devices on behalf of customers need to demonstrate value-add services. Automated optimization differentiates their offering and improves customer returns without requiring additional staff.

---

**Anchor Use Cases**

**Time-of-Use Optimization:** The most immediately compelling use case for miners in markets with significant price variation.

**Fleet Health Management:** Identifying devices operating suboptimally before they fail. Extending equipment lifespan through appropriate operating parameters.

**Environmental Adaptation:** Automatic response to heat waves and seasonal changes without manual intervention.

---

**Distribution Channels**

**Industry events** and regional conferences provide access to decision-makers at target operations.

**Direct outreach** through LinkedIn and industry networks targets operations managers and technical leadership at qualifying mining operations.

**Integration partnerships with firmware vendors** (Braiins, Luxor) provide distribution through their existing customer relationships.

**Mining pool partnerships** enable white-label deployment to pool participants.

# Roadmap

---

- **Phase 1: Proof of concept**

Launch the subnet on testnet with support for the most popular ASIC model using publicly available data. Demonstrate model quality through simulation benchmarks. Recruit initial miner and validator cohort.

- **Phase 2: Simulator Validation**

Calibrate physical models using measured data from actual devices. Establish confidence that simulation results translate to real-world performance.

- **Phase 3: Pilot Deployment**

Deploy optimization to a small fleet of real devices. Measure actual performance improvements compared to static firmware baselines. Iterate on models based on real-world feedback.

- **Phase 4: Commercial Launch**

Launch SaaS product for mining operators. Expand device support to cover major Bitmain and Whatsminer models. Establish recurring revenue through subscription pricing.

- **Phase 5: Scale**

Expand to larger deployments. Add support for additional device types. Develop enterprise partnerships with large mining operations.

# Revenue model

---

Commercial deployment follows a SaaS model with per-device monthly pricing. Initial target is 5$ per device per month, representing clear ROI for customers who see 5-15% profit improvement and up to 30% decrease in maintenance costs.

Our model vs. Braiins OS+ (data collected from [whattomine.com](http://whattomine.com) and braiins website)

- ASIC: Antminer S21 XP
- Electricity cost = $0.05/h/kW
- Revenue increased by 8% as most optimal solution by Braiins stated
- Electricity cost decreased by 15% on our side and 10% on braiins side, this gap was created due to braiins firmware not being able to optimise to the current electricity price

# Why BitTensor?

---

**Massive Exploration Space:** The optimization problem involves three continuous variables (frequency, voltage, fan speed) and many contextual variables, including multiple device types. This combinatorial space benefits enormously from distributed exploration across many participants.

**No Single Optimal Solution:** The optimal parameters depend entirely on context. Different devices, conditions, and electricity prices require different solutions. This diversity makes the problem well-suited to decentralized intelligence where many participants explore different regions of the solution space.

**Continuous Evolution:** New ASIC models release regularly. Market conditions change. Optimal strategies evolve. A decentralized network can continuously adapt through ongoing competition, while centralized solutions require dedicated development resources for each change.

**Verifiable Computation:** Deterministic simulation allows complete verification of all claimed results. No trust is required; everything is verified. This makes the subnet resistant to manipulation while ensuring that rewards correspond to genuine contribution.