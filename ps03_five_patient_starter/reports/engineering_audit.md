# PS-03 Virtual Wearable Simulator Engineering Audit

Audit completed: 2026-11-12. This is an engineering audit of the supplied prototype, not clinical validation.

## Result summary

- **Scenario coverage:** all 11 required scenarios execute. Isolated outputs were generated for each scenario at `data/simulation/runs/engineering_audit/<scenario>/`, with 10 patients and 120 samples per patient.
- **Reproducibility:** repeated runs with the same seed produced byte-identical CSVs and manifests; a different seed changed observed and true trajectories. Observation event IDs are deterministic UUIDv5 values from patient ID, seed, scenario, and sample index.
- **Concurrency:** found and fixed non-realtime worker starvation. Each worker now yields after publishing a sample; tests verify time-major progress on the common simulated UTC clock. Connectivity loss for one patient did not interrupt a peer.
- **Data integrity:** all 20 BIDMC files used for the five recordings match the shipped SHA256 manifest. The Synthea archive hash and extracted patient bundles match their recorded provenance.
- **FHIR provenance:** 10 registry/profile/Patient-resource mappings and 17,794 Observation subject and encounter references were checked; no mismatches or unresolved references were found.
- **Ground-truth separation:** observed output files contain observed telemetry only. The adapter now rejects all fields in the current ground-truth schema.
- **Final validation:** `python -m unittest discover -s tests -v` — **25 tests passed**; `compileall` passed.

See [scenario_coverage_matrix.csv](./scenario_coverage_matrix.csv), [reproducibility_test_report.json](./reproducibility_test_report.json), [concurrency_test_report.json](./concurrency_test_report.json), and [provenance_validation_report.json](./provenance_validation_report.json) for the recorded evidence.

## Scenario coverage

| Required scenario | Main behavior | Generated audit run |
|---|---|---|
| Stable recovery | No injected event or physiological delta | `data/simulation/runs/engineering_audit/stable_recovery/` |
| Gradual deterioration | Configured deltas ramp with event progress | `data/simulation/runs/engineering_audit/gradual_deterioration/` |
| Sudden deterioration | Configured deltas apply at onset | `data/simulation/runs/engineering_audit/sudden_deterioration/` |
| Temporary physiological fluctuations | Sinusoidal HR/RR changes and SpO2 dips | `data/simulation/runs/engineering_audit/temporary_fluctuations/` |
| Motion artifacts | Sensor-only artifact values and degraded quality | `data/simulation/runs/engineering_audit/motion_artifacts/` |
| Probe removal | HR/SpO2 null; no zero fill | `data/simulation/runs/engineering_audit/probe_removal/` |
| Flatlined sensor | Captured sensor values remain constant during fault | `data/simulation/runs/engineering_audit/flatlined_sensor/` |
| Missing measurements | Configured per-channel missingness, represented as null | `data/simulation/runs/engineering_audit/missing_measurements/` |
| Complete connectivity loss | All readings null and `sensor_connected=false` | `data/simulation/runs/engineering_audit/connectivity_loss/` |
| Genuine deterioration with sensor failure | True physiology changes; subsequent probe removal affects observed HR/SpO2 | `data/simulation/runs/engineering_audit/deterioration_with_sensor_failure/` |
| Recovery after deterioration | Deltas rise to a peak and return to zero | `data/simulation/runs/engineering_audit/recovery_after_deterioration/` |

Implementation is in `simulator/scenario_engine.py`, `simulator/physiological_engine.py`, and `simulator/sensor_model.py`. The matrix links each scenario to its tests and generated output. Every run has ten observed and ten ground-truth CSVs, 120 rows each. Active timestamps fell within event onset/end windows; no observed file included truth columns. No zero-valued replacement was found for missing samples.

`temporary_signal_degradation` is additionally supported but is outside the required set.

## Reproducibility

`SimulationManager` seeds a separate physiology and sensor generator for each patient. Identical configurations (seed 99, 10 patients, 4 samples) yielded byte-identical 22-file output sets, including manifests. Seed 100 yielded different full observed HR and ground-truth trajectories for P001. The first observation ID matched UUIDv5 of `P001:99:stable_recovery:0` in the configured namespace.

This proves reproducibility for the exercised configurations and same runtime/dependency versions; byte identity across different library versions or platforms is not claimed.

## Concurrency

Before the fix, non-realtime `asyncio.Queue.put()` calls did not yield with an unbounded queue. A patient worker could enqueue every sample before another patient advanced. `SimulationManager.iter_observed` now explicitly yields after each sample in non-realtime mode. Tests verify three simulated timestamps, each with P001–P010 exactly once, in time-major order.

