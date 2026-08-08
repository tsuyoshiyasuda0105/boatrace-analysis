(() => {
  const root = document.querySelector("[data-start-prediction]");
  if (!root) return;
  window.__boatraceStartPredictionLoaded = true;

  const raceId = root.dataset.raceId;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const pct = (v) => `${(Number(v || 0) * 100).toFixed(1)}%`;
  const num = (v, d = 3) => (v == null || v === "" || Number.isNaN(Number(v)) ? "-" : Number(v).toFixed(d));
  const stText = (v) => {
    if (v == null || v === "" || Number.isNaN(Number(v))) return "-";
    return Number(v).toFixed(2).replace(/^0/, "");
  };
  const lane = (n) => `<span class="lane lane-${esc(n)}">${esc(n)}</span>`;

  const stageLabel = {
    pre_exhibition: "展示前",
    post_exhibition: "展示後",
  };
  const stageSub = {
    pre_exhibition: "選手・モーター・風向きから仮予測",
    post_exhibition: "展示進入・展示ST・展示タイムで補正",
  };

  const bestBoat = (prediction, key) => {
    const boats = prediction?.boats || [];
    if (!boats.length) return null;
    return boats.reduce((best, row) => (
      Number(row[key] || 0) > Number(best[key] || 0) ? row : best
    ), boats[0]);
  };

  const boatMap = (prediction) => Object.fromEntries((prediction?.boats || []).map((b) => [Number(b.boat_number), b]));
  const actualMap = (actual) => Object.fromEntries((actual?.results || []).map((r) => [Number(r.boat_number), r]));

  const boatOffset = (row, index) => {
    const rank = Number(row.predicted_start_rank || index + 1);
    const top = Number(row.start_top_probability || 0);
    const st = Number(row.predicted_st || 0.16);
    const raw = 66 - rank * 7 + top * 22 - Math.max(0, st - 0.12) * 120;
    return Math.max(16, Math.min(84, raw));
  };

  const ensureLiteStyles = () => {
    if (document.getElementById("start-lite-styles")) return;
    const style = document.createElement("style");
    style.id = "start-lite-styles";
    style.textContent = `
      .start-lite-shell{border:1px solid #15324b;border-radius:16px;background:linear-gradient(180deg,rgba(7,17,29,.92),rgba(10,20,34,.92));padding:16px;box-shadow:0 10px 28px rgba(0,0,0,.18)}
      .start-lite-head{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;margin-bottom:12px}
      .start-lite-head h3{margin:0;font-size:24px;line-height:1.2;color:#eef8ff}.start-lite-head p{margin:4px 0 0;font-size:12px;color:#8cacbf}
      .start-lite-actions{display:flex;gap:8px;flex-wrap:wrap}
      .start-lite-actions button{border:1px solid #2b6f90;background:linear-gradient(180deg,#36d7ff,#1fa8d9);color:#032031;font-weight:800;border-radius:10px;padding:9px 14px;cursor:pointer;box-shadow:0 6px 18px rgba(22,166,213,.25)}
      .start-lite-actions button:disabled{opacity:.6;cursor:wait}
      .start-lite-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
      .start-lite-card{border:1px solid #1f3b57;border-radius:14px;background:rgba(10,22,37,.85);padding:12px}
      .start-lite-card.is-empty{display:flex;flex-direction:column;justify-content:space-between;min-height:280px}
      .start-lite-title{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:8px}
      .start-lite-title strong{font-size:18px;color:#eff9ff}.start-lite-title b{font-size:12px;color:#62e7ff}.start-lite-title span{display:block;font-size:11px;color:#8ca9bd;margin-top:3px}
      .start-lite-board{border:1px solid #214360;border-radius:12px;background:rgba(7,15,25,.78);padding:10px 10px 8px;margin:10px 0}
      .start-lite-flow{position:relative;padding-left:12px}
      .start-lite-flow:before{content:"";position:absolute;left:6px;top:2px;bottom:2px;width:2px;background:rgba(214,229,240,.82);border-radius:999px}
      .start-lite-row{display:grid;grid-template-columns:26px minmax(0,108px) 46px;align-items:center;gap:6px;min-height:26px}
      .start-lite-row.is-top .start-lite-arrow{color:#66ecff;text-shadow:0 0 12px rgba(102,236,255,.35)}
      .start-lite-row .lane{width:22px;height:22px;font-size:12px;box-shadow:none}
      .start-lite-track{position:relative;height:12px}
      .start-lite-arrow{position:absolute;top:-6px;transform:translateX(-50%);font-size:17px;line-height:1;color:#dbeaf5;font-weight:900}
      .start-lite-st{text-align:right;font-size:11px;font-weight:800;color:#eff7ff}.start-lite-st small,.start-lite-st em{display:block;font-size:10px;font-style:normal;font-weight:700}.start-lite-st small{color:#7ad7ec}.start-lite-st em{color:#ffd778}
      .start-lite-legend{display:flex;flex-wrap:wrap;gap:8px 12px;margin-top:8px;font-size:10px;color:#8ca6b8}.start-lite-legend b{font-size:14px;color:#dbeaf5}
      .start-lite-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px}
      .start-lite-fact{border:1px solid #1a3147;border-radius:10px;background:rgba(9,16,27,.7);padding:8px}.start-lite-fact span{display:block;font-size:10px;color:#80a6ba;margin-bottom:4px}.start-lite-fact b{display:block;color:#eef8ff;font-size:12px}
      .start-lite-combos{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:5px}.start-lite-combos li{display:flex;justify-content:space-between;gap:10px;font-size:12px;color:#dbeaf5;border-bottom:1px solid rgba(255,255,255,.05);padding-bottom:3px}
      .start-lite-empty-note{font-size:12px;color:#8dacbf;line-height:1.7;margin-top:10px}
      .start-lite-actual-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:10px}
      .start-lite-table-wrap{overflow:auto;margin-top:10px;border:1px solid #1a3147;border-radius:12px}
      .start-lite-table{width:100%;border-collapse:collapse;font-size:12px;min-width:420px}.start-lite-table th,.start-lite-table td{padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.06);text-align:left}.start-lite-table th{font-size:10px;letter-spacing:.08em;color:#87a8bb;background:rgba(10,20,34,.95);text-transform:uppercase}.start-lite-table td{color:#ebf6ff;background:rgba(9,16,27,.7)}
      .start-lite-diff.is-good{color:#4ef1a8}.start-lite-diff.is-bad{color:#ff8f8f}.start-lite-diff.is-flat{color:#9fc5da}
      .start-prediction-empty{border:1px solid #18364d;border-radius:14px;padding:14px;background:rgba(8,18,30,.72)}.start-prediction-empty b{display:block;font-size:15px;color:#ecf6ff;margin-bottom:4px}.start-prediction-empty span{font-size:12px;color:#8caabc}
      .start-prediction-error{margin-bottom:10px;border:1px solid rgba(255,120,120,.3);background:rgba(90,12,20,.28);color:#ffd3d3;border-radius:10px;padding:10px 12px;font-size:12px}
      @media (max-width: 980px){.start-lite-grid{grid-template-columns:1fr}.start-lite-card.is-empty{min-height:auto}}
      @media (max-width: 640px){.start-lite-shell{padding:12px}.start-lite-head h3{font-size:20px}.start-lite-row{grid-template-columns:24px minmax(0,88px) 42px}.start-lite-arrow{font-size:15px;top:-5px}.start-lite-facts,.start-lite-actual-summary{grid-template-columns:1fr 1fr}}
    `;
    document.head.appendChild(style);
  };

  const renderArrowBoard = (prediction, actual) => {
    const boats = prediction?.boats?.length
      ? [...prediction.boats].sort((a, b) => Number(a.entry_course || a.course_number || a.boat_number) - Number(b.entry_course || b.course_number || b.boat_number))
      : [1, 2, 3, 4, 5, 6].map((boat) => ({ boat_number: boat }));
    const resultByBoat = actualMap(actual);
    return `<div class="start-lite-board"><div class="start-lite-flow">${boats.map((b, idx) => {
      const boat = Number(b.boat_number);
      const course = Number(b.entry_course || b.course_number || boat);
      const result = resultByBoat[boat];
      const offset = boatOffset(b, idx);
      const isTop = Number(b.predicted_start_rank || 9) === 1;
      const finish = result?.finishing_position ? `${result.finishing_position}着` : "";
      return `<div class="start-lite-row${isTop ? " is-top" : ""}"><div>${lane(course)}</div><div class="start-lite-track"><span class="start-lite-arrow" style="left:${offset}%">▲</span></div><div class="start-lite-st"><b>${stText(b.predicted_st)}</b><small>${pct(b.start_top_probability)}</small>${finish ? `<em>${esc(finish)}</em>` : ""}</div></div>`;
    }).join("")}</div><div class="start-lite-legend"><span><b>▲</b> 前に出るほど優位</span><span>数値は予測ST / STトップ確率</span></div></div>`;
  };

  const renderStageCard = (stage, prediction, actual) => {
    if (!prediction) {
      return `<article class="start-lite-card is-empty" data-stage-card="${stage}"><div><div class="start-lite-title"><div><strong>${esc(stageLabel[stage])}</strong><span>${esc(stageSub[stage])}</span></div><b>未生成</b></div><div class="start-lite-empty-note">この段階の予測はまだありません。必要なときだけ生成できます。</div></div><div class="start-lite-actions"><button type="button" data-generate-start="${stage}">${esc(stageLabel[stage])}生成</button></div></article>`;
    }
    const leader = bestBoat(prediction, "first_probability");
    const startTop = bestBoat(prediction, "start_top_probability");
    const top3 = (prediction.trifectas || []).slice(0, 3).map((x) => `<li><b>${esc(x.scenario_key || x.combination)}</b><span>${pct(x.probability)}</span></li>`).join("");
    return `<article class="start-lite-card" data-stage-card="${stage}"><div class="start-lite-title"><div><strong>${esc(stageLabel[stage])}</strong><span>${esc(stageSub[stage])}</span></div><b>信頼 ${pct(prediction.confidence)}</b></div>${renderArrowBoard(prediction, actual)}<div class="start-lite-facts"><div class="start-lite-fact"><span>STトップ</span><b>${startTop ? `${lane(startTop.boat_number)} ${pct(startTop.start_top_probability)}` : "-"}</b></div><div class="start-lite-fact"><span>1M先頭</span><b>${prediction.first_mark_boat ? `${lane(prediction.first_mark_boat)} ${pct(prediction.first_mark_probability)}` : "-"}</b></div><div class="start-lite-fact"><span>決まり手</span><b>${esc(prediction.predicted_kimarite || "-")}</b></div><div class="start-lite-fact"><span>1着期待</span><b>${leader ? `${lane(leader.boat_number)} ${pct(leader.first_probability)}` : "-"}</b></div></div><ol class="start-lite-combos">${top3}</ol></article>`;
  };

  const renderActualCard = (actual, postPrediction) => {
    if (!actual) {
      return `<article class="start-lite-card is-empty"><div><div class="start-lite-title"><div><strong>本番結果</strong><span>結果取得後に比較します</span></div><b>未確定</b></div><div class="start-lite-empty-note">本番終了後に、予測STと実際のST差、着順、決まり手を照合します。</div></div><div class="start-lite-actions"><button type="button" data-evaluate-start>結果照合</button></div></article>`;
    }
    const resultByBoat = actualMap(actual);
    const predictionByBoat = boatMap(postPrediction);
    const actualPrediction = { boats: [1, 2, 3, 4, 5, 6].map((boat) => {
      const result = resultByBoat[boat] || {};
      return {
        boat_number: boat,
        entry_course: result.course_number || boat,
        predicted_st: result.start_timing,
        predicted_start_rank: result.start_timing == null ? 9 : null,
        start_top_probability: boat === Number(actual.actual_start_top_boat) ? 1 : 0,
      };
    }).sort((a, b) => Number(a.boat_number) - Number(b.boat_number)) };
    actualPrediction.boats.filter((b) => b.predicted_st != null).sort((a, b) => Number(a.predicted_st) - Number(b.predicted_st)).forEach((b, i) => { b.predicted_start_rank = i + 1; });
    const rows = [1, 2, 3, 4, 5, 6].map((boat) => {
      const result = resultByBoat[boat] || {};
      const pred = predictionByBoat[boat] || {};
      const stDiff = result.start_timing == null || pred.predicted_st == null ? null : Number(result.start_timing) - Number(pred.predicted_st);
      const tone = stDiff == null ? "" : stDiff <= -0.02 ? " is-good" : stDiff >= 0.02 ? " is-bad" : " is-flat";
      return `<tr><td>${lane(boat)}</td><td>${result.finishing_position ? `${result.finishing_position}着` : "-"}</td><td>${num(result.start_timing, 2)}</td><td>${num(pred.predicted_st, 2)}</td><td class="start-lite-diff${tone}">${stDiff == null ? "-" : `${stDiff >= 0 ? "+" : ""}${stDiff.toFixed(3)}`}</td><td>${esc(result.kimarite || "-")}</td></tr>`;
    }).join("");
    return `<article class="start-lite-card"><div class="start-lite-title"><div><strong>本番結果</strong><span>予測と結果の差分を確認</span></div><b>${esc(actual.actual_combo || "結果")}</b></div>${renderArrowBoard(actualPrediction, actual)}<div class="start-lite-actual-summary"><div class="start-lite-fact"><span>1着</span><b>${actual.actual_first_boat ? lane(actual.actual_first_boat) : "-"}</b></div><div class="start-lite-fact"><span>STトップ</span><b>${actual.actual_start_top_boat ? lane(actual.actual_start_top_boat) : "-"}</b></div><div class="start-lite-fact"><span>決まり手</span><b>${esc(actual.actual_kimarite || "-")}</b></div></div><div class="start-lite-table-wrap"><table class="start-lite-table"><thead><tr><th>艇</th><th>着</th><th>本番ST</th><th>予測ST</th><th>差</th><th>決まり手</th></tr></thead><tbody>${rows}</tbody></table></div></article>`;
  };

  const renderComparison = (data) => {
    ensureLiteStyles();
    const pre = data.pre_exhibition;
    const post = data.post_exhibition;
    const actual = data.actual;
    root.innerHTML = `<div class="start-lite-shell"><div class="start-lite-head"><div><h3>スタート簡易比較</h3><p>矢印だけで位置差を比較する軽量表示です。</p></div><div class="start-lite-actions"><button type="button" data-load-start-timeline>比較更新</button><button type="button" data-generate-start="pre_exhibition">展示前生成</button><button type="button" data-generate-start="post_exhibition">展示後生成</button><button type="button" data-evaluate-start>結果照合</button></div></div><div class="start-lite-grid">${renderStageCard("pre_exhibition", pre, actual)}${renderStageCard("post_exhibition", post, actual)}${renderActualCard(actual, post || pre)}</div></div>`;
  };

  const renderIdleShell = (message = "表示は必要なときだけ読み込みます。展示前または展示後の予測を必要なときだけ生成できます。") => {
    ensureLiteStyles();
    root.innerHTML = `<div class="start-prediction-empty"><div><b>スタート比較は未生成です</b><span>${esc(message)}</span></div><div class="start-lite-actions" style="margin-top:10px"><button type="button" data-load-start-timeline>既存比較を表示</button><button type="button" data-generate-start="pre_exhibition">展示前生成</button><button type="button" data-generate-start="post_exhibition">展示後生成</button><button type="button" data-evaluate-start>結果照合</button></div></div>`;
  };

  const loadTimeline = async () => {
    const res = await fetch(`/api/predictions/races/${encodeURIComponent(raceId)}/timeline`, { credentials: "same-origin" });
    if (res.status === 404) {
      renderIdleShell("既存の比較はまだありません。必要な段階だけ生成できます。");
      return false;
    }
    if (!res.ok) throw new Error("timeline not found");
    renderComparison(await res.json());
    return true;
  };

  const setBusy = (button, text) => {
    if (!button) return;
    button.disabled = true;
    button.dataset.originalText = button.textContent;
    button.textContent = text;
  };
  const clearBusy = (button) => {
    if (!button) return;
    button.disabled = false;
    button.textContent = button.dataset.originalText || button.textContent;
  };

  root.addEventListener("click", async (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const loadButton = target?.closest("[data-load-start-timeline]");
    const generateButton = target?.closest("[data-generate-start]");
    const evaluateButton = target?.closest("[data-evaluate-start]");
    root.querySelectorAll(".start-prediction-error").forEach((node) => node.remove());
    try {
      if (loadButton) {
        setBusy(loadButton, "読込中...");
        await loadTimeline();
      }
      if (generateButton) {
        const stage = generateButton.dataset.generateStart || "post_exhibition";
        setBusy(generateButton, "生成中...");
        const res = await fetch(`/api/predictions/races/${encodeURIComponent(raceId)}`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stage }),
        });
        if (!res.ok) throw new Error("prediction generation failed");
        await loadTimeline();
      }
      if (evaluateButton) {
        setBusy(evaluateButton, "照合中...");
        const res = await fetch(`/api/predictions/races/${encodeURIComponent(raceId)}/evaluate`, {
          method: "POST",
          credentials: "same-origin",
        });
        if (!res.ok) throw new Error("prediction evaluation failed");
        await loadTimeline();
      }
    } catch (err) {
      const message = esc(err?.message || "スタート比較の取得に失敗しました");
      root.insertAdjacentHTML("afterbegin", `<div class="start-prediction-error">${message}</div>`);
    } finally {
      clearBusy(loadButton || generateButton || evaluateButton);
    }
  });

  renderIdleShell();
})();
