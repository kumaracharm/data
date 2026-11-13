from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SIMULATION = ROOT / "data" / "simulation"
RUNS = SIMULATION / "runs"
PATIENT_IDS = [f"P{number:03d}" for number in range(1, 11)]
VITALS = {
    "observed_hr_bpm": ("Heart rate", "bpm"),
    "observed_spo2_percent": ("Oxygen saturation", "%"),
    "observed_rr_bpm": ("Respiratory rate", "breaths/min"),
    "observed_temp_c": ("Temperature", "°C"),
}
SCENARIO_LABELS = {
    "patient_scenario_presets": "Patient-specific demo scenarios",
    "stable_recovery": "Stable recovery",
    "gradual_deterioration": "Gradual deterioration",
    "sudden_deterioration": "Sudden deterioration",
    "temporary_fluctuations": "Temporary physiological fluctuations",
    "motion_artifacts": "Motion artifacts",
    "probe_removal": "Probe removal",
    "flatlined_sensor": "Flatlined sensor",
    "missing_measurements": "Missing measurements",
    "connectivity_loss": "Connectivity loss",
    "deterioration_with_sensor_failure": "Deterioration with sensor failure",
    "recovery_after_deterioration": "Recovery after deterioration",
}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    parsed: list[dict[str, Any]] = []
    for row in rows:
        parsed.append(
            {
                "event_id": row["event_id"],
                "patient_id": row["patient_id"],
                "timestamp": row["timestamp"],
                **{
                    key: (float(row[key]) if row[key] else None)
                    for key in VITALS
                },
                "signal_quality": float(row["signal_quality"])
                if row["signal_quality"]
                else None,
                "sensor_status": row["sensor_status"],
                "sensor_connected": row["sensor_connected"].lower() == "true",
            }
        )
    return parsed


def build_payload() -> dict[str, Any]:
    scenarios: dict[str, Any] = {}
    scenario_dirs = [path for path in RUNS.iterdir() if path.is_dir()]
    audit_dir = RUNS / "engineering_audit"
    if audit_dir.is_dir():
        scenario_dirs.extend(path for path in audit_dir.iterdir() if path.is_dir())

    for directory in sorted(scenario_dirs):
        scenario = directory.name
        manifest_path = directory / "manifests" / "simulation_manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        patient_map: dict[str, Any] = {}
        for patient_id in PATIENT_IDS:
            observed_path = directory / "observed" / f"{patient_id}.csv"
            truth_path = directory / "ground_truth" / f"{patient_id}_ground_truth.csv"
            if not observed_path.is_file() or not truth_path.is_file():
                raise FileNotFoundError(
                    f"{scenario} is missing observed or ground-truth CSV for {patient_id}"
                )
            patient_map[patient_id] = {
                "scenario": manifest["scenarios"][patient_id],
                "rows": _read_rows(observed_path),
                "observed_csv": observed_path.relative_to(SIMULATION).as_posix(),
                "ground_truth_csv": truth_path.relative_to(SIMULATION).as_posix(),
            }
        scenarios[scenario] = patient_map

    if "patient_scenario_presets" not in scenarios:
        raise FileNotFoundError(
            "Run the ten-patient patient_scenario_presets simulation before building the viewer."
        )
    return {
        "patient_ids": PATIENT_IDS,
        "scenarios": scenarios,
        "scenario_labels": {
            name: SCENARIO_LABELS.get(name, name.replace("_", " ").title())
            for name in scenarios
        },
        "vitals": {
            key: {"label": label, "unit": unit}
            for key, (label, unit) in VITALS.items()
        },
    }


