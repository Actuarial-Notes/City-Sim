/* CitySim results — step 3 of the workflow.
 *
 * The dashboard from V1, plus the thing it was missing: a record of what the
 * model believed while producing these numbers. The "Assumptions used" card
 * reads the resolved set back from the stored scenarios, so it reports what the
 * solver actually ran with — including any value the API clamped on the way in
 * — rather than echoing the form that was submitted.
 */
"use strict";

(function () {
const C = window.CitySimCharts;
const $ = id => document.getElementById(id);

const MECH_LABELS = {
  sewer_backup: "Sewer backup (combined system)",
  basement_inundation: "Basement inundation (surface water)",
  overland_flooding: "Main-floor overland flooding",
};

class Results {
  constructor(state, onReplay) {
    this.state = state;
    this.onReplay = onReplay;
  }

  render() {
    const r = this.state.results;
    const st = r.stats, hh = st.per_household;

    $("tiles").innerHTML = [
      ["Expected annual damage / household", C.fmt$(hh.mean), `median ${C.fmt$(hh.median)}`],
      ["P95 / household", C.fmt$(hh.p95), `P99 ${C.fmt$(hh.p99)}`],
      ["CVaR₉₅ / household", C.fmt$(hh.cvar95), "mean of the worst 5% of years"],
      ["Expected annual damage — region", C.fmt$(st.total.mean),
       `worst run ${C.fmt$(st.total.max)}`],
      ["Ensemble", `${st.n_runs} runs`, `${st.elapsed_s}s · seed ${st.seed}`],
    ].map(([k, v, d]) =>
      `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div>` +
      `<div class="d">${d}</div></div>`).join("");

    C.histogram($("chart-hist"), r.runs.map(x => x.per_household_mean), {
      xLabel: "annual damage per household",
    });
    C.exceedance($("chart-exceed"), r.exceedance);

    const mech = Object.entries(st.mechanism_counts).sort((a, b) => b[1] - a[1]);
    C.bars($("chart-mech"), mech, {
      labels: MECH_LABELS, empty: "no damage anywhere in this ensemble",
      fmt: v => `${v.toLocaleString()} incidents`,
    });

    this._runPicker();
    this._assumptions();
  }

  _canReplay(run) {
    return !this.state.replayable || this.state.replayable.includes(run);
  }

  _runPicker() {
    const r = this.state.results;
    const chips = $("rep-chips");
    chips.innerHTML = "";
    const labels = {
      median: "a typical year", p90: "a bad year", p95: "a very bad year",
      p99: "a rare year", max: "the worst in the ensemble",
    };
    for (const [name, run] of Object.entries(r.stats.representative_runs)) {
      if (!this._canReplay(run)) continue;
      const b = document.createElement("button");
      b.className = "rep-chip";
      b.innerHTML = `<b>${labels[name] || name}</b><span>run #${run}</span>`;
      b.onclick = () => this.onReplay(run);
      chips.appendChild(b);
    }

    const idx = [...r.run_index].sort((a, b) => b.total_loss - a.total_loss).slice(0, 8);
    $("runs-table").innerHTML =
      `<tr><th>storm</th><th>return period</th><th>rain</th><th>duration</th>` +
      `<th>total damage</th><th>$/household</th></tr>` +
      idx.map(x =>
        `<tr class="${this._canReplay(x.run) ? "clickable" : ""}" data-run="${x.run}">` +
        `<td>run #${x.run}</td><td>${x.return_period.toFixed(0)} yr</td>` +
        `<td>${x.total_mm.toFixed(0)} mm</td><td>${x.duration_h.toFixed(1)} h</td>` +
        `<td>${C.fmt$(x.total_loss)}</td>` +
        `<td>${C.fmt$(x.per_household_mean)}</td></tr>`).join("");
    for (const tr of $("runs-table").querySelectorAll("tr.clickable"))
      tr.onclick = () => this.onReplay(parseInt(tr.dataset.run));
  }

  async _assumptions() {
    const mount = $("chart-assumptions");
    mount.innerHTML = `<div class="muted">loading…</div>`;
    let payload;
    try {
      payload = await API.resolvedAssumptions(this.state.jobId);
    } catch (e) {
      mount.innerHTML = `<div class="muted">assumption record unavailable</div>`;
      return;
    }
    const devs = [...(payload.deviations || []), ...(payload.twin_deviations || [])];
    const twin = this.state.twin;

    const provenance = twin && twin.sources
      ? `<ul class="prov">${twin.sources.map(s => `<li>${s}</li>`).join("")}</ul>` : "";

    mount.innerHTML =
      (devs.length
        ? `<p class="blurb">${devs.length} assumption${devs.length > 1 ? "s" : ""} ` +
          `moved from default for this ensemble.</p>` +
          `<table class="dev-table">` +
          `<tr><th>assumption</th><th>default</th><th>used</th></tr>` +
          devs.map(d => `<tr><td>${d.label}</td>` +
            `<td class="was">${C.fmtNum(d.default)}${d.unit || ""}</td>` +
            `<td class="now">${C.fmtNum(d.value)}${d.unit || ""}</td></tr>`).join("") +
          `</table>`
        : `<p class="blurb">Every assumption was left at its default. The numbers ` +
          `above are the model's out-of-the-box view.</p>`) +
      `<div class="muted" style="margin-top:10px">` +
      `${payload.n_runs} runs · seed ${payload.seed} — the ensemble is exactly ` +
      `reproducible from these.</div>` + provenance;
  }
}

window.CitySimResults = Results;
})();
