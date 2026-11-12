/* Rental360 dashboard — vanilla JS, no build step.
   Every number displayed comes from an API response; nothing is invented. */
(function () {
  "use strict";

  var API = "/api";

  /* ---------- Formatting ---------- */
  var usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  var usdCents = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var intFmt = new Intl.NumberFormat("en-US");
  var pctFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

  function money(v) { return v == null ? "—" : usd.format(v); }
  function moneyExact(v) { return v == null ? "—" : usdCents.format(v); }
  function num(v) { return v == null ? "—" : intFmt.format(v); }
  function pct(v) { return v == null ? "—" : pctFmt.format(v) + "%"; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtDate(s) {
    if (!s) return "—";
    var d = new Date(s);
    if (isNaN(d.getTime())) return esc(s);
    return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  }
  function fmtPeriod(s) {
    if (!s) return "—";
    var d = new Date(s.length === 7 ? s + "-01" : s);
    if (isNaN(d.getTime())) return esc(s);
    return d.toLocaleDateString("en-US", { year: "numeric", month: "short" });
  }

  /* ---------- Fetch wrapper ---------- */
  var apiFailed = false;
  function api(path, options) {
    return fetch(API + path, options).then(function (res) {
      if (!res.ok) throw new Error("API " + res.status + " on " + path);
      return res.json();
    });
  }
  function showError(msg) {
    apiFailed = true;
    var banner = document.getElementById("error-banner");
    if (msg) document.getElementById("error-detail").textContent = msg;
    banner.hidden = false;
  }

  /* ---------- Chart helpers ---------- */
  var charts = {};
  function chartReady() { return typeof window.Chart !== "undefined"; }
  function baseOptions(extra) {
    var o = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#0f172a",
          padding: 10,
          titleFont: { weight: "700" },
          callbacks: {}
        }
      },
      scales: {
        x: { grid: { color: "#eef2f7" }, ticks: { color: "#64748b", font: { size: 12 } } },
        y: { grid: { color: "#eef2f7" }, ticks: { color: "#64748b", font: { size: 12 } }, beginAtZero: true }
      }
    };
    if (extra) Object.assign(o, extra);
    return o;
  }
  function destroyChart(key) {
    if (charts[key]) { charts[key].destroy(); charts[key] = null; }
  }
  function moneyTicks(v) {
    if (Math.abs(v) >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
    if (Math.abs(v) >= 1e3) return "$" + Math.round(v / 1e3) + "k";
    return "$" + v;
  }

  /* ---------- KPIs ---------- */
  function loadKpis() {
    return api("/kpis").then(function (k) {
      var set = function (id, val) {
        var el = document.getElementById(id);
        el.textContent = val;
        el.classList.remove("skeleton");
      };
      set("kpi-revenue", money(k.total_revenue));
      set("kpi-rentals", num(k.total_rentals));
      set("kpi-utilization", pct(k.avg_utilization_pct));
      set("kpi-idle", num(k.idle_equipment_count));
      set("kpi-top-branch", k.top_branch && k.top_branch.name ? k.top_branch.name : "—");
      document.getElementById("kpi-revenue-sub").textContent =
        num(k.equipment_count) + " pieces of equipment · " + num(k.customer_count) + " customers · " + num(k.branch_count) + " branches";
      document.getElementById("kpi-rentals-sub").textContent =
        k.period && k.period.start ? "From " + fmtDate(k.period.start) + " to " + fmtDate(k.period.end) : "";
      document.getElementById("kpi-utilization-sub").textContent = "Across all equipment in service";
      document.getElementById("kpi-idle-sub").textContent =
        "Utilization under 10% · of " + num(k.equipment_count) + " total";
      document.getElementById("kpi-top-branch-sub").textContent =
        k.top_branch && k.top_branch.revenue != null ? money(k.top_branch.revenue) + " in revenue" : "";
      if (k.period && k.period.start) {
        document.getElementById("period-label").textContent =
          fmtDate(k.period.start) + " – " + fmtDate(k.period.end);
      }
    });
  }

  /* ---------- Revenue trend ---------- */
  var monthlySeries = [];
  var granularity = "month";

  function quarterLabel(period) {
    // period like "2025-01" -> "Q1 2025"
    var m = /^(\d{4})-(\d{2})/.exec(period || "");
    if (!m) return period;
    var q = Math.floor((parseInt(m[2], 10) - 1) / 3) + 1;
    return "Q" + q + " " + m[1];
  }
  function aggregateQuarterly(rows) {
    var map = {};
    rows.forEach(function (r) {
      var q = quarterLabel(r.period);
      if (!map[q]) map[q] = { period: q, revenue: 0, rentals: 0 };
      map[q].revenue += r.revenue || 0;
      map[q].rentals += r.rentals || 0;
    });
    return Object.keys(map).map(function (k) { return map[k]; });
  }
  function renderRevenue() {
    var rows = granularity === "quarter" ? aggregateQuarterly(monthlySeries) : monthlySeries;
    document.getElementById("revenue-empty").hidden = rows.length > 0;
    document.getElementById("revenue-chart-wrap").style.display = rows.length ? "" : "none";
    if (!rows.length || !chartReady()) return;
    destroyChart("revenue");
    var ctx = document.getElementById("revenue-chart").getContext("2d");
    charts.revenue = new Chart(ctx, {
      type: "line",
      data: {
        labels: rows.map(function (r) { return granularity === "quarter" ? r.period : fmtPeriod(r.period); }),
        datasets: [{
          label: "Revenue",
          data: rows.map(function (r) { return r.revenue; }),
          borderColor: "#1d4ed8",
          backgroundColor: "rgba(29,78,216,0.08)",
          fill: true,
          tension: 0.3,
          pointRadius: 3,
          pointBackgroundColor: "#1d4ed8",
          borderWidth: 2
        }]
      },
      options: baseOptions({
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (c) { return "Revenue: " + money(c.parsed.y); },
              afterLabel: function (c) {
                var row = rows[c.dataIndex];
                return row && row.rentals != null ? "Rentals: " + num(row.rentals) : "";
              }
            }
          }
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: "#64748b" } },
          y: { ticks: { color: "#64748b", callback: moneyTicks }, beginAtZero: true }
        }
      })
    });
  }
  function loadRevenue() {
    return api("/revenue-timeseries?granularity=month").then(function (rows) {
      monthlySeries = Array.isArray(rows) ? rows : [];
      document.getElementById("revenue-loading").hidden = true;
      renderRevenue();
    });
  }
  function setGranularity(g) {
    granularity = g;
    document.getElementById("gran-month").setAttribute("aria-pressed", g === "month" ? "true" : "false");
    document.getElementById("gran-quarter").setAttribute("aria-pressed", g === "quarter" ? "true" : "false");
    renderRevenue();
  }

  /* ---------- Branch performance ---------- */
  function loadBranches() {
    return api("/branch-performance").then(function (rows) {
      document.getElementById("branch-loading").hidden = true;
      var list = (Array.isArray(rows) ? rows : []).slice().sort(function (a, b) { return (b.total_revenue || 0) - (a.total_revenue || 0); });
      document.getElementById("branch-empty").hidden = list.length > 0;
      document.getElementById("branch-chart-wrap").style.display = list.length ? "" : "none";
      var tbody = document.querySelector("#branch-table tbody");
      tbody.innerHTML = list.map(function (b) {
        return "<tr><td><strong>" + esc(b.branch_name) + "</strong><span class='sub'>" + esc(b.city) + ", " + esc(b.state) + "</span></td>" +
          "<td class='num'>" + num(b.rental_count) + "</td>" +
          "<td class='num'>" + money(b.total_revenue) + "</td>" +
          "<td class='num'>" + money(b.avg_rental_value) + "</td>" +
          "<td class='num'>" + (b.avg_rental_days != null ? pctFmt.format(b.avg_rental_days) : "—") + "</td></tr>";
      }).join("");
      if (!list.length || !chartReady()) return;
      destroyChart("branch");
      var ctx = document.getElementById("branch-chart").getContext("2d");
      charts.branch = new Chart(ctx, {
        type: "bar",
        data: {
          labels: list.map(function (b) { return b.branch_name; }),
          datasets: [{
            label: "Revenue",
            data: list.map(function (b) { return b.total_revenue; }),
            backgroundColor: "#1d4ed8",
            borderRadius: 6,
            barThickness: 18
          }]
        },
        options: baseOptions({
          indexAxis: "y",
          plugins: {
            tooltip: { callbacks: { label: function (c) { return "Revenue: " + money(c.parsed.x); } } }
          },
          scales: {
            x: { ticks: { color: "#64748b", callback: moneyTicks }, beginAtZero: true },
            y: { grid: { display: false }, ticks: { color: "#0f172a", font: { size: 12 } } }
          }
        })
      });
    });
  }

  /* ---------- Forecast ---------- */
  var forecastBranch = "";
  function loadForecast() {
    var q = forecastBranch ? "?branch_id=" + encodeURIComponent(forecastBranch) : "";
    return api("/forecast" + q).then(function (res) {
      document.getElementById("forecast-loading").hidden = true;
      var note = document.getElementById("forecast-note");
      var noteOk = document.getElementById("forecast-note-ok");
      note.hidden = true; noteOk.hidden = true;
      if (!res || res.available === false) {
        document.getElementById("forecast-chart-wrap").style.display = "none";
        document.getElementById("forecast-empty").hidden = false;
        document.getElementById("forecast-empty-text").textContent =
          (res && res.message) ? res.message : "The demand forecast model is not available right now.";
        note.textContent = "Forecast unavailable — " + ((res && res.message) || "the model has not produced predictions for this selection yet.");
        note.hidden = false;
        destroyChart("forecast");
        return;
      }
      var rows = Array.isArray(res.forecasts) ? res.forecasts : [];
      if (!rows.length) {
        document.getElementById("forecast-chart-wrap").style.display = "none";
        document.getElementById("forecast-empty").hidden = false;
        destroyChart("forecast");
        return;
      }
      // Latest forecast month only, summed by category across branches.
      var latest = rows.map(function (r) { return r.year_month; }).sort().pop();
      var byCat = {};
      rows.filter(function (r) { return r.year_month === latest; }).forEach(function (r) {
        var cat = r.category || "Uncategorized";
        byCat[cat] = (byCat[cat] || 0) + (r.predicted_rentals || 0);
      });
      var cats = Object.keys(byCat).sort(function (a, b) { return byCat[b] - byCat[a]; });
      document.getElementById("forecast-empty").hidden = true;
      document.getElementById("forecast-chart-wrap").style.display = "";
      document.getElementById("forecast-subtitle").textContent =
        "Predicted rentals for " + fmtPeriod(latest) + ", by category" + (forecastBranch ? " (selected branch)" : " (all branches)");
      noteOk.textContent = "Forecast for " + fmtPeriod(latest) + " · predicted rentals by category. Predictions come from the trained demand model; actuals may differ.";
      noteOk.hidden = false;
      if (!chartReady()) return;
      destroyChart("forecast");
      var ctx = document.getElementById("forecast-chart").getContext("2d");
      charts.forecast = new Chart(ctx, {
        type: "bar",
        data: {
          labels: cats,
          datasets: [{
            label: "Predicted rentals",
            data: cats.map(function (c) { return Math.round(byCat[c]); }),
            backgroundColor: "#0e7490",
            borderRadius: 6,
            barThickness: 22
          }]
        },
        options: baseOptions({
          plugins: {
            tooltip: { callbacks: { label: function (c) { return "Predicted rentals: " + num(c.parsed.y); } } }
          },
          scales: {
            x: { grid: { display: false }, ticks: { color: "#0f172a", font: { size: 12 } } },
            y: { ticks: { color: "#64748b", precision: 0 }, beginAtZero: true }
          }
        })
      });
    });
  }

  /* ---------- Top customers ---------- */
  function loadCustomers() {
    return api("/top-customers?limit=10").then(function (rows) {
      document.getElementById("customers-loading").hidden = true;
      var list = Array.isArray(rows) ? rows : [];
      document.getElementById("customers-empty").hidden = list.length > 0;
      var tbody = document.querySelector("#customers-table tbody");
      tbody.innerHTML = list.map(function (c, i) {
        return "<tr><td class='num'>" + (i + 1) + "</td>" +
          "<td><strong>" + esc(c.customer_name) + "</strong></td>" +
          "<td>" + esc(c.customer_type) + "</td>" +
          "<td>" + esc(c.city) + ", " + esc(c.state) + "</td>" +
          "<td class='num'>" + num(c.rental_count) + "</td>" +
          "<td class='num'>" + money(c.lifetime_revenue) + "</td>" +
          "<td class='num'>" + money(c.avg_rental_value) + "</td>" +
          "<td>" + fmtDate(c.last_rental_date) + "</td></tr>";
      }).join("");
    });
  }

  /* ---------- Equipment ---------- */
  var eqCategory = "", eqBranch = "";
  function fillSelect(el, items, valueKey, labelFn) {
    var keep = el.value;
    el.querySelectorAll("option:not([value=''])").forEach(function (o) { o.remove(); });
    items.forEach(function (it) {
      var opt = document.createElement("option");
      opt.value = it[valueKey];
      opt.textContent = labelFn(it);
      el.appendChild(opt);
    });
    el.value = keep;
  }
  function loadLookups() {
    var branchesP = api("/branches").then(function (rows) {
      var list = Array.isArray(rows) ? rows : [];
      fillSelect(document.getElementById("eq-branch"), list, "branch_id",
        function (b) { return b.branch_name + " — " + b.city + ", " + b.state; });
      fillSelect(document.getElementById("forecast-branch"), list, "branch_id",
        function (b) { return b.branch_name + " — " + b.city + ", " + b.state; });
    }).catch(function () { /* lookups are best-effort; tables still load */ });
    var catsP = api("/categories").then(function (rows) {
      var list = Array.isArray(rows) ? rows : [];
      var sel = document.getElementById("eq-category");
      list.forEach(function (c) {
        var name = typeof c === "string" ? c : c.category;
        var opt = document.createElement("option");
        opt.value = name; opt.textContent = name;
        sel.appendChild(opt);
      });
    }).catch(function () { /* best-effort */ });
    return Promise.allSettled([branchesP, catsP]);
  }
  function utilClass(u) { return u < 10 ? "idle" : (u < 25 ? "low" : ""); }
  function utilPill(u) {
    if (u < 10) return "<span class='pill pill-idle'>Idle</span>";
    if (u < 25) return "<span class='pill pill-low'>Low</span>";
    return "<span class='pill pill-ok'>Active</span>";
  }
  function loadEquipment() {
    var params = new URLSearchParams({ limit: "100" });
    if (eqCategory) params.set("category", eqCategory);
    if (eqBranch) params.set("branch_id", eqBranch);
    return api("/equipment?" + params.toString()).then(function (rows) {
      document.getElementById("equipment-loading").hidden = true;
      var list = Array.isArray(rows) ? rows : [];
      document.getElementById("equipment-empty").hidden = list.length > 0;
      document.getElementById("eq-count").textContent =
        list.length ? "Showing " + num(list.length) + " item" + (list.length === 1 ? "" : "s") : "";
      var tbody = document.querySelector("#equipment-table tbody");
      tbody.innerHTML = list.map(function (e) {
        var u = e.utilization_pct == null ? 0 : e.utilization_pct;
        var idle = u < 10;
        return "<tr class='" + (idle ? "row-idle" : "") + "'>" +
          "<td><strong>" + esc(e.equipment_name) + "</strong><span class='sub'>ID " + esc(e.equipment_id) + " · " + num(e.days_in_service) + " days in service</span></td>" +
          "<td>" + esc(e.category) + "</td>" +
          "<td>" + esc(e.branch_id) + "</td>" +
          "<td class='num'>" + moneyExact(e.daily_rate) + "</td>" +
          "<td>" + utilPill(u) + "<span class='sub'>" + esc(e.status) + "</span></td>" +
          "<td class='num'>" + num(e.rented_days) + "</td>" +
          "<td class='num'>" + num(e.rental_count) + "</td>" +
          "<td class='util-cell'><strong>" + pct(u) + "</strong><div class='util-bar'><div class='util-fill " + utilClass(u) + "' style='width:" + Math.min(100, Math.max(0, u)) + "%'></div></div></td></tr>";
      }).join("");
    });
  }

  /* ---------- AI Assistant ---------- */
  var chatLog = document.getElementById("chat-log");
  var chatForm = document.getElementById("chat-form");
  var chatInput = document.getElementById("chat-input");
  var chatSend = document.getElementById("chat-send");

  function addMsg(role, html) {
    var div = document.createElement("div");
    div.className = "chat-msg " + role;
    div.innerHTML = html;
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
    return div;
  }
  function evidenceTable(rows) {
    if (!rows || !rows.length) return "<p>No evidence rows returned.</p>";
    var cols = Object.keys(rows[0]);
    var shown = rows.slice(0, 10);
    var html = "<div class='evidence-table table-scroll'><table><thead><tr>" +
      cols.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      shown.map(function (r) {
        return "<tr>" + cols.map(function (c) {
          var v = r[c];
          return "<td>" + esc(typeof v === "number" ? (Number.isInteger(v) ? intFmt.format(v) : v) : v) + "</td>";
        }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>";
    if (rows.length > shown.length) html += "<p>Showing " + shown.length + " of " + rows.length + " evidence rows.</p>";
    return html;
  }
  function askAssistant(question) {
    var q = (question || "").trim();
    if (!q) return;
    addMsg("user", esc(q));
    chatSend.disabled = true;
    chatInput.disabled = true;
    var pending = addMsg("assistant", "<span class='spinner' aria-hidden='true'></span> Thinking…");
    api("/assistant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q })
    }).then(function (res) {
      var html = "";
      if (res.intent) html += "<span class='intent'>" + esc(res.intent) + "</span>";
      html += esc(res.answer || "No answer returned.");
      if (res.sql || (res.rows && res.rows.length)) {
        html += "<details><summary>How this was calculated</summary>";
        if (res.sql) html += "<pre>" + esc(res.sql) + "</pre>";
        html += evidenceTable(res.rows);
        html += "</details>";
      }
      pending.innerHTML = html;
    }).catch(function (err) {
      pending.innerHTML = "<span class='error-text'>Sorry — I couldn’t reach the assistant just now. Please try again.</span>";
      console.error(err);
    }).finally(function () {
      chatSend.disabled = false;
      chatInput.disabled = false;
      chatInput.focus();
      chatLog.scrollTop = chatLog.scrollHeight;
    });
  }
  chatForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = chatInput.value;
    chatInput.value = "";
    askAssistant(q);
  });
  document.getElementById("suggestion-chips").addEventListener("click", function (e) {
    if (e.target && e.target.classList.contains("chip")) askAssistant(e.target.textContent);
  });

  /* ---------- Wiring ---------- */
  document.getElementById("gran-month").addEventListener("click", function () { setGranularity("month"); });
  document.getElementById("gran-quarter").addEventListener("click", function () { setGranularity("quarter"); });
  document.getElementById("eq-category").addEventListener("change", function (e) {
    eqCategory = e.target.value;
    document.getElementById("equipment-loading").hidden = false;
    loadEquipment().catch(function (err) { showError("Equipment data failed to load."); console.error(err); });
  });
  document.getElementById("eq-branch").addEventListener("change", function (e) {
    eqBranch = e.target.value;
    document.getElementById("equipment-loading").hidden = false;
    loadEquipment().catch(function (err) { showError("Equipment data failed to load."); console.error(err); });
  });
  document.getElementById("forecast-branch").addEventListener("change", function (e) {
    forecastBranch = e.target.value;
    document.getElementById("forecast-loading").hidden = false;
    loadForecast().catch(function (err) { showError("Forecast data failed to load."); console.error(err); });
  });
  document.getElementById("retry-btn").addEventListener("click", function () { window.location.reload(); });

  /* ---------- Boot ---------- */
  function boot() {
    // Health check first: if the API is down, show the banner up front.
    api("/health").then(function (h) {
      if (h && h.synthetic_data) {
        document.getElementById("api-status").hidden = false;
      }
    }).catch(function (err) {
      showError("The dashboard couldn’t load live data. Check that the backend is running, then try again.");
      console.error(err);
    });
    var jobs = [loadKpis(), loadRevenue(), loadBranches(), loadForecast(), loadCustomers(), loadEquipment(), loadLookups()];
    Promise.allSettled(jobs).then(function (results) {
      var failed = results.filter(function (r) { return r.status === "rejected"; });
      if (failed.length && !apiFailed) {
        showError("Some dashboard sections failed to load. The data shown may be incomplete — try reloading.");
      }
      // Un-skeleton any KPI that never resolved so the page never looks stuck.
      document.querySelectorAll(".kpi-value.skeleton").forEach(function (el) {
        el.classList.remove("skeleton");
        if (el.textContent.trim() === "—" || el.textContent.trim() === "") el.textContent = "—";
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