def build_combined_csv(payload: dict[str, Any], scenario: str) -> str:
    output = [
        [
            "scenario",
            "scenario_name",
            "event_id",
            "patient_id",
            "timestamp",
            *VITALS,
            "signal_quality",
            "sensor_status",
            "sensor_connected",
        ]
    ]
    for patient_id in payload["patient_ids"]:
        patient = payload["scenarios"][scenario][patient_id]
        for row in patient["rows"]:
            output.append(
                [
                    scenario,
                    patient["scenario"],
                    row["event_id"],
                    patient_id,
                    row["timestamp"],
                    *[row[key] for key in VITALS],
                    row["signal_quality"],
                    row["sensor_status"],
                    row["sensor_connected"],
                ]
            )
    from io import StringIO

    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerows(output)
    return stream.getvalue()


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ten-patient wearable demo data</title>
<style>
:root{color-scheme:light;--ink:#172b4d;--muted:#5e6c84;--line:#dfe4ec;--blue:#2457c5;--bg:#f4f7fb;--card:#fff;--green:#16794b;--red:#b42318}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{background:#10264d;color:#fff;padding:26px max(20px,calc((100vw - 1200px)/2))}header h1{margin:0;font-size:clamp(24px,4vw,36px)}header p{margin:8px 0 0;color:#d9e4fa;max-width:850px}
main{max-width:1200px;margin:22px auto;padding:0 18px 40px}.notice{background:#fff8e6;border:1px solid #f1d58a;border-radius:10px;padding:12px 16px;margin-bottom:18px}
.controls,.summary,.patient{background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:0 2px 8px #1831530a}
.controls{display:flex;flex-wrap:wrap;gap:14px;align-items:end;padding:16px;margin-bottom:16px}
label{display:grid;gap:5px;font-weight:650}select,button,.download{font:inherit;border:1px solid #b8c2d1;border-radius:8px;padding:9px 12px;background:white;color:var(--ink)}
button,.download{cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:6px;font-weight:650}
button.primary{background:var(--blue);border-color:var(--blue);color:#fff}.download:hover,button:hover{filter:brightness(.96)}
.summary{padding:14px 18px;margin-bottom:16px;color:var(--muted)}.summary strong{color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}
.patient{padding:15px;min-width:0}.patient-head{display:flex;justify-content:space-between;align-items:center;gap:12px}.patient h2{margin:0;font-size:20px}.scenario{font-size:12px;border-radius:99px;background:#edf2ff;color:#244a9b;padding:5px 9px}
.status{font-size:12px;font-weight:700;color:var(--green)}.status.bad{color:var(--red)}
.vitals{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:13px 0}
.vital{border:1px solid #edf0f5;border-radius:8px;padding:8px 10px}.vital span{display:block;color:var(--muted);font-size:12px}.vital strong{font-size:19px}.vital small{color:var(--muted)}
.chart-title{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;margin-top:8px}.chart{width:100%;height:66px;display:block;background:linear-gradient(180deg,#f8faff,#fff);border-radius:6px}.chart .gridline{stroke:#e8edf5;stroke-width:1}.chart .line{fill:none;stroke:var(--blue);stroke-width:2}.chart .null{fill:#fff;stroke:var(--red);stroke-width:1.5}
.links{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.links .download{font-size:12px;padding:6px 9px}.links a.truth{color:#53657f}
.foot{margin-top:20px;color:var(--muted);font-size:13px}
@media(max-width:500px){.grid{grid-template-columns:1fr}.controls>*{width:100%}.controls label{width:100%}}
</style>
</head>
<body>
<header><h1>Ten-patient wearable demo</h1><p>A simple visual view of the 10 simulated patient streams. Choose a scenario and vital sign to see the last reading and its 120-second trend. CSV downloads are available for each patient or for all 10 together.</p></header>
<main>
<div class="notice"><strong>Research demo only.</strong> These are synthetic simulator outputs, not real-time patient readings, medical advice, or a clinically validated dataset. Starting values are provisional demo values. Temperature is simulated.</div>
<section class="controls" aria-label="View controls">
<label>Scenario<select id="scenario"></select></label>
<label>Trend to display<select id="vital"></select></label>
<button class="primary" id="download-all">Download combined CSV (10 patients)</button>
<a class="download" href="exports/ten_patients_patient_scenario_presets.csv" download>Download ready-made 10-patient CSV</a>
</section>
<section class="summary" id="summary" aria-live="polite"></section>
<section class="grid" id="patients" aria-label="Patient data cards"></section>
<p class="foot">Observed CSVs contain sensor observations only. “Simulation truth CSV” is a separate synthetic reference stream and is not observed sensor data. Blank chart gaps mean missing measurements; readings are never replaced with zero.</p>
</main>
<script>
const DATA = __DATA__;
const scenarioSelect=document.getElementById("scenario");
const vitalSelect=document.getElementById("vital");
for(const [key,label] of Object.entries(DATA.scenario_labels)){const o=document.createElement("option");o.value=key;o.textContent=label;scenarioSelect.append(o)}
scenarioSelect.value="patient_scenario_presets";
for(const [key,v] of Object.entries(DATA.vitals)){const o=document.createElement("option");o.value=key;o.textContent=`${v.label} (${v.unit})`;vitalSelect.append(o)}
vitalSelect.value="observed_hr_bpm";
function display(value,digits=1){return value===null||!Number.isFinite(value)?"Missing":`${Number(value).toFixed(digits)}`}
function pathFor(rows,key,width=620,height=66){
 const values=rows.map(r=>r[key]).filter(v=>v!==null&&Number.isFinite(v));
 if(!values.length)return {d:"",circles:[],min:null,max:null};
 let min=Math.min(...values),max=Math.max(...values);if(min===max){min-=1;max+=1}
 const pad=4,stepX=(width-2*pad)/Math.max(1,rows.length-1);
 const points=[];let segment=[];
 rows.forEach((row,i)=>{const value=row[key];if(value===null||!Number.isFinite(value)){if(segment.length)points.push(segment.join(" "));segment=[];return}const x=pad+i*stepX,y=height-pad-(value-min)/(max-min)*(height-2*pad);segment.push(`${segment.length?"L":"M"}${x.toFixed(1)},${y.toFixed(1)}`)});
 if(segment.length)points.push(segment.join(" "));
 const circles=rows.map((r,i)=>r[key]===null?`<circle class="null" cx="${(pad+i*stepX).toFixed(1)}" cy="${height/2}" r="2"/>`:"").join("");
 return {d:points.join(" "),circles,min,max};
}
function makeCard(patientId,patient,scenarioKey,vitalKey){
 const rows=patient.rows,latest=rows.at(-1),vital=DATA.vitals[vitalKey],chart=pathFor(rows,vitalKey);
 const card=document.createElement("article");card.className="patient";
 const heading=document.createElement("div");heading.className="patient-head";
 const title=document.createElement("h2");title.textContent=patientId;heading.append(title);
 const pill=document.createElement("span");pill.className="scenario";pill.textContent=DATA.scenario_labels[patient.scenario]||patient.scenario;heading.append(pill);card.append(heading);
 const status=document.createElement("div");status.className=`status${latest.sensor_connected?"":" bad"}`;status.textContent=`${latest.sensor_status} · ${latest.sensor_connected?"Connected":"Not connected"}`;card.append(status);
 const vitals=document.createElement("div");vitals.className="vitals";
 for(const [key,def] of Object.entries(DATA.vitals)){const box=document.createElement("div");box.className="vital";const name=document.createElement("span");name.textContent=def.label;const value=document.createElement("strong");value.textContent=display(latest[key],key==="observed_temp_c"?1:1);const unit=document.createElement("small");unit.textContent=` ${def.unit}`;box.append(name,value,unit);vitals.append(box)}
 card.append(vitals);
 const chartTitle=document.createElement("div");chartTitle.className="chart-title";const left=document.createElement("span");left.textContent=`${vital.label} trend`;const right=document.createElement("span");right.textContent=chart.min===null?"No readings":`${display(chart.min)}–${display(chart.max)} ${vital.unit}`;chartTitle.append(left,right);card.append(chartTitle);
 const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");svg.setAttribute("class","chart");svg.setAttribute("viewBox","0 0 620 66");svg.setAttribute("role","img");svg.setAttribute("aria-label",`${patientId} ${vital.label} trend over 120 seconds`);
 for(const y of [16,33,50]){const line=document.createElementNS(svg.namespaceURI,"line");line.setAttribute("x1","0");line.setAttribute("x2","620");line.setAttribute("y1",String(y));line.setAttribute("y2",String(y));line.setAttribute("class","gridline");svg.append(line)}
 const curve=document.createElementNS(svg.namespaceURI,"path");curve.setAttribute("class","line");curve.setAttribute("d",chart.d);svg.append(curve);const dots=document.createElementNS(svg.namespaceURI,"g");dots.innerHTML=chart.circles;svg.append(dots);card.append(svg);
 const time=document.createElement("div");time.className="chart-title";time.innerHTML="<span>Start</span><span>120 seconds</span>";card.append(time);
 const links=document.createElement("div");links.className="links";
 for(const [label,path,klass] of [["Observed CSV",patient.observed_csv,""],["Simulation truth CSV",patient.ground_truth_csv,"truth"]]){const a=document.createElement("a");a.className=`download ${klass}`;a.href=path;a.download="";a.textContent=label;links.append(a)}
 card.append(links);return card
}
function render(){
 const key=scenarioSelect.value,vital=vitalSelect.value,patients=DATA.scenarios[key];
 const container=document.getElementById("patients");container.replaceChildren();
 let readings=0,disconnected=0,missing=0;
 for(const patientId of DATA.patient_ids){const patient=patients[patientId];if(!patient)continue;readings+=patient.rows.length;disconnected+=patient.rows.filter(r=>!r.sensor_connected).length;missing+=patient.rows.reduce((n,r)=>n+(["observed_hr_bpm","observed_spo2_percent","observed_rr_bpm","observed_temp_c"].filter(k=>r[k]===null).length),0);container.append(makeCard(patientId,patient,key,vital))}
 document.getElementById("summary").innerHTML=`<strong>${DATA.scenario_labels[key]}</strong> · <strong>${DATA.patient_ids.length} patients</strong> · ${readings.toLocaleString()} observed samples · ${missing.toLocaleString()} missing channel readings · ${disconnected.toLocaleString()} disconnected samples`;
}
scenarioSelect.addEventListener("change",render);vitalSelect.addEventListener("change",render);
document.getElementById("download-all").addEventListener("click",()=>{
 const scenario=scenarioSelect.value,rows=[["scenario","scenario_name","event_id","patient_id","timestamp",...Object.keys(DATA.vitals),"signal_quality","sensor_status","sensor_connected"]];
 for(const patientId of DATA.patient_ids){const patient=DATA.scenarios[scenario][patientId];for(const row of patient.rows)rows.push([scenario,patient.scenario,row.event_id,patientId,row.timestamp,...Object.keys(DATA.vitals).map(k=>row[k]??""),row.signal_quality??"",row.sensor_status,row.sensor_connected])}
 const csv=rows.map(row=>row.map(value=>{const text=String(value??"");return /[",\r\n]/.test(text)?`"${text.replaceAll('"','""')}"`:text}).join(",")).join("\r\n");
 const link=document.createElement("a");link.href=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));link.download=`ten_patients_${scenario}_observed.csv`;document.body.append(link);link.click();setTimeout(()=>{URL.revokeObjectURL(link.href);link.remove()},1000)
});
render();
</script>
</body>
</html>
"""


def build_viewer(output: Path, payload: dict[str, Any]) -> None:
    embedded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        HTML_TEMPLATE.replace("__DATA__", embedded),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a standalone HTML viewer and combined CSV for the ten-patient simulator."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=SIMULATION / "patient_viewer.html",
        help="HTML viewer output path.",
    )
    args = parser.parse_args()
    payload = build_payload()
    build_viewer(args.output, payload)

    # Also build the unified HealthGuard Application HTML in reports
    try:
        from healthguard.ui import get_html_app
        reports_dir = ROOT / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        hg_app_path = reports_dir / "healthguard_app.html"
        hg_app_path.write_text(get_html_app(), encoding="utf-8")
        print(f"HealthGuard Unified Application: {hg_app_path}")
    except Exception as err:
        print(f"Note: HealthGuard UI export skipped: {err}")

    export_dir = SIMULATION / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    combined_path = export_dir / "ten_patients_patient_scenario_presets.csv"
    combined_path.write_text(
        build_combined_csv(payload, "patient_scenario_presets"),
        encoding="utf-8",
        newline="",
    )
    print(f"HTML viewer: {args.output}")
    print(f"Combined 10-patient CSV: {combined_path}")
    print(f"Patients: {len(payload['patient_ids'])}; scenarios: {len(payload['scenarios'])}")


if __name__ == "__main__":
    main()
