(() => {
  const shell = document.getElementById("race-signal-shell");
  const inspectorShell = document.getElementById("motor-inspector-shell");
  const inspectorBody = inspectorShell?.querySelector("[data-motor-inspector-body]");
  const staticVersion = shell?.dataset?.staticVersion || "v1";
  const startPredictionShell = document.querySelector("[data-start-prediction]");
  const startPredictionDetails = document.querySelector("[data-start-prediction-details]");
  let startPredictionScriptPromise;

  const ensureStartPredictionScript = () => {
    if (!startPredictionShell) return Promise.resolve(false);
    if (window.__boatraceStartPredictionLoaded) return Promise.resolve(true);
    if (startPredictionScriptPromise) return startPredictionScriptPromise;
    const src = startPredictionShell.dataset.startPredictionSrc;
    if (!src) return Promise.resolve(false);
    startPredictionScriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.defer = true;
      script.dataset.startPredictionScript = "1";
      script.addEventListener("load", () => resolve(true), { once: true });
      script.addEventListener("error", (event) => {
        startPredictionScriptPromise = undefined;
        reject(event);
      }, { once: true });
      document.body.appendChild(script);
    });
    return startPredictionScriptPromise;
  };

  const setupStartPredictionLazyLoad = () => {
    if (!startPredictionDetails || !startPredictionShell) return;
    const loadOnDemand = () => ensureStartPredictionScript().catch(() => {});
    if (startPredictionDetails.open) {
      loadOnDemand();
      return;
    }
    startPredictionDetails.addEventListener("toggle", () => {
      if (startPredictionDetails.open) loadOnDemand();
    });
  };

  const normalizeRaceDetailLayout = () => {
    document.querySelectorAll(".top-pick, .market-signal").forEach((node) => node.remove());
    document.querySelectorAll("[data-race-signals-loading]").forEach((node) => node.remove());
    const marketContainer = document.getElementById("market-signal-container");
    if (marketContainer) marketContainer.remove();
    const legacySignalShell = document.getElementById("race-signal-shell");
    if (legacySignalShell && !legacySignalShell.textContent.trim()) legacySignalShell.remove();
    if (
      inspectorShell
      && startPredictionShell
      && inspectorShell.nextElementSibling !== startPredictionShell
    ) {
      startPredictionShell.parentNode.insertBefore(inspectorShell, startPredictionShell);
    }
    document.querySelectorAll("details.collapsible-section").forEach((details) => {
      const summaryText = details.querySelector("summary")?.textContent || "";
      if (summaryText.includes("6艇詳細")) details.open = true;
    });
  };

  normalizeRaceDetailLayout();
  setupStartPredictionLazyLoad();

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
  const classLabelFromNumber = (value) => ({
    1: "A1",
    2: "A2",
    3: "B1",
    4: "B2",
  }[Number(value)] || "-");
  const classBadgeTone = (label) => {
    if (label === "A1") return "is-a1";
    if (label === "A2") return "is-a2";
    if (label === "B1") return "is-b1";
    if (label === "B2") return "is-b2";
    return "";
  };
  const racerClassBadge = (row) => {
    const label = row?.class_label || classLabelFromNumber(row?.class_number);
    if (!label || label === "-") return "";
    return `<span class="racer-class-badge ${classBadgeTone(label)}">${esc(label)}</span>`;
  };
  const historyCache = new Map();
  const racerDetailCache = new Map();
  let activeRaceId = "";
  let activeMotorBoatNumber = "";
  let motorHistoryRequestId = 0;
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
    const hasMeaningfulRanks = rows.filter((row) => (
      row?.dash_rank != null || row?.turn_rank != null || row?.straight_rank != null
    )).length >= 3;
    if (rows.length && hasMeaningfulRanks) {
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
      if (boats.length >= 3 && boats.some((row) => row.totalScore != null)) {
        return boats;
      }
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
      <div class="motor-position-panel" style="margin:0 0 12px;padding:12px;border:1px solid rgba(0,212,255,.16);border-radius:8px;background:rgba(6,18,34,.72);">
        <div class="motor-position-head" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px;">
          <div style="display:grid;gap:4px;">
            <strong>6艇ポジション</strong>
            <span>${esc(currentLabel)}</span>
          </div>
          <div class="motor-profile-badges">
            <span class="motor-style-chip">${esc(badgeLabel)}</span>
          </div>
        </div>
        <div class="motor-position-board" style="position:relative;height:420px;overflow:hidden;border-radius:10px;border:1px solid rgba(0,212,255,.12);background:linear-gradient(rgba(0,212,255,.10), rgba(0,212,255,.10)) 0 20%/100% 1px no-repeat,linear-gradient(rgba(0,212,255,.10), rgba(0,212,255,.10)) 0 40%/100% 1px no-repeat,linear-gradient(rgba(0,212,255,.10), rgba(0,212,255,.10)) 0 60%/100% 1px no-repeat,linear-gradient(rgba(0,212,255,.10), rgba(0,212,255,.10)) 0 80%/100% 1px no-repeat,linear-gradient(90deg, rgba(0,212,255,.10), rgba(0,212,255,.10)) 20% 0/1px 100% no-repeat,linear-gradient(90deg, rgba(0,212,255,.10), rgba(0,212,255,.10)) 40% 0/1px 100% no-repeat,linear-gradient(90deg, rgba(0,212,255,.10), rgba(0,212,255,.10)) 60% 0/1px 100% no-repeat,linear-gradient(90deg, rgba(0,212,255,.10), rgba(0,212,255,.10)) 80% 0/1px 100% no-repeat,linear-gradient(135deg, rgba(13,22,34,.98), rgba(5,22,31,.98));">
          <div class="motor-position-axis motor-position-axis-y" style="position:absolute;left:14px;top:14px;color:#d9faff;font-size:14px;font-weight:800;">回り足 強い ↑</div>
          <div class="motor-position-axis motor-position-axis-x" style="position:absolute;right:14px;bottom:14px;color:#d9faff;font-size:14px;font-weight:800;">出足 強い →</div>
          <div class="motor-position-note" style="position:absolute;left:14px;bottom:14px;color:rgba(217,250,255,.78);font-size:12px;">円が大きいほど直線上位</div>
          ${boats.map((row) => {
            const x = row.dash.score == null ? 50 : Math.max(6, Math.min(94, row.dash.score));
            const y = row.turn.score == null ? 50 : Math.max(6, Math.min(94, 100 - row.turn.score));
            const size = row.straight.score == null ? 42 : Math.max(34, Math.min(72, 24 + (row.straight.score * 0.55)));
            const active = Number(row.boatNumber) === Number(currentBoatNumber) ? " is-active" : "";
            const ring = row.totalRank === 1 ? " is-top" : "";
            return `
              <button type="button" class="motor-position-bubble${active}${ring}" data-motor-position-boat="${esc(row.boatNumber)}" aria-label="${esc(row.boatNumber)}号艇のモーター履歴を表示" title="${esc(row.boatNumber)}号艇のモーター履歴" style="appearance:none;padding:0;color:inherit;font:inherit;cursor:pointer;position:absolute;z-index:2;transform:translate(-50%,-50%);left:${x}%;top:${y}%;width:${size}px;height:${size}px;border:3px solid ${laneColor(row.boatNumber)};border-radius:999px;display:flex;align-items:center;justify-content:center;background:rgba(8,17,29,.86);box-shadow:${Number(row.boatNumber) === Number(currentBoatNumber) ? '0 0 0 4px rgba(0,212,255,.16),0 10px 30px rgba(0,0,0,.42)' : row.totalRank === 1 ? '0 0 0 4px rgba(52,232,144,.18),0 10px 30px rgba(0,0,0,.42)' : '0 10px 30px rgba(0,0,0,.32)'};">
                <span class="motor-position-bubble-core lane-${esc(row.boatNumber)}" style="width:72%;height:72%;border-radius:999px;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:900;">${esc(row.boatNumber)}</span>
                <small style="position:absolute;left:50%;bottom:-24px;transform:translateX(-50%);padding:3px 8px;border-radius:999px;background:rgba(10,18,30,.92);border:1px solid rgba(0,212,255,.16);color:#d9faff;font-size:11px;font-weight:700;white-space:nowrap;">総合 ${esc(row.totalRank || "-")}位 / 6</small>
              </button>
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
        <td class="left"><div class="motor-history-racer-line"><span>${esc(r.racer_name || "")}</span>${racerClassBadge(r)}</div><div class="racer-meta">${esc(r.racer_number || "")}</div></td>
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
    </div>`;

  const renderRacerOnly = (data) => `
    <div class="racer-detail-panel motor-inspector-racer">
      ${renderRacerDetail(data)}
    </div>`;

  const fetchHistory = (raceId, boatNumber) => {
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

  const fetchRacerDetail = (raceId, boatNumber) => {
    const key = `${raceId}:${boatNumber}`;
    if (!racerDetailCache.has(key)) {
      racerDetailCache.set(key, fetch(`/api/race/${encodeURIComponent(raceId)}/racer-detail/${boatNumber}?v=${encodeURIComponent(staticVersion)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "no-store",
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

  const openMotorHistory = async (raceId, boatNumber, sourceButton = null) => {
    if (!inspectorShell || !inspectorBody || !raceId || !boatNumber) return;
    const requestedBoatNumber = String(boatNumber);
    if (
      activeRaceId === raceId
      && activeMotorBoatNumber === requestedBoatNumber
      && inspectorBody.querySelector(".motor-history-panel")
    ) return;

    const keepCurrentHistoryVisible = !inspectorShell.hidden
      && Boolean(inspectorBody.querySelector(".motor-history-panel"));
    const requestId = ++motorHistoryRequestId;
    activeRaceId = raceId;
    activeMotorBoatNumber = requestedBoatNumber;
    document.querySelectorAll(".motor-history-btn[aria-expanded='true']").forEach((el) => el.setAttribute("aria-expanded", "false"));
    sourceButton?.setAttribute("aria-expanded", "true");
    inspectorShell.hidden = false;
    inspectorBody.setAttribute("aria-busy", "true");
    if (!keepCurrentHistoryVisible) {
      inspectorBody.innerHTML = '<div class="motor-history-loading">モーター履歴を読み込み中...</div>';
    }
    try {
      const history = await fetchHistory(raceId, requestedBoatNumber);
      if (requestId !== motorHistoryRequestId) return;
      inspectorBody.innerHTML = renderHistoryOnly(history);
    } catch (err) {
      if (requestId !== motorHistoryRequestId) return;
      inspectorBody.innerHTML = `<div class="motor-history-empty">モーター履歴の取得に失敗しました: ${esc(err.message || "")}</div>`;
    } finally {
      if (requestId === motorHistoryRequestId) inspectorBody.removeAttribute("aria-busy");
    }
  };

  const openRacerDetail = async (raceId, boatNumber, sourceButton = null) => {
    if (!inspectorShell || !inspectorBody || !raceId || !boatNumber) return;
    document.querySelectorAll(".racer-detail-btn[aria-expanded='true']").forEach((el) => el.setAttribute("aria-expanded", "false"));
    sourceButton?.setAttribute("aria-expanded", "true");
    inspectorShell.hidden = false;
    inspectorBody.innerHTML = '<div class="motor-history-loading">選手詳細を読み込み中...</div>';
    try {
      inspectorBody.innerHTML = renderRacerOnly(await fetchRacerDetail(raceId, boatNumber));
      inspectorShell.scrollIntoView({ behavior: "auto", block: "start" });
    } catch (err) {
      inspectorBody.innerHTML = `<div class="motor-history-empty">選手情報の取得に失敗しました: ${esc(err.message || "")}</div>`;
    }
  };

  document.querySelectorAll(".motor-history-btn").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openMotorHistory(button.dataset.raceId, button.dataset.boatNumber, button);
    });
  });

  inspectorBody?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-motor-position-boat]");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    openMotorHistory(activeRaceId, button.dataset.motorPositionBoat);
  });

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
    const nicheContainer = document.getElementById("niche-signals-container");
    try {
      const res = await fetch(`/api/race/${encodeURIComponent(raceId)}/signals?v=${encodeURIComponent(staticVersion)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "force-cache",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (nicheContainer) nicheContainer.innerHTML = renderNicheSignals(data.niche_signals);
    } catch (err) {
      if (nicheContainer) {
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
