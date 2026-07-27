(() => {
  const shell = document.getElementById("race-signal-shell");
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
  const qualityTone = (mark) => ({
    "◎": "excellent",
    "〇": "good",
    "△": "fair",
    "×": "poor",
  }[mark] || "empty");
  const qualityCell = (mark, value) => {
    const displayMark = mark || "-";
    const timeText = value == null ? "" : num(value);
    return `
      <span class="motor-quality-mark is-${qualityTone(displayMark)}" title="${timeText}">
        <b>${esc(displayMark)}</b>
        ${timeText ? `<small>${esc(timeText)}</small>` : ""}
      </span>
    `;
  };
  const historyCache = new Map();
  const racerDetailCache = new Map();

  const renderHistory = (data) => {
    const current = data.current || {};
    const rows = data.history || [];
    const title = `M${esc(current.motor_number ?? "-")} 現行期の直近履歴`;
    const cycleNote = current.motor_cycle_start
      ? `現行モーター期: ${esc(current.motor_cycle_start)}以降`
      : "現行モーター期";

    const resultHtml = (finishingPosition, kimarite) => {
      if (finishingPosition == null && !kimarite) return "-";
      return `
        <div class="motor-result-cell">
          ${finishingPosition != null ? `<span class="finish-badge ${posClass(finishingPosition)}">${esc(finishingPosition)}着</span>` : ""}
          ${kimarite ? `<div class="racer-meta">${esc(kimarite)}</div>` : ""}
        </div>
      `;
    };

    const currentRowHtml = `
      <tr class="motor-history-current-row">
        <td>${esc(current.race_date || "")}</td>
        <td>${esc(current.race_number || "")}R</td>
        <td><span class="lane lane-${esc(current.boat_number)} motor-mini-lane">${esc(current.boat_number)}</span></td>
        <td class="left">${esc(current.racer_name || "")}<div class="racer-meta">${esc(current.racer_number || "")}</div></td>
        <td>-</td>
        <td>${num(current.exhibition_time)}</td>
        <td>${num(current.start_timing_exhibition)}</td>
        <td>${qualityCell(current.dash_mark, current.dash_time)}</td>
        <td>${qualityCell(current.turn_mark, current.turn_time)}</td>
        <td>${qualityCell(current.straight_mark, current.straight_time)}</td>
        <td>-</td>
      </tr>
    `;

    const body = rows.map((r) => `
      <tr>
        <td>${esc(r.race_date)}</td>
        <td>${esc(r.race_number)}R</td>
        <td><span class="lane lane-${esc(r.boat_number)} motor-mini-lane">${esc(r.boat_number)}</span></td>
        <td class="left">
          ${esc(r.racer_name)}
          <div class="racer-meta">${esc(r.racer_number)}</div>
        </td>
        <td>${esc(r.course_number ?? "-")}</td>
        <td>${num(r.exhibition_time)}</td>
        <td>${num(r.start_timing_exhibition)}</td>
        <td>${qualityCell(r.dash_mark, r.dash_time)}</td>
        <td>${qualityCell(r.turn_mark, r.turn_time)}</td>
        <td>${qualityCell(r.straight_mark, r.straight_time)}</td>
        <td>${resultHtml(r.finishing_position, r.kimarite)}</td>
      </tr>
    `).join("");

    return `
      <div class="motor-history-head">
        <strong>${title}</strong>
        <span>${esc(current.stadium_name)} / ${esc(current.boat_number)}号艇 ${esc(current.racer_name)} / ${cycleNote}</span>
      </div>
      <div class="motor-history-table-wrap">
        <table class="motor-history-table">
          <thead>
            <tr>
              <th>日付</th>
              <th>R</th>
              <th>艇</th>
              <th class="left">選手</th>
              <th>進入</th>
              <th>展示T</th>
              <th>展示ST</th>
              <th>出足</th>
              <th>回り足</th>
              <th>直線</th>
              <th>結果</th>
            </tr>
          </thead>
          <tbody>
            ${currentRowHtml}
            ${body || `<tr><td colspan="11" class="motor-history-empty">モーター履歴はまだありません。</td></tr>`}
          </tbody>
        </table>
      </div>
    `;
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
            <em>直近10ST ${r.recent10_avg_st == null ? "-" : Number(r.recent10_avg_st).toFixed(3)} (${esc(r.recent10_st_n ?? 0)})</em>
          </div>
        `).join("")}
      </div>
    </div>
  `;

  const renderRacerDetail = (data) => {
    const current = data.current || {};
    return `
      <div class="racer-detail-head">
        <div>
          <strong>${esc(current.racer_name || "-")}</strong>
          <span>No. ${esc(current.racer_number || "-")} / ${esc(current.class_label || "-")} / ${esc(current.stadium_name || "-")}</span>
        </div>
        <small>${esc(data.note || "")}</small>
      </div>
      ${courseStatsGrid(`${esc(current.stadium_name || "当地")} 1C-6C 1着率`, data.venue_courses)}
      ${courseStatsGrid("全国 1C-6C 1着率", data.national_courses)}
    `;
  };

  const fetchHistory = (button) => {
    const boatNumber = button.dataset.boatNumber;
    const raceId = button.dataset.raceId;
    const key = `${raceId}:${boatNumber}`;
    if (!historyCache.has(key)) {
      historyCache.set(key, fetch(`/api/race/${encodeURIComponent(raceId)}/motor-history/${boatNumber}?v=${encodeURIComponent(staticVersion)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "no-store",
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

  const fetchRacerDetail = (button) => {
    const boatNumber = button.dataset.boatNumber;
    const raceId = button.dataset.raceId;
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

  document.querySelectorAll(".racer-detail-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const boatNumber = button.dataset.boatNumber;
      const row = document.getElementById(`racer-detail-row-${boatNumber}`);
      const panel = row?.querySelector("[data-racer-detail-panel]");
      if (!row || !panel) return;
      if (!row.hidden && panel.dataset.loaded === "1") {
        row.hidden = true;
        button.setAttribute("aria-expanded", "false");
        return;
      }
      row.hidden = false;
      button.setAttribute("aria-expanded", "true");
      if (panel.dataset.loaded === "1") return;
      panel.innerHTML = '<div class="motor-history-loading">選手詳細を読み込み中...</div>';
      try {
        const data = await fetchRacerDetail(button);
        panel.innerHTML = renderRacerDetail(data);
        panel.dataset.loaded = "1";
      } catch (err) {
        panel.innerHTML = `<div class="motor-history-empty">選手詳細の取得に失敗しました: ${esc(err.message || "")}</div>`;
      }
    });
  });

  document.querySelectorAll(".motor-history-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const boatNumber = button.dataset.boatNumber;
      const row = document.getElementById(`motor-history-row-${boatNumber}`);
      const panel = row?.querySelector("[data-motor-history-panel]");
      if (!row || !panel) return;
      if (!row.hidden && panel.dataset.loaded === "1") {
        row.hidden = true;
        button.setAttribute("aria-expanded", "false");
        return;
      }
      row.hidden = false;
      button.setAttribute("aria-expanded", "true");
      if (panel.dataset.loaded === "1") return;
      panel.innerHTML = '<div class="motor-history-loading">モーター履歴を読み込み中...</div>';
      try {
        const data = await fetchHistory(button);
        panel.innerHTML = renderHistory(data);
        panel.dataset.loaded = "1";
      } catch (err) {
        panel.innerHTML = `<div class="motor-history-empty">モーター履歴の取得に失敗しました: ${esc(err.message || "")}</div>`;
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
        ${extras.length ? `
          <div class="ms-extras">
            ${extras.map((ex) => `
              <div class="ms-extra-item">
                <span class="ms-extra-label">${esc(ex.label || "")}</span>
                <span class="ms-extra-msg">${esc(ex.msg || "")}</span>
                ${ex.bet ? `
                  <div class="ms-extra-bet">
                    <strong>買い目:</strong> ${esc(ex.bet)}
                    ${ex.expected_roi != null ? `<span class="ms-extra-roi">(期待 ${ (Number(ex.expected_roi) * 100).toFixed(1)}%)</span>` : ""}
                  </div>
                ` : ""}
              </div>
            `).join("")}
          </div>
        ` : ""}
      </div>
    `;
  };

  const renderNicheSignals = (signals) => {
    if (!Array.isArray(signals) || !signals.length) return "";
    return `
      <div class="niche-signals">
        ${signals.map((sig) => `
          <div class="niche-card niche-${esc(sig.level || "info")}">
            <div class="niche-head">
              <span class="niche-title">${esc(sig.title || "")}</span>
              <span class="niche-boat">
                <span class="lane lane-${esc(sig.boat_number)}">${esc(sig.boat_number)}</span>
                <span class="niche-meta">${esc(sig.class_label || "")} / tilt ${Number(sig.tilt || 0) > 0 ? "+" : ""}${Number(sig.tilt || 0).toFixed(1)}</span>
              </span>
            </div>
            <div class="niche-body">
              <div class="niche-desc">${esc(sig.desc || "")}</div>
              <div class="niche-recommend"><b>評価:</b> ${esc(sig.recommend || "")}</div>
              ${sig.warning ? `<div class="niche-warning">注意 ${esc(sig.warning)}</div>` : ""}
            </div>
          </div>
        `).join("")}
      </div>
    `;
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
    const run = () => {
      loadRaceSignals();
    };
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(run, { timeout: 1500 });
    } else {
      window.setTimeout(run, 450);
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleRaceSignals, { once: true });
  } else {
    scheduleRaceSignals();
  }
})();
