# PS-03 Virtual Wearable Research Simulator

This project uses the supplied revised Synthea cohort ZIP to run a ten-profile, Python-only virtual wearable simulation and replay five original BIDMC recordings. It is a **research/hackathon prototype**, not a clinically validated dataset, medical device, diagnostic system, or source of medical advice.

The revised cohort marks patient-specific initial vital signs as pending. Until an authorized patient-specific table is supplied, simulation uses the same explicit provisional demo baseline for each person. Historical observations, configured starting values, true simulated physiology, and noisy sensor observations remain separate. No historical vital measurement is used to initialize a simulated patient.

## Cohort input and provenance

The imported cohort is recorded in `data/synthea_original/cohort_import_manifest.json`; the original archive's SHA-256 and exact resource IDs are recorded there. The ten registry records, ten matching monitoring profiles, and ten matching FHIR Patient resources were checked before the simulator was run.

- `data/synthea_original/patient_registry.json`
- `data/synthea_original/README.txt`
- `data/synthea_original/original_fhir/P001.json` through `P010.json`
- `data/monitoring_profiles/P001.json` through `P010.json`
- `data/simulation/cohort_validation.json`

The archive describes a preliminary cohort but does **not** contain evidence of the size of its candidate pool. The import manifest therefore records the claimed selection population as unverifiable instead of claiming verification from 333 source records. Historical prescription entries are not confirmed active discharge medications. A missing discharge disposition remains unknown. All supplied profiles use SpO2 Scale 1; no Scale 2 case is authorized. The FHIR bundles may contain events after the encounter end; the loader filters historical observations by patient and encounter-end time and does not expose the unfiltered bundles to the monitoring stream.

Import or re-verify the provided ZIP:

```powershell
Set-Location 'C:\Users\ldevi\Downloads\PS03_Five_Patient_Starter\ps03_five_patient_starter'
& .\.venv\Scripts\python.exe scripts\import_synthea_cohort.py --archive 'C:\Users\ldevi\Downloads\synthea_cohort_v2.zip'
& .\.venv\Scripts\python.exe scripts\validate_cohort.py
```

The importer refuses to silently replace different existing cohort files. Review them and use `--force` only when intentionally switching cohort versions.

## Architecture

```text
simulator/
  config.py                 Provisional baselines, noise, and run configuration
  patient_loader.py         Ten registry/profile pairs; encounter-end-filtered FHIR history
  bidmc_loader.py           Original Numerics loading and source-faithful replay
  bidmc_analysis.py         Five-record variability statistics and signal relationships
  physiological_engine.py   Simulated underlying (ground-truth) physiological trajectories
  scenario_engine.py        Configurable physiological and event schedules
  sensor_model.py           Noise, artifacts, dropouts, connectivity, observed values
  ground_truth.py           Separate truth/event record creation
  streaming_adapter.py      Observed-only async sink or configurable JSON POST
  simulation_manager.py     Independent states and concurrent patient streams
```

### Mode A: original BIDMC replay

The recordings in `data/bidmc_original/` are retained unchanged. HR is read from the inspected `HR` header, SpO2 from `SpO2`, and respiratory rate from `RESP`. Replay identifies the BIDMC recording, preserves the source seconds and source values, and does not attach a synthetic patient ID or make up temperature.

```powershell
& .\.venv\Scripts\python.exe scripts\analyze_bidmc.py
& .\.venv\Scripts\python.exe scripts\run_simulator.py --mode replay --recording all --speed 5 --output data\simulation\replay
```

`--recording all` (the default) replays all five files and writes a replay manifest. Use `--realtime` to pace replay at the selected speed; for example, `--speed 5 --realtime` plays five times faster than source time. Without `--realtime`, the exact source rows are written as fast as possible. Output includes `recording_id`, original `source_time_s`, HR/SpO2/RR, null temperature, and a temperature-unavailable label.

### BIDMC variability analysis

```powershell
& .\.venv\Scripts\python.exe scripts\analyze_bidmc.py
```

