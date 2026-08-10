from pathlib import Path


def test_top_uses_lightweight_script_without_roi_runtime():
    source = Path("src/web/templates/index.html").read_text(encoding="utf-8")
    lightweight, full_runtime = source.split("    {% else %}\n    <script>", 1)

    assert "mode: 'top-lightweight'" in lightweight
    assert "renderTodaysPicks: { calls: 0, result: 'not_loaded_top_only' }" in lightweight
    assert "marketSignalsRequests: 0" in lightweight
    assert "runtimeErrors: []" in lightweight
    assert "dataset.topDiagnostics = JSON.stringify(diagnostics)" in lightweight
    assert "getEntriesByType?.('navigation')" in lightweight
    assert "domContentLoadedMs" in lightweight
    assert "window.setInterval(updateRaceState, 60000);" in lightweight
    assert "async function loadMarketSignals()" not in lightweight
    assert "renderTodaysPicks = function()" not in lightweight
    assert "async function loadMarketSignals()" in full_runtime
    assert "renderTodaysPicks = function()" in full_runtime
