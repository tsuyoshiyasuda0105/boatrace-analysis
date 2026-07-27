(() => {
  const shell = document.getElementById("race-signal-shell");
  const inspectorShell = document.getElementById("motor-inspector-shell");
  const inspectorBody = inspectorShell?.querySelector("[data-motor-inspector-body]");
  const staticVersion = shell?.dataset?.staticVersion || "v1";

  const esc = (value) => String(value ?? "").replace(/[&<>\"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
  const pct = (value) => value == null ? "-" : `${Number(value).toFixed(1)}%`;
  const num = (value, digits = 2) => value == null ? "-" : Number(value).toFixed(digits);
  const posClass = (value) => value ? `f-${Number(value)}` : "";
  const historyCache = new Map();
  const racerDetailCache = new Map();
  const scoreClass = (value) => {
    if (value == null) return "flat";
    const score = Number(value);
    if (score >= 60) return "up";
    if (score <= 44) return "down";
    return "flat";
  };
  const scoreLabel = (value) => value == null ? "-" : `${Number(value).toFixed(1)}pt`;
  const toneClass = (value) => {
    if (value === "up") return "up";
    if (value === "down") return "down";
    return "flat";
  };

  const qualityTone = (mark) => ({ "◎": "excellent", "○": "good", "△": "fair", "×": "poor" }[mark] || "empty");
  const qualityCell = (mark, value) => {
    const displayMark = mark || "-";
    return `
      <span class="motor-quality-mark is-${qualityTone(displayMark)}">
        <b>${esc(displayMark)}</b>
        ${value == null ? "" : `<small>${esc(num(value))}</small>`}
      </span>`;
  };

  const renderMotorProfile = (data) => {
    const current = data.current || {};
    const summary = data.summary || {};
    const profile = data.profile || {};
    const liveSignal = data.live_signal || {};
    const racerLift = data.racer_lift || {};
    const recentScores = Array.isArray(profile.recent_scores) ? profile.recent_scores : [];

    const metricCard = (label, value) => `
      <div class="motor-meter">
        <div class="motor-meter-head">
          <span>${esc(label)}</span>
          <b>${esc(scoreLabel(value))}</b>
        </div>
        <div class="motor-meter-track">
          <div class="motor-meter-fill" style="width:${Math.max(0, Math.min(100, Number(value || 0)))}%"></div>
        </div>
      </div>`;

    return `
      <div class="motor-profile">
        <div class="motor-profile-head">
          <div class="motor-profile-title">
            <strong>6艇ポジション</strong>
            <span>M${esc(current.motor_number ?? "-")} / モーター推移と当日変化</span>
          </div>
          <div class="motor-profile-badges">
            <span class="motor-condition-badge is-${esc(toneClass(profile.condition_tone))}">${esc(profile.condition_label || "標準")}</span>
            <span class="motor-style-chip">${esc(profile.style_label || "バランス型")}</span>
            <span class="motor-style-chip is-score">総合 ${esc(scoreLabel(profile.condition_score))}</span>
          </div>
        </div>
        <div class="motor-profile-meters">
          ${metricCard("出足", profile.dash_score)}
          ${metricCard("回り足", profile.turn_score)}
          ${metricCard("直線", profile.stretch_score)}
        </div>
        <div class="racer-lift-panel">
          <div class="racer-lift-head">
            <div class="racer-lift-title">
              <strong>当日変化</strong>
              <span>今節成績と上昇傾向、引き出し力をまとめます。</span>
            </div>
            <div class="motor-profile-badges">
              <span class="motor-condition-badge is-${esc(toneClass(liveSignal.trend_tone))}">上昇 ${liveSignal.trend_value == null ? "-" : (Number(liveSignal.trend_value) > 0 ? "+" : "") + num(liveSignal.trend_value)}</span>
              <span class="motor-condition-badge is-${esc(toneClass(racerLift.tone))}">引き出し ${racerLift.value == null ? "-" : (Number(racerLift.value) > 0 ? "+" : "") + num(racerLift.value)}</span>
            </div>
          </div>
          <div class="racer-lift-grid">
            <div><span>今節1着率</span><b>${pct(summary.win_rate)}</b></div>
            <div><span>今節2連率</span><b>${pct(summary.top2_rate)}</b></div>
            <div><span>当日上昇</span><b>${esc(scoreLabel(liveSignal.trend_score))}</b></div>
            <div><span>引き出し力</span><b>${esc(scoreLabel(racerLift.score))}</b></div>
          </div>
        </div>
        ${recentScores.length ? `
          <div class="motor-trend-strip">
            ${recentScores.map((row) => `
              <div class="motor-trend-card is-${esc(scoreClass(row.score))}">
                <span>${esc(row.race_date || "")} ${esc(row.race_number || "")}R</span>
                <b>${esc(scoreLabel(row.score))}</b>
                <small>${esc(row.label || "")}</small>
              </div>
            `).join("")}
          </div>
        ` : ""}
      </div>`;
  };

  const renderHistoryTable = (data) => {
    const current = data.current || {};
    const rows = [current, ...(data.history || [])].filter(Boolean);
    const body = rows.map((r, idx) => `
      <tr${idx === 0 ? ' class="motor-history-current-row"' : ""}>
        <td>${esc(r.race_date || "")}</td>
        <td>${esc(r.race_number || "")}R</td>
        <td><span class="lane lane-${esc(r.boat_number || "") } motor-mini-lane">${esc(r.boat_number || "")}</span></td>
        <td class="left">${esc(r.racer_name || "")}<div class="racer-meta">${esc(r.racer_number || "")}</div></td>
        <td>${esc(r.course_number ?? "-")}</td>
        <td>${num(r.exhibition_time)}</td>
        <td>${num(r.start_timing_exhibition)}</td>
        <td>${qualityCell(r.dash_mark, r.dash_time)}</td>
        <td>${qualityCell(r.turn_mark, r.turn_time)}</td>
        <td>${qualityCell(r.straight_mark, r.straight_time)}</td>
        <td>${r.finishing_position == null ? "-" : `<span class="finish-badge ${posClass(r.finishing_position)}">${esc(r.finishing_position)}着</span>`}</td>
      </tr>`).join("");
    return `
      <div class="motor-history-head">
        <strong>M${esc(current.motor_number ?? "-")} 現行期の直近履歴</strong>
        <span>${esc(current.stadium_name || "")} / ${esc(current.boat_number || "")}号艇 / 現行モーター期 ${esc(current.motor_cycle_start || "-")}以降</span>
      </div>
      <div class="motor-history-table-wrap">
        <table class="motor-history-table motor-history-table--compact">
          <thead>
            <tr>
              <th>日付</th><th>R</th><th>艇</th><th class="left">選手</th><th>進入</th><th>展示T</th><th>展示ST</th><th>出足</th><th>回り足</th><th>直線</th><th>結果</th>
            </tr>
          </thead>
          <tbody>${body || `<tr><td colspan="11" class="motor-history-empty">モーター履歴はまだありません。</td></tr>`}</tbody>
        </table>
      </div>`;
  };

  const courseStatsGrid = (title, rows) => `
    <div class="racer-course-block">
      <div class="racer-course-title">${esc(title)}</div>
      <div class="racer-course-grid">
        ${(rows || []).map((r) => `
          <div class="racer-course-card">
            <span>${esc(r.course)}C</span>
            <b>${pct(r.win_rate)}</b>
            <small>${esc(r.wins ?? 0)}/${esc(r.starts ?? 0)}走</small>
            <em>平均ST ${r.recent10_avg_st == null ? "-" : Number(r.recent10_avg_st).toFixed(3)}</em>
          </div>`).join("")}
      </div>
    </div>`;

  const renderRacerDetail = (data) => {
    const current = data.current || {};
    return `
      <div class="racer-detail-head">
        <div>
          <strong>${esc(current.racer_name || "-")}</strong>
          <span>${esc(current.racer_number || "-")} / ${esc(current.class_label || "-")} / ${esc(current.stadium_name || "-")}</span>
        </div>
      </div>
      ${courseStatsGrid(`${esc(current.stadium_name || "当地")} コース別`, data.venue_courses)}
      ${courseStatsGrid("全国 コース別", data.national_courses)}`;
  };

  const renderInspector = (historyData, racerData) => `
    <div class="motor-inspector-stack">
      <div class="motor-history-panel">
        ${renderMotorProfile(historyData)}
        ${renderHistoryTable(historyData)}
      </div>
      <div class="racer-detail-panel motor-inspector-racer">${renderRacerDetail(racerData)}</div>
    </div>`;

  const fetchHistory = (raceId, boatNumber) => {
    const key = `${raceId}:${boatNumber}`;
    if (!historyCache.has(key)) {
      historyCache.set(key, fetch(`/api/race/${encodeURIComponent(raceId)}/motor-history/${boatNumber}?v=${encodeURIComponent(staticVersion)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "force-cache",
      }).then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      }).catch((err) => {
        historyCache.delete(key);
        throw err;
      }));
    }
    return historyCache.get(key);
  };

  const fetchRacerDetail = (raceId, boatNumber) => {
    const key = `${raceId}:${boatNumber}`;
    if (!racerDetailCache.has(key)) {
      racerDetailCache.set(key, fetch(`/api/race/${encodeURIComponent(raceId)}/racer-detail/${boatNumber}?v=${encodeURIComponent(staticVersion)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "force-cache",
      }).then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      }).catch((err) => {
        racerDetailCache.delete(key);
        throw err;
      }));
    }
    return racerDetailCache.get(key);
  };

  document.querySelectorAll(".motor-history-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!inspectorShell || !inspectorBody) return;
      const boatNumber = button.dataset.boatNumber;
      const raceId = button.dataset.raceId;
      document.querySelectorAll(".motor-history-btn[aria-expanded='true']").forEach((el) => el.setAttribute("aria-expanded", "false"));
      button.setAttribute("aria-expanded", "true");
      inspectorShell.hidden = false;
      inspectorBody.innerHTML = '<div class="motor-history-loading">モーター履歴を読み込み中...</div>';
      try {
        const [historyData, racerData] = await Promise.all([
          fetchHistory(raceId, boatNumber),
          fetchRacerDetail(raceId, boatNumber),
        ]);
        inspectorBody.innerHTML = renderInspector(historyData, racerData);
        inspectorShell.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (err) {
        inspectorBody.innerHTML = `<div class="motor-history-empty">モーター履歴の取得に失敗しました: ${esc(err.message || "")}</div>`;
      }
    });
  });

  const renderMarketSignal = (signal) => {
    if (!signal) return "";
    const roiClass = Number(signal.expected_roi || 0) > 0 ? "ms-roi-positive" : "ms-roi-negative";
    const extras = Array.isArray(signal.extras) ? signal.extras : [];
    return `
      <div class="market-signal market-${esc(signal.tier || "neutral")}">
        <div class="ms-head">
          <span class="ms-title">${esc(signal.title || "")}</span>
          <span class="${roiClass}">想定ROI ${(Number(signal.expected_roi || 0) * 100).toFixed(1)}%</span>
        </div>
        <div class="ms-msg">${esc(signal.msg || "")}</div>
        ${extras.length ? `<div class="ms-extras">${extras.map((ex) => `
          <div class="ms-extra-item">
            <span class="ms-extra-label">${esc(ex.label || "")}</span>
            <span class="ms-extra-msg">${esc(ex.msg || "")}</span>
            ${ex.bet ? `<div class="ms-extra-bet"><strong>買い目:</strong> ${esc(ex.bet)}${ex.expected_roi != null ? `<span class="ms-extra-roi">(${(Number(ex.expected_roi) * 100).toFixed(1)}%)</span>` : ""}</div>` : ""}
          </div>`).join("")}</div>` : ""}
      </div>`;
  };

  const renderNicheSignals = (signals) => {
    if (!Array.isArray(signals) || !signals.length) return "";
    return `<div class="niche-signals">${signals.map((sig) => `
      <div class="niche-card niche-${esc(sig.level || "info")}">
        <div class="niche-head">
          <span class="niche-title">${esc(sig.title || "")}</span>
          <span class="niche-boat"><span class="lane lane-${esc(sig.boat_number)}">${esc(sig.boat_number)}</span><span class="niche-meta">${esc(sig.class_label || "")} / tilt ${Number(sig.tilt || 0) > 0 ? "+" : ""}${Number(sig.tilt || 0).toFixed(1)}</span></span>
        </div>
        <div class="niche-body"><div class="niche-desc">${esc(sig.desc || "")}</div><div class="niche-recommend"><b>評価:</b> ${esc(sig.recommend || "")}</div>${sig.warning ? `<div class="niche-warning">注意 ${esc(sig.warning)}</div>` : ""}</div>
      </div>`).join("")}</div>`;
  };

  const loadRaceSignals = async () => {
    if (!shell) return;
    const raceId = shell.dataset.raceId;
    if (!raceId) return;
    const loading = shell.querySelector("[data-race-signals-loading]");
    const marketContainer = document.getElementById("market-signal-container");
    const nicheContainer = document.getElementById("niche-signals-container");
    try {
      const res = await fetch(`/api/race/${encodeURIComponent(raceId)}/signals?v=${encodeURIComponent(staticVersion)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "force-cache",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (marketContainer) marketContainer.innerHTML = renderMarketSignal(data.market_signal);
      if (nicheContainer) nicheContainer.innerHTML = renderNicheSignals(data.niche_signals);
    } catch (err) {
      if (marketContainer && nicheContainer) {
        marketContainer.innerHTML = "";
        nicheContainer.innerHTML = `<div class="motor-history-empty">シグナル取得に失敗しました: ${esc(err.message || "")}</div>`;
      }
    } finally {
      if (loading) loading.remove();
    }
  };

  const scheduleRaceSignals = () => {
    if (!shell) return;
    const run = () => loadRaceSignals();
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(run, { timeout: 1200 });
    } else {
      window.setTimeout(run, 300);
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleRaceSignals, { once: true });
  } else {
    scheduleRaceSignals();
  }
})();