`data/bidmc_analysis/bidmc_variability.json` and `bidmc_recording_summary.csv` report per-recording and pooled mean, standard deviation, median, quartiles/IQR, missing percentage, one-second short-term differences, pairwise correlations, and sampling intervals. The configurable noise settings in `data/simulation/noise_config.json` use only BIDMC short-term variability, not absolute ICU values or patient-specific baselines. ICU variability is not an estimate of stable post-discharge physiology.

### Mode B: ten-patient virtual wearable simulation

Run 120 simulated seconds for all ten patients:

```powershell
& .\.venv\Scripts\python.exe scripts\run_simulator.py --mode simulation --scenario stable_recovery --duration 120 --sample-interval 1 --seed 20261112
```

Try physiology, sensor-fault, and combined scenarios:

```powershell
& .\.venv\Scripts\python.exe scripts\run_simulator.py --scenario gradual_deterioration --duration 120
& .\.venv\Scripts\python.exe scripts\run_simulator.py --scenario motion_artifacts --duration 120
& .\.venv\Scripts\python.exe scripts\run_simulator.py --scenario deterioration_with_sensor_failure --duration 120
```

Each run writes to `data/simulation/runs/<scenario>/` unless `--output` is supplied:

```text
observed/P001.csv ... P010.csv
ground_truth/P001_ground_truth.csv ... P010_ground_truth.csv
manifests/simulation_manifest.json
manifests/patient_initial_states.json
```

`patient_initial_states.json` explicitly separates the configured provisional baseline from encounter-end-filtered historical observations and historical condition/medication context. Every baseline entry says it is not patient-specific.

#### Scenarios and configuration

All patients can run all scenarios. Supported choices are:

- `stable_recovery`
- `gradual_deterioration`
- `sudden_deterioration`
- `temporary_fluctuations`
- `motion_artifacts`
- `probe_removal`
- `flatlined_sensor`
- `missing_measurements`
- `connectivity_loss`
- `deterioration_with_sensor_failure`
- `recovery_after_deterioration`
- `temporary_signal_degradation`

The common event timing and physiology changes are in `data/simulation/scenario_config.json`; sensor-fault magnitudes and background missingness are in `sensor_config.json`. `patient_scenario_presets.json` is an optional, explicitly synthetic mapping. Supply a patient-specific map without changing code:

```json
{
  "P001": "gradual_deterioration",
  "P002": "motion_artifacts",
  "P003": "deterioration_with_sensor_failure",
  "P004": "recovery_after_deterioration",
  "P005": "probe_removal"
}
```

Save that as `data\simulation\my_scenarios.json`, then run:

```powershell
& .\.venv\Scripts\python.exe scripts\run_simulator.py --scenario-map data\simulation\my_scenarios.json --duration 120
```

Change the provisional common initial values only in `data/simulation/initial_values.json`. An optional `patient_overrides` object can hold complete `hr_bpm`, `spo2_percent`, `rr_bpm`, and `temp_c` demo values keyed by P001–P010. Overrides remain explicitly provisional and **do not** become clinically verified patient baselines. Populate them with authorized patient-specific values only when that table is supplied.

### Physiological truth, sensor observations, and streaming

The physiological engine produces the true simulated values. The sensor model separately adds configurable measurement noise and optional motion artifacts, probe removal, flatline, intermittent missing samples, signal degradation, or connectivity loss. A missing reading is represented as null, never silently replaced with zero or a forward-filled value.

Observed CSVs contain only observed vital readings, UTC timestamp, patient ID, a unique reproducible event ID, signal quality, and sensor connection/status. They do **not** contain true values, scenario labels, future event timing, or ground-truth fault labels. Ground truth and event timing are written only under `ground_truth/`. Do not send ground-truth CSVs to a monitoring/prediction consumer.

