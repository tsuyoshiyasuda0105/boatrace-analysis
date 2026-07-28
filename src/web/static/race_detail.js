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

  const qualityTone = (mark) => ({
    "◎": "excellent",
    "○": "good",
    "〇": "good",
    "△": "fair",
    "×": "poor",
  }[mark] || "empty");

  const qualityCell = (mark, rank) => {
    const displayMark = mark || "-";
    return `
      <span class="motor-quality-mark is-${qualityTone(displayMark)}">
        <b>${esc(displayMark)}</b>
        ${rank == null ? "" : `<small>${esc(rank)}位</small>`}
      </span>`;
  };

  const laneColor = (boat) => ({
    1: "#f8fafc",
    2: "#111827",
    3: "#ff5c70",
    4: "#4b8bff",
    5: "#ffcf33",
    6: "#32d17d",
  }[Number(boat)] || "#94a3b8");

  const parseCurrentRaceRows = () => {
    const rows = Array.from(document.querySelectorAll(".preds-table tbody tr"));
    const boats = rows.map((row) => {
      const lane = row.querySelector(".lane");
      const boatNumber = Number(lane?.textContent?.trim() || 0);
      const name = row.querySelector(".racer-name")?.textContent?.trim() || "";
      const footGrades = row.querySelectorAll(".foot-grade");
      const getGrade = (idx) => {
        const el = footGrades[idx];
        if (!el) return { label: "-", score: null };
        const label = (el.textContent || "").trim() || "-";
        const title = el.getAttribute("title") || "";
        const match = title.match(/(-?\d+(?:\.\d+)?)/);
        return { label, score: match ? Number(match[1]) : null };
      };
      return {
        boatNumber,
        name,
        dash: getGrade(0),
        turn: getGrade(1),
        straight: getGrade(2),
      };
    }).filter((row) => row.boatNumber >= 1 && row.boatNumber <= 6);

    const assignRank = (key) => {
      const sortable = boats.filter((row) => row[key].score != null).sort((a, b) => b[key].score - a[key].score);
      let lastScore = null;
      let lastRank = 0;
      sortable.forEach((row, index) => {
        const score = row[key].score;
        if (lastScore == null || score !== lastScore) {
          lastRank = index + 1;
          lastScore = score;
        }
        row[key].rank = lastRank;
      });
    };
    assignRank("dash");
    assignRank("turn");
    assignRank("straight");

    boats.forEach((row) => {
      const scores = [row.dash.score, row.turn.score, row.straight.score].filter((v) => v != null);
      row.totalScore = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
    });
    const sortableTotal = boats.filter((row) => row.totalScore != null).sort((a, b) => b.totalScore - a.totalScore);
    let lastScore = null;
    let lastRank = 0;
    sortableTotal.forEach((row, index) => {
      if (lastScore == null || row.totalScore !== lastScore) {
        lastRank = index + 1;
        lastScore = row.totalScore;
      }
      row.totalRank = lastRank;
    });
    return boats;
  };

  const buildPositionRows = (historyData) => {
    const rows = Array.isArray(historyData?.position_rows) ? historyData.position_rows : [];
    if (rows.length) {
      const rankToScore = (rank) => rank == null ? null : Math.max(8, 100 - ((Number(rank) - 1) * 18));
      const boats = rows.map((row) => {
        const dash = { label: row.dash_mark || "-", score: rankToScore(row.dash_rank), rank: row.dash_rank ?? null };
        const turn = { label: row.turn_mark || "-", score: rankToScore(row.turn_rank), rank: row.turn_rank ?? null };
        const straight = { label: row.straight_mark || "-", score: rankToScore(row.straight_rank), rank: row.straight_rank ?? null };
        const scores = [dash.score, turn.score, straight.score].filter((v) => v != null);
        return {
          boatNumber: Number(row.boat_number || 0),
          name: row.racer_name || "",
          dash,
          turn,
          straight,
          totalScore: scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null,
        };
      }).filter((row) => row.boatNumber >= 1 && row.boatNumber <= 6);
      const sortableTotal = boats.filter((row) => row.totalScore != null).sort((a, b) => b.totalScore - a.totalScore);
      let lastScore = null;
      let lastRank = 0;
      sortableTotal.forEach((row, index) => {
        if (lastScore == null || row.totalScore !== lastScore) {
          lastRank = index + 1;
          lastScore = row.totalScore;
        }
        row.totalRank = lastRank;
      });
      return boats;
    }
    return parseCurrentRaceRows();
  };

  const renderPositionChart = (historyData, currentBoatNumber) => {
    const boats = buildPositionRows(historyData);
    if (!boats.length) return "";
    const currentBoat = boats.find((row) => Number(row.boatNumber) === Number(currentBoatNumber));
    const currentLabel = currentBoat
      ? `${currentBoat.dash.label} / ${currentBoat.turn.label} / ${currentBoat.straight.label}`
      : "-";
    const badgeLabel = currentBoat?.totalScore == null ? "計測待ち" : `総合 ${scoreLabel(currentBoat.totalScore)}`;
    return `
      <div class="motor-position-panel">
        <div class="motor-position-head">
          <div>
            <strong>6艇ポジション</strong>
            <span>${esc(currentLabel)}</span>
          </div>
          <div class="motor-profile-badges">
            <span class="motor-style-chip">${esc(badgeLabel)}</span>
          </div>
        </div>
        <div class="motor-position-board">
          <div class="motor-position-axis motor-position-axis-y">回り足 強い ↑</div>
          <div class="motor-position-axis motor-position-axis-x">出足 強い →</div>
          <div class="motor-position-note">円が大きいほど直線上位</div>
          ${boats.map((row) => {
            const x = row.dash.score == null ? 50 : Math.max(6, Math.min(94, row.dash.score));
            const y = row.turn.score == null ? 50 : Math.max(6, Math.min(94, 100 - row.turn.score));
            const size = row.straight.score == null ? 42 : Math.max(34, Math.min(72, 24 + (row.straight.score * 0.55)));
            const active = Number(row.boatNumber) === Number(currentBoatNumber) ? " is-active" : "";
            const ring = row.totalRank === 1 ? " is-top" : "";
            return `
              <div class="motor-position-bubble${active}${ring}" style="left:${x}%;top:${y}%;width:${size}px;height:${size}px;border-color:${laneColor(row.boatNumber)};">
                <span class="motor-position-bubble-core lane-${esc(row.boatNumber)}">${esc(row.boatNumber)}</span>
                <small>総合 ${esc(row.totalRank || "-")}位 / 6</small>
              </div>
            `;
          }).join("")}
        </div>
      </div>`;
  };

  const renderHistoryTable = (data) => {
    const current = data.current || {};
    const rows = [current, ...(data.history || [])].filter(Boolean);
    const body = rows.map((r, idx) => `
      <tr${idx === 0 ? ' class="motor-history-current-row"' : ""}>
        <td>${esc(r.race_date || "")}</td>
        <td>${esc(r.race_number || "")}R</td>
        <td><span class="lane lane-${esc(r.boat_number || "")} motor-mini-lane">${esc(r.boat_number || "")}</span></td>
        <td class="left">${esc(r.racer_name || "")}<div class="racer-meta">${esc(r.racer_number || "")}</div></td>
        <td>${esc(r.course_number ?? "-")}</td>
        <td>${num(r.exhibition_time)}</td>
        <td>${num(r.start_timing_exhibition)}</td>
        <td>${qualityCell(r.dash_mark, r.dash_rank)}</td>
        <td>${qualityCell(r.turn_mark, r.turn_rank)}</td>
        <td>${qualityCell(r.straight_mark, r.straight_rank)}</td>
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

  const renderHistoryOnly = (historyData) => `
    <div class="motor-inspector-stack">
      <div class="motor-history-panel">
        ${renderPositionChart(historyData, historyData.current?.boat_number)}
        ${renderHistoryTable(historyData)}
      </div>
      <div class="racer-detail-panel motor-inspector-racer" data-racer-detail-panel>
        <div class="motor-history-loading">選手情報を読み込み中...</div>
      </div>
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
        const historyData = await fetchHistory(raceId, boatNumber);
        inspectorBody.innerHTML = renderHistoryOnly(historyData);
        inspectorShell.scrollIntoView({ behavior: "smooth", block: "start" });
        fetchRacerDetail(raceId, boatNumber).then((racerData) => {
          const panel = inspectorBody.querySelector("[data-racer-detail-panel]");
          if (panel) panel.innerHTML = renderRacerDetail(racerData);
        }).catch((err) => {
          const panel = inspectorBody.querySelector("[data-racer-detail-panel]");
          if (panel) panel.innerHTML = `<div class="motor-history-empty">選手情報の取得に失敗しました: ${esc(err.message || "")}</div>`;
        });
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
        <div class="niche-body"><div class="niche-desc">${esc(sig.desc || "")}</div><div class="niche-recommend"><b>推奨:</b> ${esc(sig.recommend || "")}</div>${sig.warning ? `<div class="niche-warning">注意 ${esc(sig.warning)}</div>` : ""}</div>
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