With P001 in `connectivity_loss` and P002 in `stable_recovery`, P001 emitted five rows (four disconnected after onset), and P002 emitted all five connected rows. These timestamps share a deterministic UTC axis; they are not a real-time barrier. Unexpected worker exceptions remain fail-fast and cancel the overall iterator.

## Physiological and sensor models

- HR, SpO2, RR, and temperature have separate noise channels and AR(1) noise state in `PhysiologicalEngine`; scenario deltas affect truth only.
- Scenario changes are configurable; final values are clipped to HR 25–240 bpm, SpO2 50–100%, RR 3–70 bpm, and temperature 30–43 C. These output bounds are fixed in code, not independently configurable.
- Sensor corruption is applied in `SensorModel` after physiological truth is calculated. Motion, probe removal, flatline, intermittent missingness, and connectivity loss do not mutate ground truth.
- Nulls are serialized as missing CSV cells/JSON nulls; no forward-fill or zero substitution occurs.
- Temperature remains a synthetic channel based on provisional configuration, not measured BIDMC data or a verified patient-specific baseline.

## Provenance and data integrity

The five original BIDMC Numerics files each contain 481 rows and match `data/bidmc_original/SHA256SUMS.txt`; all 20 preserved source files used for the five recordings also matched. The supplied Synthea archive SHA256 is `ceeff587497e504b6f36db15e9c183b18fe0de406c8b644701a98ccd9432bef3`; it matches the import manifest, and extracted original FHIR bundles are byte-identical to their archive entries.

All ten registry-to-profile-to-FHIR Patient IDs are distinct and match. All 17,794 Observation subject references resolve to the bundle's patient; every observation encounter reference resolves to an Encounter whose subject is that patient. The profile's selected Encounter also resolves. Historical observations remain historical and do not initialize simulated baselines.

Historical observations are filtered by patient and by the selected encounter end time, but the loader retains observations associated with the patient's other earlier encounters as longitudinal history. The supplied archive does not prove selection from 333 candidates; that claim remains unverifiable.

## Gateway readiness

`StreamingAdapter.forward` publishes one observation at a time; each has a timestamp and deterministic event ID. The adapter supports an async sink or configured HTTP JSON POST with a timeout. It does not aggregate or window observations.

| Capability | Result |
|---|---|
| Individual observations | Supported |
| Timestamp and event ID | Included in each observed record |
| Ground-truth rejection | Supported for all current truth fields |
| Reconnection/retry | Not implemented |
| Cancellation | Async iteration can stop/cancel; cancelling `asyncio.to_thread` does not stop an in-flight urllib worker thread, which can run until its timeout |
| Transmission failure | HTTP/network errors propagate; no retry or reconnection policy |
| Agreed endpoint/auth/payload contract | Not available; production readiness cannot be claimed |

The adapter intentionally has no aggregation or 5–15-second windowing; that belongs to the gateway.

## Bugs fixed and recommendations

### Fixed in this audit

1. **Non-realtime task starvation:** patient workers could run patient-major instead of progressing together on each tick. Added a cooperative yield and a time-major concurrency test.
2. **Incomplete truth-field denylist:** `scenario_event_active` and `injected_physiological_changes_json` could pass through the adapter. Both are now rejected and covered by tests.
3. **Coverage gaps:** added tests for required scenario effects, sensor-only fault behavior, null handling, physiological bounds, seed-dependent trajectories and file reproducibility, FHIR reference resolution, and peer-stream isolation during sensor failure.

### Remaining limitations/recommendations

1. Add gateway-specific retry/reconnection and cancellation behavior only after endpoint, authentication, and delivery requirements are agreed.
2. If history is intended to be selected-encounter-only rather than longitudinal, filter Observation encounter references to the profile's selected encounter.
3. Unexpected worker exceptions currently abort all streams (while surfacing the error). Define a per-patient exception-isolation policy if runtime/transport failures must be isolated separately from modeled sensor faults.
4. Physiological output bounds are fixed constants. Make them configurable only if requirements specify justified ranges.
5. Candidate-pool size remains unverified. No clinical validation is claimed.

## Test execution and observed failures

Final command from the project root:

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result: **25 tests passed**. `python -m compileall -q simulator tests scripts` also passed.

An initial test invocation from the enclosing Downloads directory failed to import `simulator` in six test modules because the project root was not the working directory. Running from the project root resolved it. A temporary `NameError` in a newly added time-major assertion was caught by focused tests and fixed before the final run. All 11 scenario output validations completed with zero event-window, row-count, or truth-leakage errors.

This prototype is a research/hackathon simulator only; it is not clinically validated and is not a diagnostic system.