For a continuous observed-only Python stream, `SimulationManager.iter_observed(continuous=True, stop_event=event)` yields one observation at each configured sampling interval; set the event or cancel the task to stop it. `StreamingAdapter` accepts an async sink or a configurable `--gateway-url`. No credentials are embedded. `data/simulation/gateway_observation.schema.json` is a temporary example schema, not an agreed gateway contract. Secure gateway integration cannot be considered complete until the actual endpoint, authentication requirements, and payload contract are supplied.

## Tests

```powershell
Set-Location 'C:\Users\ldevi\Downloads\PS03_Five_Patient_Starter\ps03_five_patient_starter'
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests cover ten-profile/FHIR consistency, independent and reproducible states, source-preserving BIDMC replay, historical/baseline separation, physiological changes, sensor faults and null handling, every mandatory scenario, concurrent streams, graceful stop, and ground-truth separation.

## Engineering audit

The engineering audit and separate reproducibility, concurrency, and provenance reports are in `reports/`. The scenario matrix maps each required scenario to its implementation, tests, and generated output. Isolated 10-patient scenario runs are retained in `data/simulation/runs/engineering_audit/`; these do not overwrite original recordings or earlier run directories.

Run tests from the project root:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The audit verifies deterministic outputs for a fixed seed, time-major cooperative stream progress, and explicit sensor-failure isolation. It does not establish clinical validity or a production-ready gateway integration.

## View the ten-patient data

Open [data/simulation/patient_viewer.html](./data/simulation/patient_viewer.html) in a browser to see all 10 patients, switch between the per-patient demo scenarios or a shared scenario, and view HR, SpO2, RR, or simulated temperature trends. Each patient card links to its observed CSV and separate simulation-truth CSV. The page also downloads one combined observed CSV for the selected scenario.

The default patient-specific combined export is [data/simulation/exports/ten_patients_patient_scenario_presets.csv](./data/simulation/exports/ten_patients_patient_scenario_presets.csv). To regenerate the viewer and export after regenerating simulation runs:

```powershell
& .\.venv\Scripts\python.exe scripts\build_data_viewer.py
```

The viewer is a standalone HTML file and does not require a web server or internet connection. It presents synthetic demo data only, not medical advice or a clinically validated dataset.

For a long-duration observed-only CSV ZIP, use `scripts/export_long_duration_dataset.py`. By default it creates 10 patients × 15 days × 10 samples/second (129.6 million rows). The CSV members are stored without compression to keep export time reasonable, so select a destination drive with at least 15 GB free:

```powershell
& .\.venv\Scripts\python.exe scripts\export_long_duration_dataset.py --output 'D:\PS03_wearable_10_patients_15_days_10pps.zip'
```

The export uses provisional common demo baselines and BIDMC-calibrated noise; it is synthetic and not clinically validated.

## Legacy five-patient starter files

The original five-patient demo files remain in `data/patients/P001`–`P005`, `data/profiles/patients.json`, `data/streams/`, and the earlier generator/validator scripts. They are retained for comparison and **are not** the authoritative revised ten-patient cohort. The simulator instead loads `data/synthea_original/patient_registry.json` and `data/monitoring_profiles/`. Do not combine cohort versions.

## Attribution and limitations

- BIDMC PPG and Respiration Dataset v1.0.0, PhysioNet: <https://physionet.org/content/bidmc/1.0.0/>. Numerics recording files, original headers, source README, license, checksums, and header map are retained under `data/bidmc_original/`.
- Pimentel MAF et al., “Towards a Robust Estimation of Respiratory Rate from Pulse Oximeters,” *IEEE Transactions on Biomedical Engineering*, 64(8), 1914–1923. <https://doi.org/10.1109/TBME.2016.2613124>.
- Synthea synthetic FHIR records are the user-provided ZIP. The archive and resource identities are recorded locally; the archive itself does not document the size of the candidate population, and neither the FHIR data nor this simulator has been clinically validated.

No scoring engine, diagnostic logic, or prediction model is included. The static HTML viewer is a simple way to view the generated demo data.
