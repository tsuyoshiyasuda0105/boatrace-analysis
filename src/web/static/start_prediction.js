(() => {
  const root = document.querySelector("[data-start-prediction]");
  if (!root) return;
  const raceId = root.dataset.raceId;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const pct = (v) => `${(Number(v || 0) * 100).toFixed(1)}%`;
  const lane = (n) => `<span class="lane lane-${n}">${n}</span>`;

  const render = (p) => {
    const boats = p.boats || [];
    const rows = boats.map((b) => `<tr>
      <td>${lane(b.boat_number)}</td><td>${Number(b.predicted_st).toFixed(3)}</td>
      <td>${b.exhibition_st == null ? "-" : Number(b.exhibition_st).toFixed(2)}</td>
      <td>${b.historical_avg_st == null ? "-" : Number(b.historical_avg_st).toFixed(3)}</td>
      <td>${b.predicted_start_rank}位</td><td>${pct(b.start_top_probability)}</td>
      <td>${pct(b.first_probability)}</td><td>${pct(b.second_probability)}</td><td>${pct(b.third_probability)}</td>
    </tr>`).join("");
    const tri = (p.trifectas || []).slice(0, 10).map((x) => `<li><b>${esc(x.scenario_key)}</b><span>${pct(x.probability)}</span></li>`).join("");
    const reasons = (p.reasons || []).map((x) => `<li>${esc(x)}</li>`).join("");
    root.innerHTML = `<div class="start-prediction-head"><div><span class="start-eyebrow">START DEVELOPMENT</span><h3>スタート展開予測</h3></div><div class="start-confidence"><span>信頼度</span><b>${pct(p.confidence)}</b></div></div>
      <div class="start-scenario-strip"><div><span>攻め艇</span><b>${lane(p.primary_attack_boat)} ${esc(p.primary_attack_style)}</b></div><div><span>1マーク先頭</span><b>${lane(p.first_mark_boat)} ${pct(p.first_mark_probability)}</b></div><div><span>決まり手</span><b>${esc(p.predicted_kimarite)} ${pct(p.kimarite_probability)}</b></div></div>
      <div class="start-table-wrap"><table class="start-table"><thead><tr><th>艇</th><th>予測ST</th><th>展示ST</th><th>過去ST</th><th>順位</th><th>STトップ</th><th>1着</th><th>2着</th><th>3着</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="start-bottom-grid"><div><h4>3連単候補 Top10</h4><ol class="start-trifectas">${tri}</ol></div><div><h4>予測根拠</h4><ul class="start-reasons">${reasons}</ul><small>モデル ${esc(p.model_bundle_version)} / 確率表示であり結果を保証するものではありません。</small></div></div>`;
  };
  const missing = () => {
    root.innerHTML = `<div class="start-prediction-empty"><div><b>展開予測は未生成です</b><span>展示取得後に自動生成されます。必要な場合は手動生成できます。</span></div><button type="button" data-generate-start>生成</button></div>`;
    root.querySelector("[data-generate-start]")?.addEventListener("click", async (e) => {
      e.currentTarget.disabled = true; e.currentTarget.textContent = "生成中...";
      const res = await fetch(`/api/predictions/races/${encodeURIComponent(raceId)}`, {method:"POST", credentials:"same-origin", headers:{"Content-Type":"application/json"}, body:JSON.stringify({stage:"post_exhibition"})});
      if (res.ok) render(await res.json()); else root.textContent = "展開予測を生成できませんでした。";
    });
  };
  fetch(`/api/predictions/races/${encodeURIComponent(raceId)}?stage=post_exhibition`, {credentials:"same-origin"})
    .then(async (res) => res.ok ? render(await res.json()) : missing())
    .catch(missing);
})();
